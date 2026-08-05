# SPDX-FileCopyrightText: Copyright (c) 2026, NVIDIA CORPORATION & AFFILIATES.
# All rights reserved.
# SPDX-License-Identifier: Apache-2.0

"""
SQL generation from semantic retrieval context.

Builds SQL from graph-backed semantic candidates (custom analyses, columns),
prepared tables, and optional file extraction — not from ad-hoc “snippet”
assembly alone.

Responsibilities:
- Construct SQL using semantic candidates and schema context from CandidatePreparationAgent
- Handle file extraction results (data_for_sql) when present
- Incorporate similar questions from conversation history
- Handle feedback scenarios
- Store SQL response with custom analyses in path_state

Design Decisions:
- Primary path: vector/semantic retrieval + preparation, then LLM SQL synthesis
- Supports text-style answers when the model returns prose instead of SQL
- Optional extracted file data from upstream file steps
"""

import logging
import os
import re
import random
import time
from concurrent.futures import ThreadPoolExecutor, as_completed
from typing import Any, Dict
from langchain_core.messages import AIMessage, HumanMessage, SystemMessage

from gsf.utils.llm_invoke import get_llm_client, safe_invoke_with_structured_output
from gsf.retrieval.text_to_sql.base import BaseAgent
from gsf.retrieval.text_to_sql.agents.sql_execution import _run_sql
from gsf.retrieval.text_to_sql.connector_routing import resolve_connector_from_tables
from gsf.retrieval.data_access.custom_analyses import (
    build_custom_analyses_section,
    get_custom_analyses_ids,
)
from gsf.retrieval.text_to_sql.state import (
    AgentState,
    get_original_question,
    get_question_for_processing,
)
from gsf.retrieval.text_to_sql.prompts import (
    create_sql_from_candidates_prompt,
    get_sql_user_prompt,
    format_dialect_rules,
    format_dual_question_block,
)
from gsf.retrieval.text_to_sql.evidence_hints import (
    build_evidence_hints_block,
    extract_evidence,
)
from gsf.retrieval.text_to_sql.models import (
    SQLDecompositionModel,
    SQLDecompositionTreeModel,
    SQLGenerationModel,
    SQLQueryPlanModel,
    SYNTHETIC_EXAMPLE_COUNT,
    SyntheticSQLExamplesModel,
)

logger = logging.getLogger(__name__)


# Multi-candidate SQL generation (off by default: BIRD_NCAND=1 → today's behavior).
_DEFAULT_CANDIDATE_TEMP = 0.8
_sampling_llm_cache: dict[float, Any] = {}

# Generation methods for candidates 1..N. Cand0 stays "" so BIRD_NCAND=1
# reproduces the literal-fidelity-v1 baseline exactly.
#
# The previous lineup varied the *caveats* (grain auditor, minimal plan, result
# shape, evidence-first) while every candidate still wrote SQL in one shot from
# the same context. Measured over 1001 questions of the v1 pool: 80.3% of pools
# collapsed to a single execution result, ~40% of "diverse" candidates were
# byte-identical to candidate 0, and when candidate 0 was wrong the rest
# repeated its exact wrong answer 64.4% of the time. Those caveats also merely
# restated rules already in the shared system prompt, so there was nothing left
# to disagree about.
#
# These instead change the *intermediate artifact* in a separate LLM call
# before SQL generation — a plan, a decomposition, or same-schema examples.
# The second call receives that artifact as authoritative context. This is
# intentionally two-stage: merely redescribing the ``thought`` field in the
# one SQL call did not create an independent reasoning path.
_CANDIDATE_STRATEGY_TAGS = (
    "baseline",
    "query_plan",
    "decomposition",
    "synthetic_examples",
    "query_plan_b",
    "alt_table_set",
)


# Per-slot SCHEMA readings. The five strategy tags above vary how a candidate
# reasons; none of them vary which tables and columns it reads, and measurement
# says that is the whole problem. On the 376 v15 questions where no candidate was
# correct, the pool held 1.04 distinct table-sets — 95.7% of failing pools had
# every candidate choosing identical tables — while 174 of those failures (46%)
# are themselves schema-linking errors: 51 under-joins, 46 swapped tables, 77
# wrong columns. The pool does not pick the wrong alternative, it never writes
# one down. Slot 0 stays free so this can only add readings, never remove the
# one we already get.
_SCHEMA_SLOT_ROLES = ("free", "join_preferring", "alternative_binding", "free")

_JOIN_PREFERRING = (
    "SCHEMA READING FOR THIS CANDIDATE — prefer the joined reading.\n"
    "Other candidates are writing the single-table reading of this question, so "
    "do not duplicate it. When an attribute the question needs exists BOTH in a "
    "table you already have AND in another table reachable by a foreign key, use "
    "the reachable table's column and add the join. Where two readings are both "
    "defensible, take the one that touches more tables.\n"
    "This is a reading to explore, not a rule to force: if joining would change "
    "what the question asks for, or no second table carries the attribute, write "
    "the query you believe is correct."
)

_ALTERNATIVE_BINDING = (
    "SCHEMA READING FOR THIS CANDIDATE — take the second-choice binding.\n"
    "Another candidate is already writing the most obvious reading, so yours must "
    "explore a different one. For each ambiguous term below: name the column you "
    "would reach for first, then commit to a DIFFERENT plausible column and write "
    "the query under that reading, adding whatever join it needs.\n"
    "If a term genuinely has only one plausible binding, leave it alone — the "
    "point is to cover a reading the pool would otherwise miss, not to be wrong "
    "on purpose."
)

_ALT_TABLE_SET_STRATEGY = (
    "SCHEMA DIVERGENCE FOR THIS CANDIDATE — change the base table set.\n"
    "Other candidates will take the most obvious fact table for this question. "
    "You must answer using a DIFFERENT primary table when a foreign-key-reachable "
    "alternative carries the same attribute (or a clearer one). Prefer a join "
    "path the obvious reading skips. Name the table you are deliberately not "
    "using as the sole base, and the table you use instead.\n"
    "If no second table is defensible, write the best query you can — but when "
    "two table sets are both plausible, you MUST take the less obvious one.\n"
    "Raw SQL only in the final answer."
)


def _schema_slots_enabled() -> bool:
    """Whether slots carry distinct schema readings. Env ``BIRD_SCHEMA_SLOTS``."""
    return os.environ.get("BIRD_SCHEMA_SLOTS", "0").strip().lower() in {
        "1",
        "true",
        "yes",
    }


def _schema_directive(index: int, entity_columns: list[dict] | None) -> str:
    """The schema-reading directive for slot *index*, or "" for a free slot.

    The ambiguous terms are named inline for the alternative-binding slot so the
    instruction has something concrete to bind to; without them it reads as a
    vague suggestion and the model reverts to the obvious choice.
    """
    if not _schema_slots_enabled() or index <= 0:
        return ""
    role = _SCHEMA_SLOT_ROLES[index % len(_SCHEMA_SLOT_ROLES)]
    if role == "join_preferring":
        return _JOIN_PREFERRING
    if role != "alternative_binding":
        return ""
    terms = [
        str(g.get("entity") or "").strip()
        for g in (entity_columns or [])
        if str(g.get("entity") or "").strip() and len(g.get("columns") or []) >= 2
    ]
    if not terms:
        # No measured ambiguity: a "pick something else" instruction with nothing
        # to point at invites an arbitrary swap, so fall back to the join reading.
        return _JOIN_PREFERRING
    return _ALTERNATIVE_BINDING + "\nAmbiguous terms: " + ", ".join(
        f'"{t}"' for t in terms[:6]
    )


