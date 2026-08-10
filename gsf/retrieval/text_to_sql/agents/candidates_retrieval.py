# SPDX-FileCopyrightText: Copyright (c) 2026, NVIDIA CORPORATION & AFFILIATES.
# All rights reserved.
# SPDX-License-Identifier: Apache-2.0

"""
Candidate Retrieval Agent

Searches the semantic VDB per extracted entity, applies an LLM intent filter on
each entity's raw hits, and stores typed results in path_state.

Responsibilities:
- Search the semantic VDB (ontology_retriever) for ColumnAttribute candidates.
- Search the semantic VDB (semantic_retriever) for CustomAnalysis candidates.
- Search the semantic VDB for one Term hit using path_state["subject"].
- Filter each entity's hits by intent using the LLM (full question, not entity).
- Deduplicate across entities and store results in path_state.
"""

import logging

from concurrent.futures import ThreadPoolExecutor, as_completed
from typing import Any, Dict

from langchain_core.messages import SystemMessage

from nemo_retriever.tabular_data.ingestion.model.reserved_words import Labels

from gsf.semantic.constants import (
    LABEL_COLUMN_ATTRIBUTE,
    LABEL_SQL_ATTRIBUTE,
    LABEL_TERM,
)

from gsf.dal.attributes import (
    fetch_attr_column_contexts,
    fetch_column_attribute_fk_counts,
)
from gsf.retrieval.data_access.semantic_search import search_semantic_index
from gsf.utils.llm_invoke import invoke_with_structured_output
from gsf.retrieval.text_to_sql.base import BaseAgent
from gsf.retrieval.text_to_sql.models import (
    CandidateFilterModel,
    ColumnAttributeEvalModel,
    ColumnAttributeSpec,
    CombinedCandidateFilterModel,
    CustomAnalysisFilterModel,
)
from gsf.retrieval.text_to_sql.state import (
    AgentState,
    get_question_for_processing,
)

logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# Search / dedup helpers
# ---------------------------------------------------------------------------


def _search_by_label(
    retriever: object,
    entity: str,
    label: str,
    k: int,
    database_name: str | None = None,
) -> list[dict]:
    """Return up to *k* VDB hits for *label*."""
    try:
        return list(
            search_semantic_index(
                retriever,
                entity,
                label_filter=[label],
                per_label_k=k,
                database_name=database_name,
            )
        )
    except Exception:
        logger.warning("%s search failed for entity %r", label, entity, exc_info=True)
        return []


def _dedupe_best_score(hits: list[dict]) -> list[dict]:
    """Deduplicate by id, keeping the hit with the lowest score.

    When hits carry ``query_entity``, accumulate all entities that retrieved
    the same id into ``query_entities`` so per-entity coverage is preserved.
    """
    best: dict[str, dict] = {}
    entities_by_id: dict[str, set[str]] = {}
    for hit in hits:
        hid = hit.get("id")
        if hid is None:
            continue
        key = str(hid)
        qe = hit.get("query_entity")
        if qe:
            entities_by_id.setdefault(key, set()).add(str(qe))
        prev = best.get(key)
        if prev is None:
            best[key] = hit
    result: list[dict] = []
    for key, hit in best.items():
        out = dict(hit)
        ents = entities_by_id.get(key)
        if ents:
            out["query_entities"] = sorted(ents)
        result.append(out)
    return sorted(
        result,
        key=lambda h: float(h.get("score") or float("inf")),
    )


# ---------------------------------------------------------------------------
# LLM intent filter
# ---------------------------------------------------------------------------

_FILTER_PROMPT_TEMPLATE = """\
You are selecting the single best candidate that matches a user's question.

User question: {question}
Entity being searched: {entity}

Candidates:
{candidates_block}

Return the ID of the single best matching candidate.
Return null if none genuinely match the intent of the question.
"""


def _llm_filter(llm, question: str, entity: str, candidates: list[dict]) -> list[str]:
    """Use the LLM to pick the single best candidate by relevance to *question*.

    Each candidate must have an ``id`` field in its metadata.
    Returns a list with the single best ID, or all IDs on LLM failure.
    """
    if not candidates:
        return []

    all_ids = [str(c.get("id") or "") for c in candidates if c.get("id")]

    candidates_block = "\n".join(
        f"- id: {c.get('id')} | {c.get('text', '')}" for c in candidates if c.get("id")
    )

    messages = [
        SystemMessage(
            content=_FILTER_PROMPT_TEMPLATE.format(
                question=question,
                entity=entity,
                candidates_block=candidates_block,
            )
        )
    ]

    result = invoke_with_structured_output(llm, messages, CandidateFilterModel)
    if result is None or not result.best_id:
        return all_ids

    return [result.best_id]


