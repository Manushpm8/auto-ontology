# SPDX-FileCopyrightText: Copyright (c) 2026, NVIDIA CORPORATION & AFFILIATES.
# All rights reserved.
# SPDX-License-Identifier: Apache-2.0

"""Rerank the SQL result rows by relevance to the original question.

Uses the NeMo reranking endpoint (``nvidia/llama-nemotron-rerank-1b-v2``) to
score each result row against the user's original question. Rows are sent in
batches, scored, and the full ``sql_results`` list is re-ordered highest
relevance first before being stored back on the state.
"""

from typing import Any, Dict, List

from nemo_retriever.operators.rerank import rerank_hits

from gsf.retrieval.rerank.state import RerankState, get_original_question
from gsf.retrieval.text_to_sql.base import BaseAgent
from gsf.utils.rerank import get_rerank_kwargs

# Number of result rows sent to the reranking endpoint per request.
_RERANK_BATCH_SIZE = 50

_INDEX_KEY = "_rerank_idx"
_TEXT_KEY = "_rerank_text"
_SCORE_KEY = "_rerank_score"


def _row_to_text(row: Dict[str, Any]) -> str:
    """Flatten a result row into a single passage for the reranker."""
    parts: List[str] = []
    for key, value in row.items():
        if value is None or value == "":
            continue
        parts.append(f"{key}: {value}")
    return " | ".join(parts)


def _chunk(items: List[Any], size: int) -> List[List[Any]]:
    """Split *items* into consecutive chunks of at most *size*."""
    return [items[i : i + size] for i in range(0, len(items), size)]


class RerankSqlResultsAgent(BaseAgent):
    """Re-order ``sql_results`` by relevance to the original question."""

    def __init__(self):
        super().__init__("rerank_sql_results")

    def validate_input(self, state: RerankState) -> bool:
        """Only run when there are rows to rerank."""
        path_state = state.get("path_state", {})
        if not (path_state.get("sql_results") or []):
            self.logger.info("No SQL results to rerank, skipping")
            return False
        return True

    def execute(self, state: RerankState) -> Dict[str, Any]:
        """Score rows in batches and reorder ``sql_results`` by relevance."""
        path_state = state.get("path_state", {})
        result: Dict[str, Any] = {"path_state": path_state}

        question = get_original_question(state)
        rows: List[Dict[str, Any]] = path_state.get("sql_results") or []

        if not question:
            self.logger.warning("No question available; leaving result order as-is")
            return result

        try:
            rerank_kwargs = get_rerank_kwargs()
        except EnvironmentError as e:
            self.logger.warning("Reranker not configured (%s); keeping order", e)
            return result

        scores: Dict[int, float] = {}
        for batch in _chunk(list(range(len(rows))), _RERANK_BATCH_SIZE):
            hits = [
                {_INDEX_KEY: idx, _TEXT_KEY: _row_to_text(rows[idx])} for idx in batch
            ]
            try:
                ranked = rerank_hits(
                    question,
                    hits,
                    text_key=_TEXT_KEY,
                    **rerank_kwargs,
                )
            except Exception:
                self.logger.exception("Reranking batch failed; keeping original order")
                return result
            for hit in ranked:
                scores[hit[_INDEX_KEY]] = hit.get(_SCORE_KEY, float("-inf"))

        reordered = sorted(
            range(len(rows)),
            key=lambda idx: scores.get(idx, float("-inf")),
            reverse=True,
        )
        path_state["sql_results"] = [rows[idx] for idx in reordered]
        self.logger.info("Reranked %d result row(s) by relevance", len(rows))
        return result