def _pinned_strategy() -> str | None:
    """Force every candidate slot onto one strategy. Env ``BIRD_PIN_STRATEGY``.

    Empty (the default) keeps the round-robin over all five strategies, so the
    pool trades breadth for depth only when asked.

    This exists because our saturation evidence only covers breadth. Averaging
    over slot subsets of one run, added slots gain +3.70, +1.92, +1.31, +0.97 —
    but those are five *different* generators at one sample each, which says
    nothing about sampling one generator repeatedly. Published results put a
    single generator's seven-sample ceiling above our entire five-generator pool,
    and that is the untested direction.

    Note this also makes slot 0 sample. Normally slot 0 runs on the base client
    at no sampling temperature so ``BIRD_NCAND=1`` reproduces single-candidate
    behavior exactly; left alone it would make one of N draws deterministic and
    understate the spread the experiment is trying to measure.
    """
    raw = os.environ.get("BIRD_PIN_STRATEGY", "").strip().lower()
    if raw in _CANDIDATE_STRATEGY_TAGS:
        return raw
    if raw:
        logger.warning(
            "BIRD_PIN_STRATEGY=%r is not one of %s; ignoring",
            raw,
            ", ".join(_CANDIDATE_STRATEGY_TAGS),
        )
    return None


def _slot_plan() -> tuple[str, ...] | None:
    """Explicit strategy per candidate slot. Env ``BIRD_SLOT_PLAN``.

    Comma-separated tags, one per slot, e.g. ``baseline,query_plan,decomposition,
    synthetic_examples,synthetic_examples,synthetic_examples,query_plan``. Empty
    (the default) keeps the round-robin, which at seven slots hands out two
    baseline, two query_plan, and one each of the rest.

    Round-robin caps a strategy's influence at its slot share, and that share is
    what bounds its contribution to the pool — not how good it is. Online
    synthetic examples are reported at +9.34pp when they *are* the system; ours
    owns one slot of seven and measured +1.34pp marginal, which is within rounding
    of 9.34/7. Scaling the examples inside one slot cannot escape that ceiling, so
    the slot share has to move instead.
    """
    raw = os.environ.get("BIRD_SLOT_PLAN", "").strip().lower()
    if not raw:
        return None
    plan = tuple(tag.strip() for tag in raw.split(",") if tag.strip())
    unknown = sorted({tag for tag in plan if tag not in _CANDIDATE_STRATEGY_TAGS})
    if unknown:
        logger.warning(
            "BIRD_SLOT_PLAN names unknown strategies %s; ignoring the plan",
            ", ".join(unknown),
        )
        return None
    return plan or None


_QUERY_PLAN_ARTIFACT_PROMPT = (
    "STAGE 1 OF 2 — QUERY PLAN ONLY. Do not write SQL. Build a concrete "
    "numbered execution plan for the target question from the supplied schema "
    "and evidence. Name every table and join key, exact filters/literals, "
    "target row grain, aggregation, projection, ordering, and LIMIT. Return "
    "only the structured plan."
)
_DECOMPOSITION_ARTIFACT_PROMPT = (
    "STAGE 1 OF 2 — DECOMPOSITION ONLY. Do not write the final SQL. Break the "
    "target question into 2-4 independently answerable sub-questions. For "
    "each, identify the exact tables, columns, joins, filters, and aggregate "
    "needed, then state how their answers compose at the requested row grain. "
    "Return only the structured decomposition."
)
# The flat variant above yields a linear list of table-access steps — the same
# artifact the query-plan generator already produces, which is why the two
# agree so often and why decomposition adds almost nothing to the pool ceiling.
# This keeps the recursion and the bottom-up substitution.
_DECOMPOSITION_TREE_ARTIFACT_PROMPT = (
    "STAGE 1 OF 2 — DIVIDE AND CONQUER. Do not write the final SQL.\n\n"
    "Start from the target question. State what it asks for, then write "
    "top-level pseudo SQL in which every part you cannot yet resolve is left "
    "as bracketed natural language, e.g.\n"
    "  SELECT T1.gender FROM client AS T1 "
    "WHERE <youngest client in the lowest average salary branch>\n\n"
    "Then solve each bracketed part as its own numbered sub-question with its "
    "own analysis and its own pseudo SQL. If a sub-question still contains a "
    "bracketed part, give it a nested label (1.1, 1.2) and solve that too. "
    "Stop only when no brackets remain.\n\n"
    "Finish with two separate steps: assembly, substituting each node's "
    "pseudo SQL into its parent's placeholder from the bottom up; and "
    "simplification, collapsing nested subqueries into joins and dropping "
    "redundant clauses without changing the row grain.\n\n"
    "Return only the structured decomposition."
)
_SYNTHETIC_ARTIFACT_PROMPT = (
    "STAGE 1 OF 2 — ONLINE SAME-SCHEMA DEMONSTRATIONS ONLY. Generate "
    f"{SYNTHETIC_EXAMPLE_COUNT} realistic question-to-SQL examples using ONLY "
    "the supplied target schema and documented join keys. Make them "
    "structurally useful for the target question (relevant joins, filters, "
    "aggregation, or output grain) but do not paraphrase or solve the target "
    "question. Vary them: different join depths, different aggregations, and "
    "both filtered and unfiltered shapes, so together they show how this "
    "schema is meant to be queried. Each SQL must be complete and executable. "
    "Return only the structured examples."
)
# A demonstration transfers the procedure it shows. A bare question/answer pair
# shows a question leaping straight to finished SQL, which is the one-shot
# guessing behavior the rule block then tries to suppress. Asking for the
# derivation costs nothing extra — these examples are already generated and
# execution-checked — and turns each one into a worked example.
_SYNTHETIC_ARTIFACT_PROMPT_WITH_REASONING = (
    _SYNTHETIC_ARTIFACT_PROMPT[:-1]
    + " with their derivations.\n\nFor each example also give the reasoning "
    "that produces the SQL: the tables needed and why, the join keys, the "
    "filters with exact literals, the row grain, and the projection. Write it "
    "as the derivation a reader could follow to rebuild the query, not as a "
    "description of the finished query."
)
_QUERY_PLAN_TRANSLATION_PROMPT = (
    "STAGE 2 OF 2 — TRANSLATE THE PLAN TO SQL. The plan below is the "
    "intermediate reasoning artifact. Implement it faithfully, but correct any "
    "step that contradicts the visible schema or evidence. Do not add a SQL "
    "clause unless a plan step requires it.\n\nQUERY PLAN:\n{artifact}"
)
_DECOMPOSITION_TRANSLATION_PROMPT = (
    "STAGE 2 OF 2 — ASSEMBLE THE FINAL SQL. Solve each decomposition item and "
    "compose them into one executable query at the requested row grain. Drop "
    "any clause that answers no sub-question.\n\nDECOMPOSITION:\n{artifact}"
)
_DECOMPOSITION_TREE_TRANSLATION_PROMPT = (
    "STAGE 2 OF 2 — EMIT THE SIMPLIFIED QUERY. The decomposition below already "
    "carries the assembly and simplification steps. Produce the query they "
    "arrive at: every bracketed placeholder resolved, the simplification "
    "applied, and no clause that answers no sub-question. Correct any step "
    "that contradicts the visible schema or evidence, and keep the row grain "
    "the main analysis states.\n\nDECOMPOSITION:\n{artifact}"
)
_SYNTHETIC_USE_PROMPT = (
    "STAGE 2 OF 2 — SOLVE THE TARGET QUESTION. The Example SQL Queries section "
    "contains newly generated and execution-checked demonstrations from this "
    "exact database schema. Use their schema-specific joins and conventions "
    "when relevant, but answer the target question rather than copying an "
    "example."
)


