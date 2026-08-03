# SPDX-FileCopyrightText: Copyright (c) 2026, NVIDIA CORPORATION & AFFILIATES.
# All rights reserved.
# SPDX-License-Identifier: Apache-2.0

"""
Candidate Retrieval Agent

Batch-embeds all query texts once, searches the semantic VDB per label bucket,
reranks each bucket independently, and stores typed results in path_state.

Responsibilities:
- Embed the question and every entity in a single HTTP round-trip.
- Search CustomAnalysis / SqlAttribute with the question vector.
- Search ColumnAttribute once per entity (batched under one label filter).
- Rerank CustomAnalysis and SqlAttribute hits against the full question.
- Merge and deduplicate all entity ColumnAttribute hits, then rerank them once
  against a query containing the full question and all entities.
- Store the resulting candidates in path_state.
"""

import logging

from concurrent.futures import ThreadPoolExecutor, as_completed
from typing import Any, Dict

from nemo_retriever.tabular_data.ingestion.model.reserved_words import Labels
from nemo_retriever.operators.rerank import rerank_hits

from gsf.semantic.constants import LABEL_COLUMN_ATTRIBUTE, LABEL_SQL_ATTRIBUTE

from gsf.retrieval.data_access.semantic_search import search_semantic_index_by_vectors
from gsf.retrieval.text_to_sql.base import BaseAgent
from gsf.retrieval.text_to_sql.state import (
    AgentState,
    get_question_for_processing,
)
from gsf.utils.embedding import embed_query_texts
from gsf.utils.rerank import get_rerank_kwargs

logger = logging.getLogger(__name__)


_CAND_RETRIEVE_K = 10
_CAND_KEEP_K = 3
_MAX_VECTOR_DISTANCE = 0.7


# ---------------------------------------------------------------------------
# Search / rerank helpers
# ---------------------------------------------------------------------------


def _task_tag(label: str, search_query: str) -> str:
    """Short identifier for log lines, since tasks run concurrently."""
    query = search_query if len(search_query) <= 40 else f"{search_query[:37]}…"
    return f"{label}/{query!r}"


def _column_attribute_rerank_query(question: str, entities: list[str]) -> str:
    """Build the single query used to rerank all ColumnAttribute hits."""
    return f"Question: {question}\nEntities: {', '.join(entities)}"


def _rerank_hits(
    hits: list[dict],
    search_query: str,
    label: str,
    keep_k: int,
) -> list[dict]:
    """Rerank pre-fetched hits once and keep the best *keep_k*."""
    tag = _task_tag(label, search_query)
    if len(hits) <= 2:
        logger.info(
            "%s rerank skipped — only %d hit(s), keeping vector order",
            tag,
            len(hits),
        )
        return hits[:keep_k]

    try:
        ranked: list[dict] | None = list(
            rerank_hits(
                search_query,
                hits,
                top_n=len(hits),
                **get_rerank_kwargs(),
            )
        )
    except Exception:
        logger.warning(
            "%s rerank failed for query %r",
            label,
            search_query,
            exc_info=True,
        )
        ranked = None
    return (ranked or hits)[:keep_k]


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


def _merge_column_attribute_hits(
    hits_per_entity: list[list[dict]],
    entities: list[str],
) -> tuple[list[dict], dict[str, set[str]]]:
    """Deduplicate hits while retaining which entities retrieved each hit."""
    matched_entities: dict[str, set[str]] = {}
    all_hits: list[dict] = []
    for entity, hits in zip(entities, hits_per_entity, strict=False):
        for hit in hits:
            hit_id = hit.get("id")
            if hit_id is None:
                continue
            matched_entities.setdefault(str(hit_id), set()).add(entity)
            all_hits.append(hit)
    return _dedupe_best_score(all_hits), matched_entities


def _select_column_attributes_with_entity_coverage(
    ranked_hits: list[dict],
    matched_entities: dict[str, set[str]],
    entities: list[str],
    keep_k: int,
) -> list[dict]:
    """Keep one distinct candidate per entity when possible, then fill by rank."""
    selected: list[dict] = []
    selected_ids: set[str] = set()

    for entity in entities:
        candidate = next(
            (
                hit
                for hit in ranked_hits
                if str(hit.get("id")) not in selected_ids
                and entity in matched_entities.get(str(hit.get("id")), set())
            ),
            None,
        )
        if candidate is not None:
            selected.append(candidate)
            selected_ids.add(str(candidate.get("id")))

    target_count = max(keep_k, len(selected))
    for hit in ranked_hits:
        hit_id = str(hit.get("id"))
        if hit_id not in selected_ids:
            selected.append(hit)
            selected_ids.add(hit_id)
        if len(selected) >= target_count:
            break

    for hit in selected:
        hit["_matched_entities"] = sorted(
            matched_entities.get(str(hit.get("id")), set())
        )
    return selected


