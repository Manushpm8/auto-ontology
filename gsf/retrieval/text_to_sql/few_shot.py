# SPDX-FileCopyrightText: Copyright (c) 2026, NVIDIA CORPORATION & AFFILIATES.
# All rights reserved.
# SPDX-License-Identifier: Apache-2.0

"""Retrieve Train Q→SQL few-shots from the ``train_qa`` VDB for text-to-SQL ICL."""

from __future__ import annotations

import logging
import os
from typing import Any

from gsf.retrieval.data_access.semantic_search import search_semantic_index
from gsf.retrieval.text_to_sql.question_masking import mask_question
from gsf.semantic.constants import FEW_SHOT_DATABASE_NAME, LABEL_FEW_SHOT_QA

logger = logging.getLogger(__name__)

_MAX_EXAMPLES = int(os.environ.get("BIRD_FEW_SHOT_K", "8"))
_ENABLED = os.environ.get("BIRD_FEW_SHOT", "1").strip().lower() not in {
    "0",
    "false",
    "no",
    "off",
}


def fetch_similar_questions(
    question: str,
    k: int = _MAX_EXAMPLES,
    *,
    retriever: Any | None = None,
) -> list[tuple[str, str]]:
    """Return up to *k* ``(question, sql)`` demos most similar to *question*.

    Searches the ``train_qa`` / ``FewShotQA`` corpus using a masked form of
    *question*. Returns ``[]`` when disabled, missing, or on error.
    """
    if not _ENABLED or not (question or "").strip():
        return []

    try:
        from gsf.utils.retriever import get_train_qa_retriever

        train_qa_retriever = retriever or get_train_qa_retriever()
        masked = mask_question(question)
        rows = search_semantic_index(
            train_qa_retriever,
            masked or question,
            label_filter=[LABEL_FEW_SHOT_QA],
            per_label_k={LABEL_FEW_SHOT_QA: k},
            database_name=FEW_SHOT_DATABASE_NAME,
        )
    except Exception:
        logger.warning("fetch_similar_questions: train_qa search failed", exc_info=True)
        return []

    examples: list[tuple[str, str]] = []
    seen: set[str] = set()
    for row in rows:
        q = str(row.get("question") or row.get("name") or "").strip()
        sql = str(row.get("sql") or "").strip()
        if not q or not sql:
            continue
        key = f"{q}\n{sql}"
        if key in seen:
            continue
        seen.add(key)
        examples.append((q, sql))
        if len(examples) >= k:
            break

    logger.info("Retrieved %d Train few-shot example(s).", len(examples))
    return examples