def _decomposition_tree_enabled() -> bool:
    """Whether decomposition emits a recursive tree instead of a flat list.

    Env ``BIRD_DECOMPOSITION_TREE`` (default ``0`` = current behavior).
    """
    return os.environ.get("BIRD_DECOMPOSITION_TREE", "0").strip().lower() not in {
        "0",
        "false",
        "no",
        "off",
        "",
    }


def _format_decomposition_tree(artifact) -> str:
    """Render the decomposition tree in solve order, deepest nodes first.

    Sorting by label depth puts a child ahead of its parent, so the text is
    already in substitution order by the time stage 2 reads it.
    """

    def _depth(label: str) -> tuple:
        parts = [p for p in str(label).split(".") if p.strip()]
        return (-len(parts), [int(p) if p.isdigit() else 0 for p in parts])

    lines = [
        f"Main analysis: {artifact.main_analysis}",
        f"Main pseudo SQL: {artifact.main_pseudo_sql}",
        "",
    ]
    for node in sorted(artifact.nodes, key=lambda n: _depth(n.label)):
        lines.append(f"Sub-question {node.label}: {node.question}")
        lines.append(f"  Analysis: {node.analysis}")
        lines.append(f"  Pseudo SQL: {node.pseudo_sql}")
    lines.append("")
    lines.append(f"Assembly: {artifact.assembly}")
    lines.append(f"Simplification: {artifact.simplification}")
    return "\n".join(lines)


def _synthetic_reasoning_enabled() -> bool:
    """Whether online demonstrations carry their derivation into the prompt.

    Env ``BIRD_SYNTHETIC_REASONING`` (default ``0`` = current behavior).
    """
    return os.environ.get("BIRD_SYNTHETIC_REASONING", "0").strip().lower() not in {
        "0",
        "false",
        "no",
        "off",
        "",
    }


def _num_candidates() -> int:
    """Number of SQL candidates to generate (env ``BIRD_NCAND``, default 1)."""
    try:
        return max(1, int(os.environ.get("BIRD_NCAND", "1")))
    except (TypeError, ValueError):
        return 1


def _prompt_sql_attrs_enabled() -> bool:
    """Whether SQL attributes (derived metrics/formulas) go into the prompt.

    Env ``BIRD_PROMPT_SQL_ATTRS`` (default ``1`` = current behavior). Set to 0 to
    ablate the block: retrieved SQL attributes are still logged but never shown
    to the generator, and the system prompt drops the "reuse these expressions"
    instruction.
    """
    return os.environ.get("BIRD_PROMPT_SQL_ATTRS", "1").strip().lower() not in {
        "0",
        "false",
        "no",
    }


def _candidate_temperature() -> float:
    """Sampling temperature for candidates 2..N (env ``BIRD_NCAND_TEMP``)."""
    try:
        return float(os.environ.get("BIRD_NCAND_TEMP", str(_DEFAULT_CANDIDATE_TEMP)))
    except (TypeError, ValueError):
        return _DEFAULT_CANDIDATE_TEMP


def _get_sampling_llm(temperature: float):
    """Return a cached LLM client at *temperature* for candidate diversity.

    gpt-5.x / o-series ignore an explicit temperature (their provider default
    already samples), so schema shuffling is the main diversity lever there;
    nemotron / claude need the raised temperature to actually diverge.
    """
    client = _sampling_llm_cache.get(temperature)
    if client is None:
        client = get_llm_client(temperature=temperature)
        _sampling_llm_cache[temperature] = client
    return client


def _shuffled(tables: list[dict], rng: random.Random) -> list[dict]:
    """Return a copy of *tables* with table order and per-table column order shuffled.

    AskData-style schema-order perturbation that diversifies candidates even
    when the model is near-deterministic. Each table dict is shallow-copied so
    the caller's list (and the shared column lists) are left untouched.
    """
    out = [dict(t) for t in tables]
    rng.shuffle(out)
    for t in out:
        cols = t.get("columns")
        if isinstance(cols, list):
            cols = list(cols)
            rng.shuffle(cols)
            t["columns"] = cols
    return out


def _candidate_few_shots(
    examples: list[tuple[str, str]], index: int
) -> tuple[list[tuple[str, str]], list[int]]:
    """Return a deterministic, diverse few-shot subset for candidate *index*.

    Candidate 0 keeps every reranked example, preserving the baseline path.
    Other candidates use complementary four-example slices so they do not all
    anchor on the exact same demonstrations.
    """
    if index == 0 or len(examples) <= 4:
        return list(examples), list(range(len(examples)))

    patterns = (
        (0, 2, 4, 6),
        (1, 3, 5, 7),
        (0, 1, 4, 5),
        (2, 3, 6, 7),
    )
    pattern = patterns[(index - 1) % len(patterns)]
    selected_indices = [i for i in pattern if i < len(examples)]
    return [examples[i] for i in selected_indices], selected_indices


def _hop_column(hop: dict, side: str, target_db: str | None = None) -> str:
    """Format a hop endpoint (``side`` is ``"source"`` or ``"target"``) as
    ``schema.table.column`` (or ``table.column`` when the schema is absent).

    ``target_db`` scopes execution to one database, so the *database* prefix is
    dropped — but the schema qualifier is kept whenever present, since schema
    dialects (Postgres/Snowflake) need it to resolve the table.
    """
    schema = hop.get(f"{side}_schema", "")
    table = hop.get(f"{side}_table", "")
    column = hop.get(f"{side}_column", "")
    if not schema:
        prefix = table
    else:
        prefix = f"{schema}.{table}"
    return f"{prefix}.{column}"


def _hop_table(hop: dict, side: str) -> str:
    return (hop.get(f"{side}_table") or "").strip().lower()


def _cross_table_join_eqs_from_path(
    path: list[dict],
    target_db: str | None = None,
) -> list[str]:
    """Return only real cross-table join equalities from a hop path.

    Same-table hops are CONTAINS navigation (metric col → FK col on the same
    table). Emitting them as ``col_a = col_b`` pollutes the AUTHORITATIVE join
    block with nonsense equalities.
    """
    if not path:
        return []

    eqs: list[str] = []
    seen: set[str] = set()

    def _add(left: str, right: str) -> None:
        key = "|=|".join(sorted([left.lower(), right.lower()]))
        if key in seen or left.lower() == right.lower():
            return
        seen.add(key)
        eqs.append(f"{left} = {right}")

    # Within-hop: only when the hop itself crosses tables (shared-PK SEMANTIC_FK).
    for hop in path:
        if _hop_table(hop, "source") != _hop_table(hop, "target"):
            _add(
                _hop_column(hop, "source", target_db),
                _hop_column(hop, "target", target_db),
            )

    # Between consecutive hops: target[i] = source[i+1] (usual multi-hop form).
    for cur, nxt in zip(path, path[1:]):
        if _hop_table(cur, "target") != _hop_table(nxt, "source"):
            _add(
                _hop_column(cur, "target", target_db),
                _hop_column(nxt, "source", target_db),
            )

    return eqs


def _physical_fk_joins_enabled() -> bool:
    """Whether physical FK edges replace semantic paths as the join authority.

    Env ``BIRD_PHYSICAL_FK_JOINS`` (default ``0`` = current behavior). When on,
    the prompt lists the schema's own FK equations and any semantic path hop not
    backed by an FK is dropped instead of being presented as authoritative.
    """
    return os.environ.get("BIRD_PHYSICAL_FK_JOINS", "0").strip().lower() not in {
        "0",
        "false",
        "no",
    }