_CANDIDATE_FILTER_PROMPT = """\
You are filtering candidate {candidate_type} for relevance to a user question.
Keep only candidates that could meaningfully contribute to answering the question.
Remove any that share no common domain, idea, or intent with the question.

User question: {question}

Candidates:
{candidates_block}

Return the list of IDs to KEEP. If none are relevant, return an empty list.
"""

_COMBINED_FILTER_PROMPT = """\
You are filtering two sets of candidates for relevance to a user question.
Keep only candidates that could meaningfully contribute to answering the question.
Remove any that share no common domain, idea, or intent with the question.

User question: {question}

Custom analyses:
{custom_block}

SQL attributes:
{sql_attr_block}

Return the IDs to KEEP for each set separately. Use empty lists if none are relevant.
"""


def _llm_filter_candidates(
    llm, question: str, candidates: list[dict], candidate_type: str
) -> list[dict]:
    """Keep only candidates relevant to *question* via LLM.

    Falls back to the original list on LLM failure.
    """
    if not candidates:
        return []

    candidates_block = "\n".join(
        f"- id: {c.get('id')} | {c.get('text', '')}" for c in candidates if c.get("id")
    )

    messages = [
        SystemMessage(
            content=_CANDIDATE_FILTER_PROMPT.format(
                candidate_type=candidate_type,
                question=question,
                candidates_block=candidates_block,
            )
        )
    ]

    result = invoke_with_structured_output(llm, messages, CustomAnalysisFilterModel)
    if result is None:
        return candidates

    kept_ids = set(result.kept_ids)
    filtered = [c for c in candidates if str(c.get("id") or "") in kept_ids]
    logger.debug(
        "%s filter: %d → %d (kept ids: %s)",
        candidate_type,
        len(candidates),
        len(filtered),
        kept_ids,
    )
    return filtered


def _llm_filter_both(
    llm,
    question: str,
    custom_hits: list[dict],
    sql_attr_hits: list[dict],
) -> tuple[list[dict], list[dict]]:
    """Filter custom analyses and SQL attributes in a single LLM call.

    Falls back to the original lists on LLM failure.
    """
    if not custom_hits and not sql_attr_hits:
        return [], []

    def _fmt(candidates: list[dict]) -> str:
        lines = [
            f"- id: {c.get('id')} | {c.get('text', '')}"
            for c in candidates
            if c.get("id")
        ]
        return "\n".join(lines) if lines else "(none)"

    messages = [
        SystemMessage(
            content=_COMBINED_FILTER_PROMPT.format(
                question=question,
                custom_block=_fmt(custom_hits),
                sql_attr_block=_fmt(sql_attr_hits),
            )
        )
    ]

    result = invoke_with_structured_output(llm, messages, CombinedCandidateFilterModel)
    if result is None:
        return custom_hits, sql_attr_hits

    kept_custom = set(result.custom_analysis_ids)
    kept_sql = set(result.sql_attribute_ids)
    filtered_custom = [c for c in custom_hits if str(c.get("id") or "") in kept_custom]
    filtered_sql = [c for c in sql_attr_hits if str(c.get("id") or "") in kept_sql]

    logger.debug(
        "combined filter: custom %d→%d, sql_attr %d→%d",
        len(custom_hits),
        len(filtered_custom),
        len(sql_attr_hits),
        len(filtered_sql),
    )
    return filtered_custom, filtered_sql


# ---------------------------------------------------------------------------
# ColumnAttribute SQL-relevance evaluator
# ---------------------------------------------------------------------------

_COL_ATTR_EVAL_PROMPT = """\
You are evaluating ColumnAttribute candidates for their usefulness in constructing \
a SQL query that answers a user's question.

A ColumnAttribute is a named semantic concept mapped to a specific table column — \
for example, "Total Due" maps to salesorderheader.TotalDue.

User question: {question}

Candidates:
{candidates_block}

Think step by step about what data is needed to answer this question with SQL:
- What value, metric, or measure is being asked for? Which candidate column \
computes or stores it?
- What is the primary entity (table) being queried?
- Are there filters, time periods, or groupings implied? Which candidates support them?

Important: semantic text similarity is NOT a reliable signal here. A candidate \
whose name differs from the question's wording may still be the correct column \
for computing the requested metric — reason about what data is actually needed.

Return the IDs of ALL candidates that could meaningfully contribute to the SQL answer \
— as a selected value, a filter, a GROUP BY dimension, or a join key. Be inclusive: \
only exclude candidates that are clearly from a completely unrelated domain.
"""


