# SPDX-FileCopyrightText: Copyright (c) 2026, NVIDIA CORPORATION & AFFILIATES.
# All rights reserved.
# SPDX-License-Identifier: Apache-2.0

"""Combined question sanitization + entity extraction (single LLM call)."""

from __future__ import annotations

import logging
from typing import Any, Dict

from langchain_core.messages import SystemMessage

from gsf.retrieval.entity_coverage.models import QuestionExtractionModel
from gsf.retrieval.entity_coverage.prompts import create_question_extraction_prompt
from gsf.retrieval.text_to_sql.base import BaseAgent
from gsf.retrieval.text_to_sql.state import AgentState, get_original_question
from gsf.utils.llm_invoke import invoke_with_structured_output

logger = logging.getLogger(__name__)


class QuestionExtractionAgent(BaseAgent):
    """Sanitize the question and extract entities in one structured LLM call."""

    def __init__(self) -> None:
        super().__init__("question_extraction")

    def validate_input(self, state: AgentState) -> bool:
        question = get_original_question(state)
        if not question:
            self.logger.warning("No question found, skipping question extraction")
            return False
        return True

    def execute(self, state: AgentState) -> Dict[str, Any]:
        llm = state["llm"]
        path_state = state.get("path_state", {})
        original_question = get_original_question(state)
        result: Dict[str, Any] = {"path_state": path_state}

        try:
            messages = [
                SystemMessage(
                    content=create_question_extraction_prompt(original_question)
                )
            ]
            extraction = invoke_with_structured_output(
                llm,
                messages,
                QuestionExtractionModel,
            )

            if extraction is None:
                self.logger.warning(
                    "Question extraction returned None, using original question"
                )
                path_state["normalized_question"] = original_question
                path_state["entities"] = [original_question]
                path_state["subject"] = original_question
                return result

            sanitized = (extraction.sanitized_question or "").strip()
            if not sanitized:
                self.logger.warning(
                    "Question extraction returned empty sanitize, using original"
                )
                sanitized = original_question

            entities = extraction.required_entity_name or []
            if not entities:
                self.logger.warning(
                    "Question extraction returned empty entities — using question"
                )
                entities = [sanitized]

            subject = (extraction.subject or "").strip()
            if not subject:
                self.logger.warning(
                    "Question extraction returned empty subject — using sanitized"
                )
                subject = sanitized

            path_state["normalized_question"] = sanitized
            path_state["entities"] = entities
            path_state["subject"] = subject
            self.logger.info(
                "Extracted question:\n  raw: %s\n  sanitized: %s\n  entities: %s"
                "\n  subject: %s",
                original_question,
                sanitized,
                entities,
                subject,
            )
        except Exception as exc:
            self.logger.warning(
                "Question extraction failed: %s, using original question",
                exc,
            )
            path_state["normalized_question"] = original_question
            path_state["entities"] = [original_question]
            path_state["subject"] = original_question

        return result
