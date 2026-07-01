# SPDX-FileCopyrightText: Copyright (c) 2026, NVIDIA CORPORATION & AFFILIATES.
# All rights reserved.
# SPDX-License-Identifier: Apache-2.0

"""Automatic SqlAttribute suggestion from SQL query history.

After the semantic FK pass this module runs once per compilation:

1. For every Term, fetch the SQL queries linked to its tables.
2. Extract SQL expressions (filters, aggregations, functions) from each query.
3. Score each expression by summing the 3-month usage counters of the queries
   it appears in.
4. Send the top 10 expressions per term to an LLM that judges whether any
   should become named SqlAttributes.
5. Persist new suggestions in Neo4j (skipping existing semantic SqlAttributes)
   and embed only the newly written nodes into the semantic VDB.
"""

from __future__ import annotations

import logging
import re
import time
from collections import defaultdict
from concurrent.futures import ThreadPoolExecutor, as_completed
from threading import Lock
from typing import TYPE_CHECKING, Any

from langchain_core.messages import HumanMessage, SystemMessage
from pydantic import BaseModel

from gsf.dal.sql_attributes import (
    fetch_suggested_sql_attribute_docs,
    merge_suggested_sql_attribute,
)
from gsf.dal.terms import fetch_terms_with_sqls
from gsf.semantic.constants import SEMANTIC_SOURCE
from gsf.utils.llm_invoke import get_llm_client, invoke_with_structured_output

if TYPE_CHECKING:
    from nemo_retriever.common.params.models import EmbedParams
    from nemo_retriever.common.vdb.adt_vdb import VDB

logger = logging.getLogger(__name__)

_EMBED_RETRIES = 3
_EMBED_RETRY_DELAY = 5.0  # seconds between retries
_TOP_N = 10
_TERM_WORKERS = 4
_COUNTER_RE = re.compile(r"^count_monthly_(\d{4})_(\d{2})$")


# ---------------------------------------------------------------------------
# LLM output schema
# ---------------------------------------------------------------------------


class _SuggestedAttr(BaseModel):
    name: str
    description: str
    expression: str


class _Suggestions(BaseModel):
    suggestions: list[_SuggestedAttr]


# ---------------------------------------------------------------------------
# Usage scoring
# ---------------------------------------------------------------------------


def _latest_3month_score(props: dict[str, Any]) -> float:
    """Return the sum of the three most recent monthly counters in *props*."""
    monthly: list[tuple[tuple[int, int], float]] = []
    for key, val in props.items():
        m = _COUNTER_RE.match(key)
        if m and val is not None:
            try:
                monthly.append(((int(m.group(1)), int(m.group(2))), float(val)))
            except (TypeError, ValueError):
                pass
    monthly.sort(key=lambda x: x[0], reverse=True)
    return sum(v for _, v in monthly[:3])


# ---------------------------------------------------------------------------
# Expression extraction via sqlglot
# ---------------------------------------------------------------------------

_BARE_KEYWORDS = frozenset(
    {"select", "where", "from", "join", "on", "union", "intersect", "except", "having"}
)