def _llm_evaluate_col_attr_candidates(
    llm: object,
    question: str,
    candidates: list[dict],
    attr_contexts: dict[str, dict],
) -> list[dict]:
    """Evaluate ColumnAttribute candidates for SQL-construction relevance via the LLM.

    Unlike VDB similarity, this applies SQL domain reasoning to identify which
    candidates are actually needed to compute the answer — even when surface-level
    text similarity between the question and a candidate is low (e.g. "Total Due"
    for "annual total sales").

    Falls back to the full candidate list on LLM failure or empty response.
    """
    if not candidates:
        return candidates

    all_ids = {str(c.get("id") or "") for c in candidates if c.get("id")}

    lines: list[str] = []
    for c in candidates:
        cid = str(c.get("id") or "")
        if not cid:
            continue
        ctx = attr_contexts.get(cid, {})
        attr_name = (
            ctx.get("attr_name") or c.get("name") or (c.get("text") or "").split(":")[0]
        )
        table = ctx.get("table_name") or ""
        schema = ctx.get("schema_name") or ""
        col = ctx.get("col_name") or ""
        desc = ctx.get("attr_description") or ctx.get("column_description") or ""

        if schema and table and col:
            location = f"{schema}.{table}.{col}"
        elif table and col:
            location = f"{table}.{col}"
        else:
            location = ""

        entry = f"- id: {cid} | {attr_name}"
        if location:
            entry += f" ({location})"
        if desc:
            entry += f": {desc}"
        lines.append(entry)

    if not lines:
        return candidates

    messages = [
        SystemMessage(
            content=_COL_ATTR_EVAL_PROMPT.format(
                question=question,
                candidates_block="\n".join(lines),
            )
        )
    ]

    result = invoke_with_structured_output(llm, messages, ColumnAttributeEvalModel)
    if result is None:
        return candidates

    kept_ids = set(result.relevant_ids) & all_ids
    if not kept_ids:
        logger.warning(
            "LLM col-attr eval returned no kept IDs — retaining all candidates"
        )
        return candidates

    filtered = [c for c in candidates if str(c.get("id") or "") in kept_ids]
    logger.info(
        "LLM col-attr eval: %d → %d candidates (kept: %s)",
        len(candidates),
        len(filtered),
        kept_ids,
    )
    return filtered if filtered else candidates


# ---------------------------------------------------------------------------
# ColumnAttributeSpec builder
# ---------------------------------------------------------------------------


def _build_column_attribute_spec(hit: dict) -> ColumnAttributeSpec | None:
    """Build a :class:`ColumnAttributeSpec` from a raw VDB hit dict.

    Expects the hit (or its ``metadata`` sub-dict) to contain ``name`` and
    ``source_column``. Returns ``None`` when required fields are absent.
    """
    # The hit may carry fields directly or nested under ``metadata``.
    meta: dict = hit.get("metadata") or {}
    if isinstance(meta, str):
        import json as _json

        try:
            meta = _json.loads(meta)
        except Exception:
            meta = {}

    def _get(key: str) -> Any:
        return hit.get(key) or meta.get(key)

    name = _get("name") or (hit.get("text") or "").strip() or None
    source_column = _get("source_column")

    if not name or not source_column:
        return None

    return ColumnAttributeSpec(
        name=name,
        source_column=source_column,
        display_name=_get("display_name") or "",
        datatype=_get("datatype") or "",
        description=_get("description"),
    )


# ---------------------------------------------------------------------------
# Agent
# ---------------------------------------------------------------------------


