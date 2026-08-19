# SPDX-FileCopyrightText: Copyright (c) 2026, NVIDIA CORPORATION & AFFILIATES.
# All rights reserved.
# SPDX-License-Identifier: Apache-2.0

"""Retrieve Train Q→SQL few-shots from the ``train_qa`` VDB for text-to-SQL ICL.

Flow:
1. Embed a masked form of the question and recall a larger candidate pool
   (``BIRD_FEW_SHOT_RETRIEVE_K``, default 40) from ``train_qa``.
2. Optionally rerank those candidates with a NIM cross-encoder on
   ``(question, train_question)``. Whether that happens is decided by
   :func:`gsf.utils.rerank.rerank_enabled`, which reads ``BIRD_FEW_SHOT_RERANK``.
3. Keep the final top-k (``BIRD_FEW_SHOT_K``) for the prompt.

Every flag named above is declared with its default in :mod:`gsf.flags`.

BIRD train/dev databases are disjoint, so we cannot filter by ``db_id``; the
reranker is what picks the most semantically similar cross-DB demos.
"""

from __future__ import annotations

import logging
from typing import Any

from gsf import flags
from gsf.retrieval.data_access.semantic_search import search_semantic_index
from gsf.retrieval.text_to_sql.question_masking import mask_question
from gsf.semantic.constants import FEW_SHOT_DATABASE_NAME, LABEL_FEW_SHOT_QA

logger = logging.getLogger(__name__)


def _dedupe_pairs(rows: list[dict]) -> list[tuple[str, str, str]]:
    """Return unique ``(question, sql, db_id)`` triples from VDB hits."""
    examples: list[tuple[str, str, str]] = []
    seen: set[str] = set()
    for row in rows:
        q = str(row.get("question") or row.get("name") or "").strip()
        sql = str(row.get("sql") or "").strip()
        db_id = str(row.get("db_id") or "").strip()
        if not q or not sql:
            continue
        key = f"{q}\n{sql}"
        if key in seen:
            continue
        seen.add(key)
        examples.append((q, sql, db_id))
    return examples


def _rerank_pairs(
    question: str,
    pairs: list[tuple[str, str, str]],
    k: int,
) -> tuple[list[tuple[str, str, str]], bool, list[float]]:
    """Rerank *pairs* by train-question relevance; return top-*k* + meta."""
    from gsf.utils.rerank import rerank_enabled, rerank_passages

    if not rerank_enabled() or len(pairs) <= 1:
        return pairs[:k], False, []

    passages = [q for q, _, _ in pairs]
    ranked = rerank_passages(question, passages, top_n=k)
    if not ranked:
        return pairs[:k], False, []

    out: list[tuple[str, str, str]] = []
    logits: list[float] = []
    for idx, logit in ranked:
        out.append(pairs[idx])
        logits.append(logit)
    return out, True, logits


def fetch_similar_questions(
    question: str,
    k: int | None = None,
    *,
    retriever: Any | None = None,
    retrieve_k: int | None = None,
) -> list[tuple[str, str]]:
    """Return up to *k* ``(question, sql)`` demos most similar to *question*.

    Recalls ``retrieve_k`` (default ``BIRD_FEW_SHOT_RETRIEVE_K``) neighbors from
    ``train_qa`` via embedding search, optionally reranks them with a NIM
    cross-encoder, then keeps the final top-*k* (default ``BIRD_FEW_SHOT_K``).
    Returns ``[]`` when disabled, missing, or on error.
    """
    if not flags.FEW_SHOT() or not (question or "").strip():
        return []

    k = flags.FEW_SHOT_K() if k is None else k
    pool_k = max(
        k, retrieve_k if retrieve_k is not None else flags.FEW_SHOT_RETRIEVE_K()
    )

    try:
        from gsf.utils.retriever import get_train_qa_retriever

        train_qa_retriever = retriever or get_train_qa_retriever()
        masked = mask_question(question)
        rows = search_semantic_index(
            train_qa_retriever,
            masked or question,
            label_filter=[LABEL_FEW_SHOT_QA],
            per_label_k={LABEL_FEW_SHOT_QA: pool_k},
            database_name=FEW_SHOT_DATABASE_NAME,
        )
    except Exception:
        logger.warning("fetch_similar_questions: train_qa search failed", exc_info=True)
        return []

    pairs = _dedupe_pairs(rows)
    selected, used_rerank, _logits = _rerank_pairs(question, pairs, k)

    logger.info(
        "Retrieved %d Train few-shot example(s) (pool=%d, retrieve_k=%d, reranked=%s).",
        len(selected),
        len(pairs),
        pool_k,
        used_rerank,
    )
    return [(q, sql) for q, sql, _ in selected]