def _extract_expressions(sql_text: str) -> list[str]:
    """Parse *sql_text* and return deduplicated SQL sub-expressions.

    Extracted categories:
    - WHERE / JOIN ON predicates (comparisons with at least one non-column operand)
    - Aggregations, anonymous functions, CASE expressions
    - HAVING predicates
    - Full JOIN ON predicate (when it contains non-FK conditions)
    - Whole UNION / INTERSECT / EXCEPT expression and each individual branch

    Excluded:
    - Bare column references, star, plain literals
    - Pure FK equalities (col_a = col_b, both sides are columns with no literal)
    - Degenerate sqlglot emissions that are just a SQL keyword token
    """
    try:
        import sqlglot
        import sqlglot.expressions as exp

        ast = sqlglot.parse_one(sql_text, error_level=sqlglot.ErrorLevel.IGNORE)
        if ast is None:
            return []
    except Exception:
        return []

    seen: set[str] = set()
    results: list[str] = []

    cmp_types = (
        exp.EQ,
        exp.NEQ,
        exp.GT,
        exp.GTE,
        exp.LT,
        exp.LTE,
        exp.In,
        exp.Between,
        exp.Like,
        exp.Is,
    )

    def _is_pure_fk_eq(node: exp.Expression) -> bool:
        """Return True for bare col = col equalities (no literals on either side)."""
        return (
            isinstance(node, exp.EQ)
            and isinstance(node.left, exp.Column)
            and isinstance(node.right, exp.Column)
        )

    def _add(node: exp.Expression) -> None:
        text = node.sql().strip()
        key = text.lower()
        if len(text) < 5 or key in seen:
            return
        if isinstance(node, (exp.Column, exp.Star, exp.Literal)):
            return
        if key in _BARE_KEYWORDS:
            return
        if _is_pure_fk_eq(node):
            return
        seen.add(key)
        results.append(text)

    # WHERE predicates — each individual comparison condition
    where = ast.find(exp.Where)
    if where:
        for node in where.walk():
            if isinstance(node, cmp_types):
                _add(node)

    # Aggregations, anonymous functions, CASE anywhere in the query
    for node in ast.walk():
        if isinstance(node, (exp.AggFunc, exp.Anonymous, exp.Case)):
            _add(node)

    # HAVING expression
    having = ast.find(exp.Having)
    if having and having.this:
        _add(having.this)

    # JOIN ON conditions — capture the full ON predicate and each sub-predicate.
    # These encode business rules beyond simple FK equality.
    for join in ast.find_all(exp.Join):
        on = join.args.get("on")
        if on:
            _add(on)
            for node in on.walk():
                if isinstance(node, cmp_types):
                    _add(node)

    # UNION / INTERSECT / EXCEPT — capture the whole set-operation and each branch.
    for set_op in ast.find_all(exp.Union, exp.Intersect, exp.Except):
        _add(set_op)
        for branch in (set_op.left, set_op.right):
            if branch is not None:
                _add(branch)

    return results


# ---------------------------------------------------------------------------
# Score and rank across all SQLs for a term
# ---------------------------------------------------------------------------


def _rank_expressions(sqls: list[dict[str, Any]]) -> list[tuple[str, float]]:
    """Return (expression, score) pairs sorted by score descending."""
    scores: dict[str, float] = defaultdict(float)
    for item in sqls:
        sql_text = item.get("sql_text") or ""
        sql_score = _latest_3month_score(item.get("props") or {})
        if not sql_text:
            continue
        for expr in _extract_expressions(sql_text):
            scores[expr] += sql_score
    return sorted(scores.items(), key=lambda x: x[1], reverse=True)


# ---------------------------------------------------------------------------
# LLM judge
# ---------------------------------------------------------------------------