class CandidateRetrievalAgent(BaseAgent):
    """Retrieve ColumnAttribute, CustomAnalysis, SqlAttribute, and subject Term candidates.

    - ColumnAttributes: searched per entity from the semantic VDB (top-12 each).
    - CustomAnalysis: searched once with the full question from the semantic VDB.
    - SqlAttribute: searched once with the full question from the semantic VDB.
    - Subject Term: searched once with ``path_state["subject"]`` (top-1 hit).

    Deduplicate across entities and store:
    - ``path_state["retrieved_column_attributes"]``: ``list[dict]``
      (each ColumnAttribute hit may include ``query_entity`` /
      ``query_entities`` naming the extraction string(s) that retrieved it)
    - ``path_state["retrieved_custom_analyses"]``:   ``list[dict]``
    - ``path_state["retrieved_sql_attributes"]``:    ``list[dict]``
    - ``path_state["retrieved_subject_term"]``:      ``dict | None``
    """

    def __init__(self):
        super().__init__("candidate_retrieval")

    def validate_input(self, state: AgentState) -> bool:
        question = get_question_for_processing(state)
        if not question:
            self.logger.warning("No question available for retrieval")
            return False
        return True

    def execute(self, state: AgentState) -> Dict[str, Any]:
        path_state = state.get("path_state", {})
        question = get_question_for_processing(state)
        entities: list[str] = path_state.get("entities") or []
        subject = (path_state.get("subject") or "").strip()
        llm = state["llm"]
        semantic_retriever = state.get("semantic_retriever")
        target_db = path_state.get("target_db")

        all_col_attr_hits: list[dict] = []
        all_custom_hits: list[dict] = []
        all_sql_attr_hits: list[dict] = []
        subject_term_hits: list[dict] = []

        if semantic_retriever is not None:
            clean_entities = [e.strip() for e in entities if (e or "").strip()]

            search_tasks: list[tuple[str, Any]] = [
                (
                    "custom",
                    (
                        semantic_retriever,
                        question,
                        Labels.CUSTOM_ANALYSIS,
                        3,
                        target_db,
                    ),
                ),
                *[
                    (
                        f"col_attr:{entity}",
                        (
                            semantic_retriever,
                            entity,
                            LABEL_COLUMN_ATTRIBUTE,
                            12,
                            target_db,
                        ),
                    )
                    for entity in clean_entities
                ],
            ]
            if subject:
                search_tasks.append(
                    (
                        "subject_term",
                        (
                            semantic_retriever,
                            subject,
                            LABEL_TERM,
                            1,
                            target_db,
                        ),
                    )
                )

            with ThreadPoolExecutor(max_workers=len(search_tasks) or 1) as pool:
                futures = {
                    pool.submit(_search_by_label, *args): key
                    for key, args in search_tasks
                }
                for future in as_completed(futures):
                    key = futures[future]
                    result = future.result()
                    if key == "custom":
                        all_custom_hits = result
                    elif key == "sql_attr":
                        all_sql_attr_hits = result
                    elif key == "subject_term":
                        subject_term_hits = result
                    else:
                        # key is "col_attr:{entity}" — tag each hit for coverage.
                        entity = key.split(":", 1)[1]
                        for hit in result:
                            tagged = dict(hit)
                            tagged["query_entity"] = entity
                            all_col_attr_hits.append(tagged)

        # Annotate each ColumnAttribute hit with its incoming SEMANTIC_FK count so
        # that join-central candidates are ranked higher than VDB-score alone.
        if all_col_attr_hits:
            col_attr_ids = [str(h.get("id")) for h in all_col_attr_hits if h.get("id")]
            fk_counts = fetch_column_attribute_fk_counts(col_attr_ids)
            for hit in all_col_attr_hits:
                hit["fk_count"] = fk_counts.get(str(hit.get("id") or ""), 0)

        deduped_col_attr = _dedupe_best_score(all_col_attr_hits)
        deduped_custom = _dedupe_best_score(all_custom_hits)
        deduped_sql_attr = _dedupe_best_score(all_sql_attr_hits)
        subject_term = subject_term_hits[0] if subject_term_hits else None

        # Fetch Neo4j context (table names, descriptions) for the deduped candidates
        # and evaluate them by SQL-construction relevance via the LLM.  This catches
        # candidates that score low on text similarity but are the correct column for
        # computing the requested metric (e.g. "Total Due" for "annual total sales").
        # The contexts are cached in path_state to avoid a duplicate Neo4j call in
        # candidates_preparation.
        col_attr_contexts: dict[str, dict] = {}
        if deduped_col_attr:
            deduped_ids = [
                str(h.get("id") or "") for h in deduped_col_attr if h.get("id")
            ]
            col_attr_contexts = fetch_attr_column_contexts(deduped_ids)
            deduped_col_attr = _llm_evaluate_col_attr_candidates(
                llm, question, deduped_col_attr, col_attr_contexts
            )

        # Filter CustomAnalysis candidates after deduplication so the LLM
        # evaluates each unique candidate only once.
        deduped_custom, deduped_sql_attr = _llm_filter_both(
            llm, question, deduped_custom, deduped_sql_attr
        )

        path_state["retrieved_column_attributes"] = deduped_col_attr
        path_state["retrieved_custom_analyses"] = deduped_custom
        path_state["retrieved_sql_attributes"] = deduped_sql_attr
        path_state["col_attr_contexts"] = col_attr_contexts
        path_state["retrieved_subject_term"] = subject_term

        self.logger.info(
            "Retrieved %d ColumnAttributes, %d CustomAnalysis, "
            "%d SqlAttribute candidates, and subject Term %s "
            "(%d entities queried, subject=%r)",
            len(deduped_col_attr),
            len(deduped_custom),
            len(deduped_sql_attr),
            (
                f"id={subject_term.get('id')!r} score={subject_term.get('score')}"
                if subject_term
                else "None"
            ),
            len(entities),
            subject,
        )

        return {"path_state": path_state}