def _fk_side(ref: str) -> str:
    """Normalize one side of a join equation to ``table.column``."""
    parts = [p for p in str(ref).strip().lower().split(".") if p]
    return ".".join(parts[-2:]) if len(parts) >= 2 else str(ref).strip().lower()


def _fk_equation_key(left: str, right: str) -> str:
    return "=".join(sorted((_fk_side(left), _fk_side(right))))


def _fk_index(verified_fks: list[dict]) -> tuple[set[str], dict[str, set[str]]]:
    """Return ``(equation keys, column -> columns it is FK-linked to)``.

    The second map lets two columns that both reference the same parent key be
    recognized as a legitimate join even though no FK edge directly connects
    them (the common ``a.fk = b.fk`` sibling join).
    """
    keys: set[str] = set()
    linked: dict[str, set[str]] = {}
    for fk in verified_fks or []:
        left = f"{fk.get('table1', '')}.{fk.get('column1', '')}"
        right = f"{fk.get('table2', '')}.{fk.get('column2', '')}"
        if not fk.get("column1") or not fk.get("column2"):
            continue
        keys.add(_fk_equation_key(left, right))
        a, b = _fk_side(left), _fk_side(right)
        linked.setdefault(a, set()).add(b)
        linked.setdefault(b, set()).add(a)
    return keys, linked


def _fk_supports(eq: str, keys: set[str], linked: dict[str, set[str]]) -> bool:
    """True when *eq* is a real FK edge or a shared-parent sibling join."""
    sides = str(eq).split("=")
    if len(sides) != 2:
        return False
    a, b = _fk_side(sides[0]), _fk_side(sides[1])
    if "=".join(sorted((a, b))) in keys:
        return True
    return bool(linked.get(a) and linked.get(b) and linked[a] & linked[b])


def _format_verified_fk_block(verified_fks: list[dict], target_db: str | None) -> str:
    """Render the schema's own FK equations as the join authority."""
    seen: set[str] = set()
    lines: list[str] = []
    for fk in verified_fks or []:
        if not fk.get("column1") or not fk.get("column2"):
            continue
        left = f"{fk.get('table1', '')}.{fk.get('column1', '')}"
        right = f"{fk.get('table2', '')}.{fk.get('column2', '')}"
        key = _fk_equation_key(left, right)
        if key in seen:
            continue
        seen.add(key)
        lines.append(f"  {_fk_side(left)} = {_fk_side(right)}")
    if not lines:
        return ""
    return "\n".join(
        [
            "VERIFIED FOREIGN KEYS (from the database schema — these are the only "
            "join conditions known to be correct; use them whenever you join these "
            "tables):",
            *sorted(lines),
        ]
    )


def _format_semantic_context(
    primary_attribute: dict,
    attribute_join_paths: list[dict],
    target_db: str | None = None,
    verified_fks: list[dict] | None = None,
) -> str:
    """Format the semantic anchor + join-path context for the SQL prompt.

    Produces a human-readable block describing the anchor table/column and
    how to reach every other retrieved column via JOIN conditions derived
    from the semantic graph.

    When *target_db* is set, schema qualifiers are omitted because
    execution is already scoped to that database.

    Example output::

        ANCHOR TABLE (primary focus of the question):
          Table: public.orders
          Column: creator_id  (Creator)

        RELATED COLUMNS (accessible via semantic joins):
          User Name: public.users.name
            Join path from anchor:
              public.orders.creator_id = public.users.id
    """
    anchor_schema = primary_attribute.get("schema_name", "")
    anchor_table = primary_attribute.get("table_name", "")
    anchor_col = primary_attribute.get("col_name", "")
    anchor_name = primary_attribute.get("attr_name", "")
    anchor_full = f"{anchor_schema}.{anchor_table}" if anchor_schema else anchor_table

    lines: list[str] = [
        "SEMANTIC HINT — likely starting table (use as a strong hint, not a mandate):",
        f"  Table: {anchor_full}",
        f"  Column: {anchor_col}  ({anchor_name})",
    ]

    physical = _physical_fk_joins_enabled()
    fk_keys, fk_linked = _fk_index(verified_fks or [])

    if physical:
        fk_block = _format_verified_fk_block(verified_fks or [], target_db)
        if fk_block:
            lines.append("")
            lines.append(fk_block)

    if attribute_join_paths:
        lines.append("")
        if physical:
            lines.append(
                "RELATED COLUMNS reachable from the anchor. The equations below are "
                "cross-checked against the foreign keys above; prefer the verified "
                "foreign keys whenever both apply. Use only the hops you need:"
            )
        else:
            lines.append(
                "JOIN PATHS (AUTHORITATIVE — derived from the verified semantic model). "
                "This is our most reliable knowledge of how these tables join: use these "
                "exact join conditions almost always, and only deviate if they clearly "
                "cannot answer the question. Use only the hops you need:"
            )
        n_same_skipped = 0
        n_cross_shown = 0
        n_unbacked_dropped = 0
        for entry in attribute_join_paths:
            attr_name = entry.get("attr_name", "")
            col_name = entry.get("col_name", "")
            schema = entry.get("schema_name", "")
            table = entry.get("table_name", "")
            full_table = f"{schema}.{table}" if schema else table
            lines.append(f"  {attr_name}: {full_table}.{col_name}")
            path = entry.get("path") or []
            # Count same-table-only single hops (legacy noise) for debug.
            if len(path) == 1 and _hop_table(path[0], "source") == _hop_table(
                path[0], "target"
            ):
                n_same_skipped += 1
            eqs = _cross_table_join_eqs_from_path(path, target_db)
            if physical:
                kept = [e for e in eqs if _fk_supports(e, fk_keys, fk_linked)]
                n_unbacked_dropped += len(eqs) - len(kept)
                eqs = kept
            if eqs:
                lines.append("    Join path:")
                for eq in eqs:
                    lines.append(f"      {eq}")
                    n_cross_shown += 1

    return "\n".join(lines)


# A column description written at ingest ends in "— samples: a, b" or, for a
# closed enumeration, "— one of: a, b".
_VALUE_SUFFIX_MARKERS = ("samples:", "one of:")


def _entity_columns_enabled() -> bool:
    """Whether to print the entity -> competing columns block. Env ``BIRD_ENTITY_COLUMNS``."""
    return os.environ.get("BIRD_ENTITY_COLUMNS", "0").strip().lower() not in {
        "0",
        "false",
        "no",
        "off",
        "",
    }