def _judge_with_llm(
    term_name: str,
    term_description: str,
    expressions: list[tuple[str, float]],
) -> list[_SuggestedAttr]:
    """Ask the LLM whether any of *expressions* should become SqlAttributes."""
    if not expressions:
        return []

    expr_block = "\n".join(
        f"{i + 1}. [{score:.0f} uses] {expr}"
        for i, (expr, score) in enumerate(expressions)
    )

    llm = get_llm_client(temperature=0.0)
    messages = [
        SystemMessage(
            content=(
                "You are an expert data engineer reviewing SQL expressions to decide "
                "which ones represent reusable business logic worth naming. "
                "A SqlAttribute is a named, reusable SQL expression — a filter, "
                "aggregation, or calculation — that captures a clear business concept "
                "and is specific enough to be useful while general enough to recur."
            )
        ),
        HumanMessage(
            content=(
                f"Business term: {term_name}\n"
                f"Description: {term_description or '(none)'}\n\n"
                f"Top SQL expressions extracted from queries on tables for this term "
                f"(ranked by 3-month execution count):\n\n"
                f"{expr_block}\n\n"
                f"Which expressions should become a named SqlAttribute for the term "
                f"'{term_name}'?\n\n"
                "Rules you MUST follow:\n"
                "  1. ALWAYS mark an expression as a SqlAttribute if it compares a column "
                "against a literal value (e.g. status = 'active', amount > 1000, "
                "type IN ('A','B')). Literal values encode business thresholds and "
                "categories that are exactly what SqlAttributes are designed to capture. "
                "Exception: do NOT promote an expression whose only literal is an integer "
                "that looks like a surrogate or primary-key identifier "
                "(e.g. request_id = 50000, id = 12). Such values are instance-specific "
                "record lookups, not reusable business logic. Only promote an expression "
                "containing such an integer if the surrounding context also encodes a "
                "reusable business rule (e.g. the same WHERE clause additionally filters "
                "on a status, category, or date range).\n"
                "  2. NEVER mark an expression as a SqlAttribute if it only references "
                "columns from the same table with no literal value, constant, or "
                "cross-table condition (e.g. start_date < end_date). Such expressions "
                "describe structural integrity, not reusable business logic.\n"
                "  4. NEVER mark an IS NULL / IS NOT NULL check on a surrogate or "
                "primary-key column (any column named 'id' or ending in '_id') as a "
                "SqlAttribute. These are LEFT JOIN absence checks — structural plumbing "
                "that detects whether a join matched, not a business concept "
                "(e.g. request_task_collaborators.id IS NULL). "
                "Exception: IS NULL on non-key columns that carry business meaning IS "
                "valid (e.g. deleted_at IS NULL, approved_by IS NULL).\n"
                "  3. ALWAYS mark a JOIN ON condition or a UNION/INTERSECT/EXCEPT branch "
                "as a SqlAttribute when it appears frequently AND any of the following "
                "is true:\n"
                "     a. It encodes obvious business logic — "
                "e.g. a join that filters to active records, a union arm that defines a "
                "named sub-population, or a cross-table condition capturing a business "
                "relationship.\n"
                "     b. It is used to define a meaningful business alias — e.g. a SELECT "
                "branch or subquery whose result column is aliased with a descriptive "
                "business name (e.g. 'AS active_assignees', 'AS overdue_tasks'). "
                "The alias itself signals that the expression represents a named concept "
                "worth capturing.\n"
                "     c. A UNION combines two or more SELECT branches that each use a "
                "different join path to reach the same entity type (e.g. one branch joins "
                "via a direct ownership FK, another via a membership/collaborator table). "
                "This pattern structurally defines a named aggregate concept — all members "
                "of a group regardless of their role. Promote the whole UNION as a "
                "SqlAttribute representing that concept (e.g. 'Task Participants', "
                "'Order Stakeholders'). When naming and describing it, ignore any "
                "instance-specific WHERE predicates (like record-ID literals) and focus "
                "on the reusable structural pattern.\n"
                "     Ignore pure FK-only joins and UNION branches that select from a "
                "single table with no join or filter.\n\n"
                "For each chosen expression provide:\n"
                "  • name — user-friendly Title Case label with spaces between words, "
                "matching the ColumnAttribute naming style "
                "(e.g. 'Active Customer Filter', 'Revenue Above Threshold', "
                "'Last 90 Days Activity')\n"
                "  • description — what business logic it captures, why it matters for "
                "this term, and when a data consumer would apply it\n"
                "  • expression — the exact SQL expression copied verbatim\n\n"
                "Return an empty list if none qualify."
            )
        ),
    ]

    result = invoke_with_structured_output(llm, messages, _Suggestions)
    return result.suggestions if result else []


# ---------------------------------------------------------------------------
# Embedding
# ---------------------------------------------------------------------------


def _embed_new_attrs(
    attr_ids: list[str],
    embed_params: "EmbedParams",
    vdb: "VDB",
    database_name: str,
) -> None:
    import pandas as pd

    from nemo_retriever.models.inference.runtime import embed_text_main_text_embed
    from nemo_retriever.operators.vdb import IngestVdbOperator

    docs = fetch_suggested_sql_attribute_docs(attr_ids)
    if not docs:
        return

    rows = []
    for item in docs:
        node_id = item.get("id")
        path = f"neo4j:{node_id}" if node_id is not None else "neo4j:unknown"
        tabular_fields = {
            "id": node_id,
            "label": item.get("label", ""),
            "name": item.get("name", ""),
            "source_path": path,
            "database_name": database_name,
        }
        rows.append(
            {
                "text": (item.get("text") or "").strip(),
                "_embed_modality": "text",
                "path": path,
                "page_number": -1,
                "metadata": {
                    **tabular_fields,
                    "content_metadata": dict(tabular_fields),
                },
            }
        )

    before = time.time()
    embedded = None
    for attempt in range(1, _EMBED_RETRIES + 1):
        try:
            embedded = embed_text_main_text_embed(
                pd.DataFrame(rows),
                model_name=embed_params.model_name,
                embed_invoke_url=embed_params.embed_invoke_url,
                api_key=embed_params.api_key,
                embed_modality=embed_params.embed_modality,
            )
            break
        except Exception as exc:
            if attempt < _EMBED_RETRIES:
                logger.warning(
                    "Embedding attempt %d/%d failed (%s) — retrying in %.0fs.",
                    attempt,
                    _EMBED_RETRIES,
                    exc,
                    _EMBED_RETRY_DELAY,
                )
                time.sleep(_EMBED_RETRY_DELAY)
            else:
                logger.warning(
                    "Embedding failed after %d attempts (%s). "
                    "SqlAttributes were written to Neo4j but not embedded — "
                    "re-run compilation to embed them.",
                    _EMBED_RETRIES,
                    exc,
                )
                return

    if embedded is None:
        return

    with_embeddings = [
        r
        for r in embedded.to_dict(orient="records")
        if (r.get("metadata") or {}).get("embedding")
    ]
    if not with_embeddings:
        logger.warning(
            "Embedding step produced 0/%d SqlAttribute rows with embeddings — "
            "no rows were ingested into the VDB.",
            len(embedded),
        )
        return
    IngestVdbOperator(vdb=vdb)(with_embeddings)
    logger.info(
        "Embedded %d/%d new SqlAttribute(s) in %.2fs.",
        len(with_embeddings),
        len(embedded),
        time.time() - before,
    )


