# SPDX-FileCopyrightText: Copyright (c) 2026, NVIDIA CORPORATION & AFFILIATES.
# All rights reserved.
# SPDX-License-Identifier: Apache-2.0

"""
Candidate Retrieval Agent

Searches both VDBs per extracted entity, optionally reranks a larger recall
pool with the NIM cross-encoder against the full question, applies an LLM
intent filter on custom/sql hits, and stores typed results in path_state.

Responsibilities:
- Search the semantic VDB (ontology_retriever) for ColumnAttribute candidates.
- Search the semantic VDB (semantic_retriever) for CustomAnalysis candidates.
- Recall ``BIRD_CAND_RETRIEVE_K`` hits, rerank with NIM (``BIRD_CAND_RERANK``),
  keep ``BIRD_CAND_KEEP_K_*`` before the LLM filter.
- Deduplicate across entities and store results in path_state.
"""

import logging
import os

from concurrent.futures import ThreadPoolExecutor, as_completed
from typing import Any, Dict

from langchain_core.messages import SystemMessage

from nemo_retriever.tabular_data.ingestion.model.reserved_words import Labels

from gsf.semantic.constants import LABEL_COLUMN_ATTRIBUTE, LABEL_SQL_ATTRIBUTE

from gsf.retrieval.data_access.semantic_search import search_semantic_index
from gsf.utils.llm_invoke import invoke_with_structured_output
from gsf.retrieval.text_to_sql.base import BaseAgent
from gsf.retrieval.text_to_sql.evidence_hints import evidence_retrieval_phrases
from gsf.retrieval.text_to_sql.models import (
    CandidateFilterModel,
    ColumnAttributeSpec,
    CombinedCandidateFilterModel,
    CustomAnalysisFilterModel,
)
from gsf.retrieval.text_to_sql.state import (
    AgentState,
    get_original_question,
    get_question_for_processing,
)

logger = logging.getLogger(__name__)


def _env_int(name: str, default: int) -> int:
    try:
        return max(1, int(os.environ.get(name, str(default))))
    except (TypeError, ValueError):
        return default


# Embedding recall pool size (before rerank). Keep small enough that NIM
# rerank latency stays acceptable under parallel eval.
_CAND_RETRIEVE_K = _env_int("BIRD_CAND_RETRIEVE_K", 12)
_CAND_KEEP_COL = _env_int("BIRD_CAND_KEEP_K_COL", 3)
_CAND_KEEP_CUSTOM = _env_int("BIRD_CAND_KEEP_K_CUSTOM", 3)
_CAND_KEEP_SQL = _env_int("BIRD_CAND_KEEP_K_SQL", 3)
_CAND_RERANK = os.environ.get("BIRD_CAND_RERANK", "1").strip().lower() not in {
    "0",
    "false",
    "no",
    "off",
}