def format_entity_columns_for_prompt(entity_columns: list[dict]) -> str:
    """Group the columns that matched each question entity, with their real values.

    The columns themselves are mostly already in the schema block; what is missing
    there is adjacency. A flat block of twenty tables scatters schools.Charter,
    schools.FundingType and frpm.'Charter Funding Type' far apart, so the choice
    between them never presents itself as a choice. Printing them together under the
    entity that matched all three makes the comparison unavoidable without asking the
    model to volunteer any doubt.
    """
    if not entity_columns:
        return ""
    max_cols = _env_int_local("BIRD_ENTITY_COLUMNS_PER_ENTITY", 4)
    lines = [
        "ENTITY -> CANDIDATE COLUMNS (each question term matched several columns in "
        "the tables above; their real values tell them apart. These are listed "
        "alphabetically, NOT in preference order, and a column in a different table "
        "is only usable if you join to that table — do not switch tables to avoid a "
        "join):"
    ]
    for group in entity_columns:
        entity = str(group.get("entity") or "").strip()
        cols = (group.get("columns") or [])[:max_cols]
        if not entity or len(cols) < 2:
            continue
        lines.append(f'  "{entity}":')
        tables_here = {str(c.get("table") or "").lower() for c in cols}
        for c in cols:
            bits = [f"    - {c.get('qualified')}"]
            if c.get("type"):
                bits.append(f"({c['type']})")
            # Naming the join cost inline is the point: v1 presented a same-table
            # lookalike as a peer of the column gold needed, and the model took the
            # single-table shortcut rather than joining.
            if len(tables_here) > 1:
                partners = [
                    p for p in (c.get("fk_partners") or []) if p in tables_here
                ]
                bits.append(
                    f"[in {c.get('table')}; FK to {', '.join(partners)}]"
                    if partners
                    else f"[in {c.get('table')}; no FK to the other tables listed here]"
                )
            desc = _short_description(
                str(c.get("description") or ""), str(c.get("column") or "")
            )
            if desc:
                bits.append(f"— {desc}")
            vals = str(c.get("sample_values") or "").strip()
            if vals:
                bits.append(f"| values: {vals[:110]}")
            lines.append(" ".join(bits))
    if len(lines) == 1:
        return ""
    return "\n".join(lines) + "\n\n"


def _env_int_local(name: str, default: int) -> int:
    try:
        return max(1, int(os.environ.get(name, str(default))))
    except (TypeError, ValueError):
        return default


def _short_description(description: str, column: str) -> str:
    """A description worth the tokens, or nothing.

    Two thirds of these restate the column name ("Charter Funding Type" for
    ``frpm.'Charter Funding Type'``), which costs tokens and adds no way to tell two
    columns apart. The rest often enumerate the values, which are printed right after
    anyway. Trims at a word boundary so the line does not end mid-word.
    """
    desc = " ".join(description.split())
    if not desc:
        return ""
    # Cut the value enumeration before comparing to the column name, or a description
    # that is just the name plus its codes ("Charter School (Y/N), 0: N;1: Y") fails to
    # match and prints anyway. The values follow on the same line regardless.
    desc = re.split(r"(?:Values are as follows|The field is coded as follows|, 0:)", desc)[0]
    desc = desc.strip().rstrip(",.")
    norm = re.sub(r"[^a-z0-9]+", "", desc.lower())
    if norm == re.sub(r"[^a-z0-9]+", "", column.lower()):
        return ""
    if len(desc) > 90:
        desc = desc[:90].rsplit(" ", 1)[0] + "..."
    return desc


def _description_lists_values(description: str | None) -> bool:
    """Whether a description already enumerates the column's values."""
    return any(marker in (description or "") for marker in _VALUE_SUFFIX_MARKERS)


# SQL keywords and probe scaffolding, so `_ev_cols` holds column names only.
_SQL_WORDS = {
    "SELECT", "DISTINCT", "FROM", "WHERE", "LIMIT", "AND", "OR", "NOT", "NULL",
    "IS", "LIKE", "JOIN", "INNER", "LEFT", "OUTER", "ON", "AS", "GROUP", "BY",
    "ORDER", "HAVING", "COUNT", "SUM", "AVG", "MIN", "MAX", "CAST", "REAL",
    "CASE", "WHEN", "THEN", "ELSE", "END", "MAIN", "ASC", "DESC", "IN",
}


def format_tables_for_prompt(tables: list[dict], target_db: str | None = None) -> str:
    """
    Format tables with clear column information to prevent cross-table column confusion.

    Args:
        tables: Table dicts from ``path_state["relevant_tables"]`` — each must expose
            ``columns`` as a list of dicts (from ``_normalize_table_to_relevant_shape`` / prep).
        target_db: When set, only the *database* prefix is omitted (execution is
            already scoped to this database). The schema qualifier is kept when
            present, since schema dialects (Postgres/Snowflake) need it.

    Returns:
        Formatted string clearly showing which columns belong to each table
    """
    if not tables:
        return "No tables available"

    formatted_tables = []
    for table in tables:
        table_parts = []

        # Table identifier
        table_name = table.get("name", "UNKNOWN")
        table_label = table.get("label", "")
        table_description = table.get("description", "")

        # Database and schema info
        database_name = table.get("database_name", "")
        schema_name = table.get("schema_name", "")

        # target_db scopes execution to one database, so drop only the *database*
        # prefix; keep the schema (Postgres/Snowflake need schema.table). SQLite/
        # BIRD tables carry no schema_name, so this collapses to a bare name.
        if database_name and schema_name and not target_db:
            full_name = f"{database_name}.{schema_name}.{table_name}"
        elif schema_name:
            full_name = f"{schema_name}.{table_name}"
        else:
            full_name = table_name

        table_parts.append(f"TABLE: {full_name}")
        if table_label and table_label != table_name:
            table_parts.append(f"  Label: {table_label}")
        if table_description:
            table_parts.append(f"  Description: {table_description}")

        # Primary key
        if "primary_key" in table:
            table_parts.append(f"  Primary Key: {table['primary_key']}")

        columns = table.get("columns")
        if not isinstance(columns, list):
            columns = []
        if columns:
            table_parts.append(
                "  AVAILABLE COLUMNS (only use these columns for this table):"
            )
            for col in columns:
                # Handle both dict and string column formats
                if isinstance(col, dict):
                    col_name = col.get("name", "UNKNOWN")
                    col_type = col.get("data_type", "UNKNOWN")
                    col_desc = col.get("description", "")
                    sample_values = col.get("sample_values")

                    col_line = f"    - {col_name} ({col_type})"
                    if col_desc:
                        col_line += f" - {col_desc}"
                    # Candidate expansion projects the description *and*
                    # sample_values, so printing both repeats every value — the
                    # second time with JSON quoting. ``fetch_fk_neighbour_tables``
                    # drops the field at fetch time; doing it here covers every
                    # fetch path, including the candidate one that never did.
                    if sample_values and not _description_lists_values(col_desc):
                        col_line += f" | sample values: {sample_values}"
                    table_parts.append(col_line)
                elif isinstance(col, str):
                    # If column is a string, use it directly
                    table_parts.append(f"    - {col}")
                else:
                    # Unknown format, convert to string
                    table_parts.append(f"    - {str(col)}")

        formatted_tables.append("\n".join(table_parts))

    return "\n\n".join(formatted_tables)