def _search_label_bucket(
    retriever: object,
    vectors: list[list[float]],
    label: str,
    top_k: int,
    database_name: str | None,
) -> list[list[dict]]:
    """Run one pgvector search for *vectors* under a single label filter."""
    try:
        results = search_semantic_index_by_vectors(
            retriever,
            vectors,
            label=label,
            top_k=top_k,
            database_name=database_name,
        )
    except Exception:
        logger.warning(
            "%s vector search failed for %d quer(y/ies)",
            label,
            len(vectors),
            exc_info=True,
        )
        return [[] for _ in vectors]

    filtered_results: list[list[dict]] = []
    for hits in results:
        kept = [
            hit
            for hit in hits
            if float(hit.get("score", float("inf"))) <= _MAX_VECTOR_DISTANCE
        ]
        if not kept and hits and label == LABEL_COLUMN_ATTRIBUTE:
            # Per-entity coverage takes precedence over the distance threshold:
            # preserve the closest vector hit when an entity has no hit <= 0.7.
            kept = [
                min(
                    hits,
                    key=lambda hit: float(hit.get("score", float("inf"))),
                )
            ]
        filtered_results.append(kept)
    return filtered_results


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

        if semantic_retriever is not None and question:
            clean_entities = [e.strip() for e in entities if (e or "").strip()]

            # Phase 1: one embed round-trip for the question + every entity.
            # Identical strings are deduped inside embed_query_texts, so the
            # question is not paid twice when CustomAnalysis and SqlAttribute
            # both need it.
            embed_texts = [question, *clean_entities]
            try:
                vectors = embed_query_texts(embed_texts)
            except Exception:
                self.logger.warning(
                    "Batch query embed failed — skipping candidate retrieval",
                    exc_info=True,
                )
                vectors = []

            if vectors:
                question_vector = vectors[0]
                entity_vectors = vectors[1:]

                # Phase 2: three label-filtered pgvector searches. Entity
                # ColumnAttribute queries share a filter so they ride one call.
                search_jobs: list[tuple[str, list[list[float]], str]] = [
                    ("custom", [question_vector], Labels.CUSTOM_ANALYSIS),
                    ("sql_attr", [question_vector], LABEL_SQL_ATTRIBUTE),
                ]
                if entity_vectors:
                    search_jobs.append(
                        ("col_attr", entity_vectors, LABEL_COLUMN_ATTRIBUTE)
                    )

                bucket_hits: dict[str, list[list[dict]]] = {}

                with ThreadPoolExecutor(max_workers=len(search_jobs) or 1) as pool:
                    futures = {
                        pool.submit(
                            _search_label_bucket,
                            semantic_retriever,
                            vecs,
                            label,
                            _CAND_RETRIEVE_K,
                            target_db,
                        ): key
                        for key, vecs, label in search_jobs
                    }
                    for future in as_completed(futures):
                        bucket_hits[futures[future]] = future.result()

                custom_raw = (bucket_hits.get("custom") or [[]])[0]
                sql_attr_raw = (bucket_hits.get("sql_attr") or [[]])[0]
                col_attr_raw_per_entity = bucket_hits.get("col_attr") or [
                    [] for _ in clean_entities
                ]

                # Phase 3: merge the per-entity ColumnAttribute buckets and
                # deduplicate before one global rerank. The combined query
                # carries both the user's intent and every extracted entity.
                merged_col_attr_raw, col_attr_matched_entities = (
                    _merge_column_attribute_hits(
                        col_attr_raw_per_entity,
                        clean_entities,
                    )
                )
                col_attr_query = _column_attribute_rerank_query(
                    question, clean_entities
                )
                rerank_jobs: list[tuple[str, Any]] = [
                    (
                        "custom",
                        (custom_raw, question, Labels.CUSTOM_ANALYSIS, _CAND_KEEP_K),
                    ),
                    (
                        "sql_attr",
                        (
                            sql_attr_raw,
                            question,
                            LABEL_SQL_ATTRIBUTE,
                            _CAND_KEEP_K,
                        ),
                    ),
                    (
                        "col_attr",
                        (
                            merged_col_attr_raw,
                            col_attr_query,
                            LABEL_COLUMN_ATTRIBUTE,
                            len(merged_col_attr_raw),
                        ),
                    ),
                ]

                with ThreadPoolExecutor(max_workers=len(rerank_jobs) or 1) as pool:
                    futures = {
                        pool.submit(_rerank_hits, *args): key
                        for key, args in rerank_jobs
                    }
                    for future in as_completed(futures):
                        key = futures[future]
                        result = future.result()
                        if key == "custom":
                            all_custom_hits = result
                        elif key == "sql_attr":
                            all_sql_attr_hits = result
                        else:
                            all_col_attr_hits = (
                                _select_column_attributes_with_entity_coverage(
                                    result,
                                    col_attr_matched_entities,
                                    clean_entities,
                                    _CAND_KEEP_K,
                                )
                            )

        path_state["retrieved_column_attributes"] = all_col_attr_hits
        path_state["retrieved_custom_analyses"] = all_custom_hits
        path_state["retrieved_sql_attributes"] = all_sql_attr_hits

        self.logger.info(
            "Retrieved %d ColumnAttributes, %d CustomAnalysis, "
            "and %d SqlAttribute candidates (%d entities queried)",
            len(all_col_attr_hits),
            len(all_custom_hits),
            len(all_sql_attr_hits),
            len(entities),
        )

        return {"path_state": path_state}
