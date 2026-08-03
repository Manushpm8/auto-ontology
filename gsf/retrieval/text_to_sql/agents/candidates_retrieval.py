# SPDX-FileCopyrightText: Copyright (c) 2026, NVIDIA CORPORATION & AFFILIATES.
# All rights reserved.
# SPDX-License-Identifier: Apache-2.0

"""
Candidate Retrieval Agent

Searches the semantic VDB, reranks each search independently, and stores typed
results in path_state.

Responsibilities:
- Search the semantic VDB (ontology_retriever) for ColumnAttribute candidates.
- Search the semantic VDB (semantic_retriever) for CustomAnalysis candidates.
- Rerank each entity's ColumnAttribute hits against that entity.
- Rerank CustomAnalysis and SqlAttribute hits against the full question.
- Deduplicate ColumnAttribute hits across entities and store results in path_state.
"""

import logging

from concurrent.futures import ThreadPoolExecutor, as_completed
from typing import Any, Dict

from nemo_retriever.tabular_data.ingestion.model.reserved_words import Labels
from nemo_retriever.operators.rerank import rerank_hits

from gsf.semantic.constants import LABEL_COLUMN_ATTRIBUTE, LABEL_SQL_ATTRIBUTE

from gsf.retrieval.data_access.semantic_search import search_semantic_index
from gsf.retrieval.text_to_sql.base import BaseAgent
from gsf.retrieval.text_to_sql.models import ColumnAttributeSpec
from gsf.retrieval.text_to_sql.state import (
    AgentState,
    get_question_for_processing,
)
from gsf.utils.rerank import get_rerank_kwargs

logger = logging.getLogger(__name__)


_CAND_RETRIEVE_K = 10
_CAND_KEEP_K = 3
_RRF_K = 60


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


def _search_and_rerank(
    retriever: object,
    search_query: str,
    label: str,
    retrieve_k: int,
    keep_k: int,
    database_name: str | None = None,
    question: str | None = None,
) -> list[dict]:
    """Search one label and keep the best reranked hits.

    When *question* is set, *search_query* is treated as the entity text:
    rerank the retrieved set independently against the entity and against the
    question, then combine ranks with equal-weight Reciprocal Rank Fusion.
    Otherwise rerank once against *search_query* alone.
    """
    hits = _search_by_label(
        retriever,
        search_query,
        label,
        retrieve_k,
        database_name,
    )
    if not hits:
        return []

    def _rerank(rerank_query: str) -> list[dict] | None:
        try:
            return list(
                rerank_hits(
                    rerank_query,
                    hits,
                    top_n=len(hits),
                    **get_rerank_kwargs(),
                )
            )
        except Exception:
            logger.warning(
                "%s rerank failed for query %r",
                label,
                rerank_query,
                exc_info=True,
            )
            return None

    if question is None:
        ranked = _rerank(search_query)
        return (ranked or hits)[:keep_k]

    entity = search_query
    entity_ranked = _rerank(entity)
    question_ranked = _rerank(question)
    if entity_ranked is None and question_ranked is None:
        logger.warning(
            "%s entity/question reranks failed — keeping top %d vector hit(s)",
            label,
            keep_k,
        )
        return hits[:keep_k]
    if entity_ranked is None:
        assert question_ranked is not None
        return question_ranked[:keep_k]
    if question_ranked is None:
        return entity_ranked[:keep_k]

    entity_by_id = {
        str(hit["id"]): (rank, hit)
        for rank, hit in enumerate(entity_ranked, start=1)
        if hit.get("id") is not None
    }
    question_by_id = {
        str(hit["id"]): (rank, hit)
        for rank, hit in enumerate(question_ranked, start=1)
        if hit.get("id") is not None
    }

    fused: list[dict] = []
    for hit_id in entity_by_id.keys() | question_by_id.keys():
        entity_entry = entity_by_id.get(hit_id)
        question_entry = question_by_id.get(hit_id)
        entity_score = (
            float(entity_entry[1].get("_rerank_score"))
            if entity_entry and entity_entry[1].get("_rerank_score") is not None
            else None
        )
        question_score = (
            float(question_entry[1].get("_rerank_score"))
            if question_entry and question_entry[1].get("_rerank_score") is not None
            else None
        )
        rrf_score = (1 / (_RRF_K + entity_entry[0]) if entity_entry else 0) + (
            1 / (_RRF_K + question_entry[0]) if question_entry else 0
        )
        source_hit = entity_entry[1] if entity_entry is not None else question_entry[1]
        fused_hit = dict(source_hit)
        fused_hit["_entity_rerank_score"] = entity_score
        fused_hit["_question_rerank_score"] = question_score
        fused_hit["_rerank_score"] = rrf_score
        fused.append(fused_hit)

    return sorted(
        fused,
        key=lambda hit: (
            -float(hit["_rerank_score"]),
            float(hit["score"]) if hit.get("score") is not None else float("inf"),
            str(hit.get("id") or ""),
        ),
    )[:keep_k]


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
# Agent
# ---------------------------------------------------------------------------


class CandidateRetrievalAgent(BaseAgent):
    """Retrieve ColumnAttribute, CustomAnalysis, and SqlAttribute candidates.

    - ColumnAttributes: searched per entity from the semantic VDB.
    - CustomAnalysis: searched once with the full question from the semantic VDB.
    - SqlAttribute: searched once with the full question from the semantic VDB.

    Deduplicate ColumnAttributes across entities and store:
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
        entities: list[str] = path_state.get("entities") or []
        semantic_retriever = state.get("semantic_retriever")
        target_db = path_state.get("target_db")

        all_col_attr_hits: list[dict] = []
        all_custom_hits: list[dict] = []
        all_sql_attr_hits: list[dict] = []

        if semantic_retriever is not None:
            clean_entities = [e.strip() for e in entities if (e or "").strip()]

            search_tasks: list[tuple[str, Any]] = [
                (
                    "custom",
                    (
                        semantic_retriever,
                        question,
                        Labels.CUSTOM_ANALYSIS,
                        _CAND_RETRIEVE_K,
                        _CAND_KEEP_K,
                        target_db,
                    ),
                ),
                (
                    "sql_attr",
                    (
                        semantic_retriever,
                        question,
                        LABEL_SQL_ATTRIBUTE,
                        _CAND_RETRIEVE_K,
                        _CAND_KEEP_K,
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
                            _CAND_RETRIEVE_K,
                            _CAND_KEEP_K,
                            target_db,
                            question,
                        ),
                    )
                    for entity in clean_entities
                ],
            ]

            with ThreadPoolExecutor(max_workers=len(search_tasks) or 1) as pool:
                futures = {
                    pool.submit(_search_and_rerank, *args): key
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
                        all_col_attr_hits.extend(result)

        # ColumnAttributes are searched per entity and merged, so the same
        # id can appear more than once; keep the best score. CustomAnalysis
        # and SqlAttribute are searched once each — no cross-query duplicates.
        deduped_col_attr = _dedupe_best_score(all_col_attr_hits)

        path_state["retrieved_column_attributes"] = deduped_col_attr
        path_state["retrieved_custom_analyses"] = all_custom_hits
        path_state["retrieved_sql_attributes"] = all_sql_attr_hits

        self.logger.info(
            "Retrieved %d ColumnAttributes, %d CustomAnalysis, "
            "and %d SqlAttribute candidates (%d entities queried)",
            len(deduped_col_attr),
            len(all_custom_hits),
            len(all_sql_attr_hits),
            len(entities),
        )

        return {"path_state": path_state}