def _rerank_hits(
    question: str,
    hits: list[dict],
    *,
    keep_k: int,
    label: str,
) -> list[dict]:
    """Rerank VDB hits by full-question relevance; keep top *keep_k*.

    Falls back to embedding order (input order after score-sort) when rerank
    is disabled or the NIM call fails.
    """
    if not hits:
        return []
    if not _CAND_RERANK or len(hits) <= 1:
        return hits[:keep_k]

    from gsf.utils.rerank import rerank_passages

    passages = [
        (h.get("text") or h.get("name") or str(h.get("id") or "")).strip() for h in hits
    ]
    ranked = rerank_passages(question, passages, top_n=keep_k)
    if not ranked:
        return hits[:keep_k]

    reordered = [hits[idx] for idx, _logit in ranked if 0 <= idx < len(hits)]
    return reordered


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
    """Deduplicate by id, keeping the hit with the lowest score."""
    best: dict[str, dict] = {}
    for hit in hits:
        hid = hit.get("id")
        if hid is None:
            continue
        key = str(hid)
        prev = best.get(key)
        if prev is None or float(hit.get("score") or float("inf")) < float(
            prev.get("score") or float("inf")
        ):
            best[key] = hit
    return sorted(
        best.values(),
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
    """Retrieve ColumnAttribute, CustomAnalysis, and SqlAttribute candidates.

    - ColumnAttributes: searched per entity from the semantic VDB.
    - CustomAnalysis: searched once with the full question from the semantic VDB.
    - SqlAttribute: searched once with the full question from the semantic VDB.

    Deduplicate across entities and store:
    - ``path_state["retrieved_column_attributes"]``: ``list[dict]``
    - ``path_state["retrieved_custom_analyses"]``:   ``list[dict]``
    - ``path_state["retrieved_sql_attributes"]``:    ``list[dict]``
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
        original_question = get_original_question(state) or question
        entities: list[str] = list(path_state.get("entities") or [])
        # Evidence phrases (e.g. "frpm", "account.district_id") expand column recall.
        for phrase in evidence_retrieval_phrases(original_question):
            if phrase and phrase not in entities:
                entities.append(phrase)
        llm = state["llm"]
        semantic_retriever = state.get("semantic_retriever")
        target_db = path_state.get("target_db")

        all_col_attr_hits: list[dict] = []
        all_custom_hits: list[dict] = []
        all_sql_attr_hits: list[dict] = []

        if semantic_retriever is not None:
            clean_entities = [e.strip() for e in entities if (e or "").strip()]
            retrieve_k = _CAND_RETRIEVE_K

            # Prefer original question (includes Evidence) for custom/sql search.
            search_question = original_question or question

            search_tasks: list[tuple[str, Any]] = [
                (
                    "custom",
                    (
                        semantic_retriever,
                        search_question,
                        Labels.CUSTOM_ANALYSIS,
                        retrieve_k,
                        target_db,
                    ),
                ),
                (
                    "sql_attr",
                    (
                        semantic_retriever,
                        search_question,
                        LABEL_SQL_ATTRIBUTE,
                        retrieve_k,
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
                            retrieve_k,
                            target_db,
                        ),
                    )
                    for entity in clean_entities
                ],
            ]

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
                    else:
                        # Per-entity: recall large → rerank by full question →
                        # keep top-k (preserves multi-entity coverage).
                        entity = key.split(":", 1)[-1]
                        kept = _rerank_hits(
                            search_question,
                            result,
                            keep_k=_CAND_KEEP_COL,
                            label=f"column_attribute:{entity}",
                        )
                        all_col_attr_hits.extend(kept)

        # Custom/SQL: dedupe → rerank once against the question → keep.
        deduped_col_attr = _dedupe_best_score(all_col_attr_hits)
        deduped_custom = _dedupe_best_score(all_custom_hits)
        deduped_sql_attr = _dedupe_best_score(all_sql_attr_hits)

        search_question = original_question or question
        deduped_custom = _rerank_hits(
            search_question,
            deduped_custom,
            keep_k=_CAND_KEEP_CUSTOM,
            label="custom_analysis",
        )
        deduped_sql_attr = _rerank_hits(
            search_question,
            deduped_sql_attr,
            keep_k=_CAND_KEEP_SQL,
            label="sql_attribute",
        )

        # LLM intent filter on the (already-shrunk) custom/sql pools.
        deduped_custom, deduped_sql_attr = _llm_filter_both(
            llm, search_question, deduped_custom, deduped_sql_attr
        )

        path_state["retrieved_column_attributes"] = deduped_col_attr
        path_state["retrieved_custom_analyses"] = deduped_custom
        path_state["retrieved_sql_attributes"] = deduped_sql_attr

        self.logger.info(
            "Retrieved %d ColumnAttributes, %d CustomAnalysis, "
            "and %d SqlAttribute candidates (%d entities, recall=%d, rerank=%s)",
            len(deduped_col_attr),
            len(deduped_custom),
            len(deduped_sql_attr),
            len(entities),
            _CAND_RETRIEVE_K,
            _CAND_RERANK,
        )

        return {"path_state": path_state}
