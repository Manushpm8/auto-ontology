# SPDX-FileCopyrightText: Copyright (c) 2026, NVIDIA CORPORATION & AFFILIATES.
# All rights reserved.
# SPDX-License-Identifier: Apache-2.0

"""Split a multi-step request into ordered single-step questions.

Runs before the graph and deliberately is not a node in it: the sub-questions
it produces drive a loop *around* the graph, and a node's output cannot steer
the run that produced it. ``stream_agent_response`` calls this agent once, up
front, then replays the whole node graph per sub-question.
"""

from __future__ import annotations

import logging
from typing import Any, Dict

from langchain_core.messages import SystemMessage

from gsf.retrieval.text_to_sql.base import BaseAgent
from gsf.retrieval.text_to_sql.models import QuestionDecompositionModel
from gsf.retrieval.text_to_sql.prompts import (
    create_question_decomposition_prompt,
)
from gsf.retrieval.text_to_sql.state import AgentState, get_standalone_question
from gsf.utils.llm_invoke import invoke_with_structured_output

logger = logging.getLogger(__name__)

# A sub-question shorter than this is not a question, it is a fragment the model
# emitted while restating the input. Splitting on such an entry costs a full
# graph pass and contributes nothing, so the whole decomposition is discarded.
_MIN_SUB_QUESTION_CHARS = 12


def _clean(sub_questions: list[str], *, limit: int) -> list[str]:
    """Strip, drop blanks and duplicates, and cap the list at *limit*."""
    cleaned: list[str] = []
    seen: set[str] = set()
    for raw in sub_questions:
        text = (raw or "").strip()
        key = text.casefold()
        if not text or key in seen:
            continue
        seen.add(key)
        cleaned.append(text)
    return cleaned[:limit]


class QuestionDecompositionAgent(BaseAgent):
    """Emit ``path_state["sub_questions"]`` — ordered, single-step questions.

    Always returns at least one entry. A question that is already a single step
    (the common case) yields a one-element list holding the original question,
    even if the model paraphrased it.
    """

    def __init__(self, *, max_sub_questions: int = 5) -> None:
        super().__init__("question_decomposition")
        self._max_sub_questions = max_sub_questions

    def validate_input(self, state: AgentState) -> bool:
        if not get_standalone_question(state):
            self.logger.warning("No question found, skipping decomposition")
            return False
        return True

    def execute(self, state: AgentState) -> Dict[str, Any]:
        llm = state["llm"]
        path_state = state.get("path_state", {})
        question = get_standalone_question(state)
        glossary = state.get("glossary") or []
        evidence = state.get("evidence") or ""

        sub_questions = [question]
        try:
            messages = [
                SystemMessage(
                    content=create_question_decomposition_prompt(
                        question,
                        glossary,
                        evidence=evidence,
                        max_sub_questions=self._max_sub_questions,
                    )
                )
            ]
            decomposition = invoke_with_structured_output(
                llm, messages, QuestionDecompositionModel
            )
            if decomposition is None:
                self.logger.warning(
                    "Decomposition returned None, treating as a single step"
                )
            else:
                cleaned = _clean(
                    decomposition.sub_questions or [],
                    limit=self._max_sub_questions,
                )
                if not cleaned:
                    self.logger.warning(
                        "Decomposition returned no usable sub-questions, "
                        "treating as a single step"
                    )
                elif any(len(s) < _MIN_SUB_QUESTION_CHARS for s in cleaned):
                    # One unusable entry invalidates the split rather than just
                    # itself: the steps are a chain, so dropping a middle one
                    # leaves a later step referring to a value nothing computed.
                    self.logger.warning(
                        "Decomposition produced a fragment (%s), "
                        "falling back to a single step",
                        cleaned,
                    )
                elif len(cleaned) == 1:
                    # One step means the original request, even if the model
                    # paraphrased it. Rewrites belong in later nodes.
                    sub_questions = [question]
                    self.logger.info("Decomposed into 1 step(s):\n  1. %s", question)
                else:
                    sub_questions = cleaned
                    self.logger.info(
                        "Decomposed into %d step(s):\n%s",
                        len(cleaned),
                        "\n".join(f"  {i}. {s}" for i, s in enumerate(cleaned, 1)),
                    )
        except Exception as exc:
            self.logger.warning(
                "Decomposition failed: %s, treating as a single step", exc
            )

        path_state["sub_questions"] = sub_questions
        return {"path_state": path_state}


__all__ = ["QuestionDecompositionAgent"]