class SQLFromCandidatesAgent(BaseAgent):
    """
    Agent that constructs SQL from semantic retrieval and prepared schema context.

    Uses candidates, table groups, and related signals produced by
    CandidatePreparationAgent, then prompts the LLM to produce SQL

    Input Requirements:
    - path_state["retrieved_candidates"]: Candidate dicts from preparation
    - path_state["relevant_tables"]: schema context
    - path_state["relevant_queries"]: Relevant queries (from CandidatePreparationAgent)

    Output:
    - path_state["sql_generation_result"]: SQL response with SQL code or text answer
    - path_state["relevant_tables"]: Relevant tables used
    - path_state["custom_analyses_used"]: Semantic entity IDs used
    - decision: "constructable" or "unconstructable"
    """

    def __init__(self):
        super().__init__("sql_from_semantic")

    def validate_input(self, state: AgentState) -> bool:
        """Validate that CandidatePreparationAgent has run (keys exist in path_state)."""
        path_state = state.get("path_state", {})
        has_context = (
            "primary_attribute" in path_state
            or "attribute_join_paths" in path_state
            or "retrieved_column_attributes" in path_state
        )
        if not has_context:
            self.logger.warning(
                "CandidatePreparationAgent output missing: expected primary_attribute, "
                "attribute_join_paths, or retrieved_column_attributes in path_state"
            )
            return False
        return True

    def execute(self, state: AgentState) -> Dict[str, Any]:
        """
        Construct SQL from semantic candidates and prepared schema context.

        Uses CandidatePreparationAgent outputs (candidates, tables, queries,
        similar questions). May return a text response when the model does not emit SQL.

        Args:
            state: Current agent state

        Returns:
            Dictionary with:
            - path_state: Contains SQL response, tables, connection, custom analyses
            - messages: Adds SQL response to messages
            - decision: "constructable" or "unconstructable"
        """
        path_state = state.get("path_state", {})
        llm = state["llm"]
        connectors = state.get("connectors") or []
        original_question = get_original_question(state)
        sanitized_question = get_question_for_processing(state)
        main_question = format_dual_question_block(
            original_question, sanitized_question
        )

        primary_attribute: dict | None = path_state.get("primary_attribute")
        attribute_join_paths: list[dict] = path_state.get("attribute_join_paths") or []
        relevant_tables = path_state.get("relevant_tables", [])
        relevant_queries = path_state.get("relevant_queries", [])
        similar_questions = path_state.get("similar_questions", [])
        custom_analyses = path_state.get("custom_analyses", [])
        custom_analyses_str = path_state.get("custom_analyses_str", [])
        sql_attributes = path_state.get("sql_attributes", [])
        sql_attributes_str = path_state.get("sql_attributes_str", [])
        term_synonyms: dict = path_state.get("term_synonyms") or {}

        connector = resolve_connector_from_tables(relevant_tables, connectors)
        dialect = getattr(connector, "dialect", None)
        show_sql_attrs = _prompt_sql_attrs_enabled()

        self.logger.info(
            "Semantic context: anchor=%s, join_paths=%d, custom_analyses=%d, "
            "sql_attributes=%d, fallback_tables=%d",
            primary_attribute.get("attr_name") if primary_attribute else None,
            len(attribute_join_paths),
            len(custom_analyses),
            len(sql_attributes),
            len(relevant_tables),
        )

        self.logger.info(
            f"Using {len(similar_questions)} similar questions from conversations."
        )

        def build_messages(
            tables_variant: list | None = None,
            similar_questions_variant: list[tuple[str, str]] | None = None,
            strategy: str = "",
            schema_directive: str = "",
        ) -> list:
            """
            Build messages for SQL construction.

            Includes semantic candidate context, similar questions, and optionally
            extracted file data or file excerpts.

            ``tables_variant`` overrides the schema tables used in the prompt.
            ``similar_questions_variant`` supplies a per-candidate demo subset.
            ``strategy`` adds a candidate-specific reasoning lens.
            ``schema_directive`` adds a candidate-specific *schema* reading, which
            is a different axis: ``strategy`` changes how the slot reasons, this
            changes which tables and columns it is asked to reason about.
            """
            tables_for_prompt = (
                tables_variant if tables_variant is not None else relevant_tables
            )
            few_shots_for_prompt = (
                similar_questions_variant
                if similar_questions_variant is not None
                else similar_questions
            )
            similar_questions_txt = "\n".join(
                (
                    f"question: {x[0]}\nreasoning: {x[2]}\nanswer: {x[1]}"
                    if len(x) > 2 and str(x[2]).strip()
                    else f"question: {x[0]}\nanswer: {x[1]}"
                )
                for x in few_shots_for_prompt
            )
            relevance_reasoning = path_state.get("table_relevance_reasoning", "")
            observation_block = ""
            if relevance_reasoning:
                observation_block += (
                    f"\nTable selection reasoning:\n{relevance_reasoning}\n"
                )
            observation_block += f"\nlist of important semantic entities with sql snippets:\n{custom_analyses_str}\n"
            if sql_attributes_str and show_sql_attrs:
                observation_block += (
                    f"\nlist of sql attributes (derived metrics/formulas):\n"
                    f"{sql_attributes_str}\n"
                )
            if term_synonyms:
                gloss_lines = ["TERM GLOSSARY (alternate names users may use):"]
                for term_name, syns in term_synonyms.items():
                    gloss_lines.append(
                        f"  {term_name}: also known as {', '.join(syns)}"
                    )
                observation_block += "\n" + "\n".join(gloss_lines) + "\n"
            if extract_evidence(original_question):
                evidence_hints = build_evidence_hints_block(original_question)
                if evidence_hints:
                    observation_block += f"\n{evidence_hints}\n"

            # Build custom analyses section for user prompt
            ca_section = ""
            if custom_analyses:
                ca_lines = []
                for a in custom_analyses:
                    line = f"- {a.get('name', '(unnamed)')}"
                    desc = (a.get("description") or "").strip()
                    if desc:
                        line += f": {desc}"
                    sql = (a.get("sql") or "").strip()
                    if sql:
                        line += f"\n  SQL: {sql}"
                    ca_lines.append(line)
                ca_section = (
                    "DOMAIN-SPECIFIC CUSTOM ANALYSES (use their SQL patterns as guidance):\n"
                    + "\n".join(ca_lines)
                    + "\n\n"
                )

            # Build sql attributes section for user prompt
            sa_section = ""
            if sql_attributes and show_sql_attrs:
                sa_lines = []
                for a in sql_attributes:
                    line = f"- {a.get('name', '(unnamed)')}"
                    desc = (a.get("description") or "").strip()
                    if desc:
                        line += f": {desc}"
                    expr = (a.get("expression") or "").strip()
                    sql = (a.get("sql") or "").strip()
                    if expr:
                        line += f"\n  Expression: {expr}"
                    if sql and sql != expr:
                        line += f"\n  Full query: {sql}"
                    sa_lines.append(line)
                sa_section = (
                    "SQL ATTRIBUTES (derived metrics/formulas — "
                    "use their expressions and SQL as guidance):\n"
                    + "\n".join(sa_lines)
                    + "\n\n"
                )

            target_db = path_state.get("target_db")

            # Build the join-paths section (semantic hint + suggested joins).
            join_paths = ""
            if primary_attribute:
                join_paths = (
                    "## Semantic Hints & Join Paths\n"
                    + _format_semantic_context(
                        primary_attribute,
                        attribute_join_paths,
                        target_db=target_db,
                        verified_fks=path_state.get("verified_fks") or [],
                    )
                    + "\n\n"
                )

            # Build the available-tables schema section.
            tables_section = (
                "AVAILABLE TABLES (schema context):\n"
                + format_tables_for_prompt(tables_for_prompt, target_db=target_db)
                if tables_for_prompt
                else "No tables available."
            )
            # Ahead of the schema, so the ambiguous bindings are read before the flat
            # block in which they are otherwise scattered.
            entity_columns_section = ""
            if _entity_columns_enabled():
                entity_columns_section = format_entity_columns_for_prompt(
                    path_state.get("entity_columns") or []
                )
                tables_section = entity_columns_section + tables_section

            # Build user prompt
            user_prompt = get_sql_user_prompt().format(
                dialect=dialect,
                dialect_rules=format_dialect_rules(dialect),
                main_question=main_question,
                observation_block=observation_block,
                queries=relevant_queries,
                qa_from_conversations=similar_questions_txt,
                tables=tables_section,
                join_paths=join_paths,
                custom_analyses=ca_section + sa_section,
            )


            # Choose system prompt based on context
            has_evidence = extract_evidence(original_question) is not None
            system_prompt = create_sql_from_candidates_prompt(
                dialect=dialect,
                target_db=target_db,
                has_evidence=has_evidence,
                include_sql_attributes=show_sql_attrs,
            )

            messages = state["messages"] + [SystemMessage(content=system_prompt)]
            if strategy:
                messages.append(SystemMessage(content=strategy))
            # After the strategy, so a two-stage method's reasoning lens is read
            # first and the schema reading it must work under comes last.
            if schema_directive:
                messages.append(SystemMessage(content=schema_directive))
            messages.append(HumanMessage(content=user_prompt))


            # Add calendar time window reminder if needed
            if any(
                phrase in sanitized_question.lower()
                for phrase in ["last week", "last month", "last year"]
            ):
                messages.append(
                    SystemMessage(
                        content="Apply only calendar time windows. DO NOT apply rolling time windows."
                    )
                )

            return messages

        def generate_candidate(index: int) -> tuple:
            """Generate one SQL candidate.

            Candidate 0 uses the base client and the original schema order (so
            ``BIRD_NCAND=1`` reproduces today's behavior exactly). Other slots
            use a real two-stage method: first produce an explicit intermediate
            artifact, then make a separate call to translate it into SQL.

            Under ``BIRD_PIN_STRATEGY`` every slot runs one strategy and every
            slot samples, including slot 0 — see ``_pinned_strategy``.
            """
            started = time.monotonic()
            pinned = _pinned_strategy() if n_candidates > 1 else None
            if n_candidates < 2 or (index == 0 and pinned is None):
                tables_variant = None
                client = llm
            else:
                rng = random.Random(1000 + index)
                tables_variant = _shuffled(relevant_tables, rng)
                client = _get_sampling_llm(candidate_temp)
            few_shot_variant, few_shot_indices = _candidate_few_shots(
                similar_questions, index
            )
            plan = _slot_plan() if n_candidates > 1 else None
            if pinned:
                strategy_tag = pinned
            elif plan:
                strategy_tag = plan[index % len(plan)]
            elif n_candidates > 1:
                strategy_tag = _CANDIDATE_STRATEGY_TAGS[
                    index % len(_CANDIDATE_STRATEGY_TAGS)
                ]
            else:
                strategy_tag = "baseline"
            schema_directive = (
                _schema_directive(index, path_state.get("entity_columns"))
                if n_candidates > 1
                else ""
            )
            method_meta: dict[str, Any] = {
                "method": strategy_tag,
                "schema_role": (
                    _SCHEMA_SLOT_ROLES[index % len(_SCHEMA_SLOT_ROLES)]
                    if schema_directive
                    else "free"
                ),
                "stage1_ok": index == 0,
                "stage2_ok": False,
                "artifact_chars": 0,
                "artifact_preview": "",
                "synthetic_generated": 0,
                "synthetic_execution_valid": 0,
            }

            def invoke_stage1(stage1_messages: list, artifact_schema):
                try:
                    artifact = safe_invoke_with_structured_output(
                        client, stage1_messages, artifact_schema
                    )
                    if artifact is None:
                        method_meta["stage1_error"] = "returned_none"
                    return artifact
                except Exception as exc:
                    method_meta["stage1_error"] = (f"{type(exc).__name__}: {exc}")[:240]
                    return None

            # Baseline remains a one-call path. Every other method below uses
            # two independent invocations, not a longer thought field in the
            # same response.
            if strategy_tag == "baseline":
                messages = build_messages(
                    tables_variant,
                    similar_questions_variant=few_shot_variant,
                    schema_directive=schema_directive,
                    strategy="",
                )
            elif strategy_tag.startswith("query_plan"):
                stage1_messages = build_messages(
                    tables_variant,
                    similar_questions_variant=few_shot_variant,
                    schema_directive=schema_directive,
                    strategy=_QUERY_PLAN_ARTIFACT_PROMPT,
                )
                artifact = invoke_stage1(stage1_messages, SQLQueryPlanModel)
                if artifact is None:
                    method_meta["elapsed_s"] = round(time.monotonic() - started, 3)
                    return (
                        None,
                        stage1_messages,
                        few_shot_indices,
                        strategy_tag,
                        method_meta,
                    )
                plan_text = artifact.plan
                method_meta.update(
                    stage1_ok=True,
                    artifact_chars=len(plan_text),
                    artifact_preview=plan_text[:800],
                )
                messages = build_messages(
                    tables_variant,
                    similar_questions_variant=few_shot_variant,
                    schema_directive=schema_directive,
                    strategy=_QUERY_PLAN_TRANSLATION_PROMPT.format(artifact=plan_text),
                )
            elif strategy_tag == "decomposition":
                tree_mode = _decomposition_tree_enabled()
                stage1_messages = build_messages(
                    tables_variant,
                    similar_questions_variant=few_shot_variant,
                    schema_directive=schema_directive,
                    strategy=(
                        _DECOMPOSITION_TREE_ARTIFACT_PROMPT
                        if tree_mode
                        else _DECOMPOSITION_ARTIFACT_PROMPT
                    ),
                )
                artifact = invoke_stage1(
                    stage1_messages,
                    SQLDecompositionTreeModel if tree_mode else SQLDecompositionModel,
                )
                if artifact is None:
                    method_meta["elapsed_s"] = round(time.monotonic() - started, 3)
                    return (
                        None,
                        stage1_messages,
                        few_shot_indices,
                        strategy_tag,
                        method_meta,
                    )
                if tree_mode:
                    decomposition_text = _format_decomposition_tree(artifact)
                else:
                    decomposition_text = "\n".join(
                        f"{i}. {item}"
                        for i, item in enumerate(artifact.sub_questions, 1)
                    )
                    decomposition_text += f"\nComposition: {artifact.composition}"
                method_meta.update(
                    stage1_ok=True,
                    artifact_chars=len(decomposition_text),
                    artifact_preview=decomposition_text[:800],
                )
                messages = build_messages(
                    tables_variant,
                    similar_questions_variant=few_shot_variant,
                    schema_directive=schema_directive,
                    strategy=(
                        _DECOMPOSITION_TREE_TRANSLATION_PROMPT
                        if tree_mode
                        else _DECOMPOSITION_TRANSLATION_PROMPT
                    ).format(artifact=decomposition_text),
                )
            elif strategy_tag == "synthetic_examples":
                # Do not expose cross-database examples while inventing the
                # same-schema demonstrations; they would anchor the generator
                # back onto the very signal this method is meant to replace.
                with_reasoning = _synthetic_reasoning_enabled()
                stage1_messages = build_messages(
                    tables_variant,
                    similar_questions_variant=[],
                    schema_directive=schema_directive,
                    strategy=(
                        _SYNTHETIC_ARTIFACT_PROMPT_WITH_REASONING
                        if with_reasoning
                        else _SYNTHETIC_ARTIFACT_PROMPT
                    ),
                )
                artifact = invoke_stage1(stage1_messages, SyntheticSQLExamplesModel)
                if artifact is None:
                    method_meta["elapsed_s"] = round(time.monotonic() - started, 3)
                    return (
                        None,
                        stage1_messages,
                        few_shot_indices,
                        strategy_tag,
                        method_meta,
                    )
                method_meta["stage1_ok"] = True
                method_meta["synthetic_generated"] = len(artifact.examples)

                # A generated example is instruction, not just decoration. An
                # invalid example can actively teach the final call a bogus
                # table/column, so execute each one and inject only examples
                # that the target database accepts.
                synthetic_examples: list[tuple[str, ...]] = []
                rejected_errors: list[str] = []
                for example in artifact.examples:
                    checked = _run_sql(example.sql, connector)
                    if checked.error:
                        rejected_errors.append(checked.error[:160])
                    elif with_reasoning:
                        synthetic_examples.append(
                            (
                                example.question,
                                example.sql,
                                getattr(example, "reasoning", "") or "",
                            )
                        )
                    else:
                        synthetic_examples.append((example.question, example.sql))
                method_meta["synthetic_execution_valid"] = len(synthetic_examples)
                method_meta["synthetic_rejected_errors"] = rejected_errors
                synthetic_text = "\n\n".join(
                    (
                        f"question: {x[0]}\nreasoning: {x[2]}\nanswer: {x[1]}"
                        if len(x) > 2 and str(x[2]).strip()
                        else f"question: {x[0]}\nanswer: {x[1]}"
                    )
                    for x in synthetic_examples
                )
                method_meta.update(
                    artifact_chars=len(synthetic_text),
                    artifact_preview=synthetic_text[:800],
                )
                if not synthetic_examples:
                    method_meta["elapsed_s"] = round(time.monotonic() - started, 3)
                    return (
                        None,
                        stage1_messages,
                        few_shot_indices,
                        strategy_tag,
                        method_meta,
                    )
                messages = build_messages(
                    tables_variant,
                    similar_questions_variant=synthetic_examples,
                    schema_directive=schema_directive,
                    strategy=_SYNTHETIC_USE_PROMPT,
                )
            elif strategy_tag == "alt_table_set":
                # One-shot SQL with a hard table-set divergence directive. Goal:
                # fail on different questions than slot 0 (independence), not just
                # different SQL text for the same wrong reading.
                alt_directive = (
                    (schema_directive + "\n\n" if schema_directive else "")
                    + _ALT_TABLE_SET_STRATEGY
                )
                messages = build_messages(
                    tables_variant,
                    similar_questions_variant=few_shot_variant,
                    schema_directive=alt_directive,
                    strategy="",
                )
            else:
                raise AssertionError(f"Unknown candidate method: {strategy_tag}")

            try:
                response = safe_invoke_with_structured_output(
                    client, messages, SQLGenerationModel
                )
            except Exception as e:
                self.logger.error(
                    "LLM structured output failed (candidate %d): %s: %s",
                    index,
                    type(e).__name__,
                    e,
                    exc_info=True,
                )
                method_meta["stage2_error"] = f"{type(e).__name__}: {e}"[:240]
                method_meta["elapsed_s"] = round(time.monotonic() - started, 3)
                return (
                    None,
                    messages,
                    few_shot_indices,
                    strategy_tag,
                    method_meta,
                )
            method_meta["stage2_ok"] = response is not None and bool(
                (getattr(response, "sql_code", "") or "").strip()
            )
            method_meta["elapsed_s"] = round(time.monotonic() - started, 3)
            if response and hasattr(response, "response") and response.response:
                self.logger.info(
                    "LLM candidate %d [%s] generated: %s...",
                    index,
                    strategy_tag,
                    response.response[:100],
                )
            return (
                response,
                messages,
                few_shot_indices,
                strategy_tag,
                method_meta,
            )

        n_candidates = _num_candidates()
        candidate_temp = _candidate_temperature()

        candidates: list = []
        messages: list = []
        if n_candidates < 2:
            # Single-candidate path: preserve the original retry-on-None behavior.
            MAX_RETRIES = 3
            response = None
            for attempt in range(1, MAX_RETRIES + 1):
                response, messages, _, _, _ = generate_candidate(0)
                if response is not None:
                    break
                self.logger.warning(
                    "LLM returned None on attempt %d/%d — retrying.",
                    attempt,
                    MAX_RETRIES,
                )
            if response is not None:
                candidates = [response]
        else:
            # Fan out N candidates in parallel using the house ThreadPoolExecutor
            # pattern. The global _INFLIGHT semaphore in llm_invoke caps real
            # endpoint concurrency, so this stays polite regardless of N.
            results: list = [None] * n_candidates
            candidate_prompt_meta: list[dict] = [{} for _ in range(n_candidates)]
            base_messages: list = []
            with ThreadPoolExecutor(max_workers=min(n_candidates, 4)) as pool:
                futures = {
                    pool.submit(generate_candidate, i): i for i in range(n_candidates)
                }
                for fut in as_completed(futures):
                    i = futures[fut]
                    resp, msgs, few_shot_indices, strategy_tag, method_meta = (
                        fut.result()
                    )
                    results[i] = resp
                    candidate_prompt_meta[i] = {
                        "few_shot_indices": few_shot_indices,
                        "strategy_tag": strategy_tag,
                    }
                    if i == 0:
                        base_messages = msgs
            candidates = [
                r
                for r in results
                if r is not None and r.sql_code and r.sql_code.strip()
            ]
            messages = base_messages
            self.logger.info(
                "Generated %d/%d usable SQL candidates.",
                len(candidates),
                n_candidates,
            )

        response = candidates[0] if candidates else None

        if response is None:
            self.logger.error("No usable SQL candidate produced.")
            return {
                "path_state": {
                    **path_state,
                    "unconstructable_explanation": "LLM failed to produce a response.",
                },
                "decision": "unconstructable",
            }

        # Filter each candidate's custom_analyses_used down to retrieved candidate
        # IDs, so whichever candidate the selection node picks carries clean refs.
        candidate_ids = {
            c.get("id") if isinstance(c, dict) else getattr(c, "id", None)
            for c in path_state.get("custom_analyses", [])
        }

        def _filter_custom_analyses(resp) -> None:
            if getattr(resp, "custom_analyses_used", None):
                resp.custom_analyses_used = [
                    elem
                    for elem in resp.custom_analyses_used
                    if (elem.id if hasattr(elem, "id") else elem.get("id"))
                    in candidate_ids
                ]

        # Check if we have a valid response (either SQL or text-based answer from file contents)
        has_sql = bool(response.sql_code and response.sql_code.strip())
        has_response = bool(response.response and response.response.strip())

        if has_sql:
            for cand in candidates:
                _filter_custom_analyses(cand)
            custom_analyses_used = (
                get_custom_analyses_ids(response.custom_analyses_used)
                if getattr(response, "custom_analyses_used", None)
                else []
            )

            return {
                "messages": messages,  # Don't add formatted response here - formatting agent will do it
                "path_state": {
                    **path_state,
                    "sql_generation_result": response,  # Keep as object (Pydantic model)
                    "sql_candidates": candidates,  # Pool for the selection node
                    "relevant_tables": relevant_tables if has_sql else [],
                    "custom_analyses_used": custom_analyses_used,
                },
                "decision": "constructable",
            }
        elif has_response:
            custom_analyses_used = []
            if hasattr(response, "custom_analyses_used"):
                response.response += build_custom_analyses_section(
                    response.custom_analyses_used, path_state.get("custom_analyses", [])
                )
                custom_analyses_used = get_custom_analyses_ids(
                    response.custom_analyses_used
                )

            return {
                "messages": messages + [AIMessage(content=response.response)],
                "path_state": {
                    **path_state,
                    "sql_generation_result": response,
                    "relevant_tables": relevant_tables if has_sql else [],
                    "custom_analyses_used": custom_analyses_used,
                },
                "decision": "constructable",
            }
        else:
            # SQL could not be generated
            return {
                "path_state": {
                    **path_state,
                    "unconstructable_explanation": response.response
                    or "Unable to construct response.",
                },
                "decision": "unconstructable",
            }