# ---------------------------------------------------------------------------
# Public entry point
# ---------------------------------------------------------------------------


def suggest_sql_attributes(database_name: str) -> int:
    """Suggest and persist SqlAttributes from query history for every Term.

    For each Term:
      1. Collects SQL queries from connected tables.
      2. Extracts and scores expressions by 3-month usage.
      3. Sends the top 10 to the LLM for SqlAttribute judgment.
      4. Writes newly approved suggestions to Neo4j (existing ones are skipped).
      5. Embeds only the newly created nodes into the semantic VDB.

    Returns the total number of new SqlAttribute nodes written.
    """
    from gsf.utils import get_embed_params
    from gsf.vdb import get_semantic_vdb

    logger.info("Collecting SQL expressions per term…")
    term_rows = fetch_terms_with_sqls(SEMANTIC_SOURCE)

    if not term_rows:
        logger.info(
            "No terms with associated SQL queries — skipping SqlAttribute suggestion."
        )
        return 0

    logger.info("Found %d term(s) with SQL queries.", len(term_rows))

    embed_params = get_embed_params()
    vdb = get_semantic_vdb()
    new_attr_ids: list[str] = []
    # Shared across workers — guards seen_expressions to prevent duplicate
    # SqlAttribute nodes when terms share the same underlying table.
    seen_expressions: set[str] = set()
    seen_lock = Lock()

    def _process_term(row: dict[str, Any]) -> list[str]:
        term_id: str = row["term_id"]
        term_name: str = row["term_name"]
        term_description: str = row.get("term_description") or ""
        sqls: list[dict] = row.get("sqls") or []

        ranked = _rank_expressions(sqls)
        top = ranked[:_TOP_N]
        if not top:
            logger.debug("Term %r: no scoreable expressions — skipping.", term_name)
            return []

        logger.info(
            "Term %r: %d SQL(s), %d unique expression(s) — sending top %d to LLM.",
            term_name,
            len(sqls),
            len(ranked),
            len(top),
        )
        suggestions = _judge_with_llm(term_name, term_description, top)

        if not suggestions:
            logger.info("Term %r: LLM suggested 0 SqlAttributes.", term_name)
            return []

        logger.info(
            "Term %r: LLM suggested %d SqlAttribute(s).", term_name, len(suggestions)
        )
        created: list[str] = []
        for s in suggestions:
            expr_key = s.expression.strip().lower()
            with seen_lock:
                if expr_key in seen_expressions:
                    logger.debug(
                        "SqlAttribute expression already handled this run — "
                        "skipping duplicate for term %r: %r",
                        term_name,
                        s.expression,
                    )
                    continue
                seen_expressions.add(expr_key)

            attr_id = merge_suggested_sql_attribute(
                name=s.name,
                description=s.description,
                expression=s.expression,
                term_id=term_id,
            )
            if attr_id is None:
                logger.debug("SqlAttribute %r already exists — skipping.", s.name)
            else:
                logger.info("Created SqlAttribute %r (id=%s).", s.name, attr_id)
                created.append(attr_id)
        return created

    with ThreadPoolExecutor(max_workers=_TERM_WORKERS) as pool:
        futures = {pool.submit(_process_term, row): row for row in term_rows}
        for future in as_completed(futures):
            try:
                new_attr_ids.extend(future.result())
            except Exception:
                row = futures[future]
                logger.exception("Error processing term %r", row.get("term_name"))

    total = len(new_attr_ids)
    if new_attr_ids:
        logger.info("Embedding %d new SqlAttribute(s)…", total)
        _embed_new_attrs(new_attr_ids, embed_params, vdb, database_name)

    logger.info(
        "SqlAttribute suggestion pass complete — %d new node(s) written.", total
    )
    return total
