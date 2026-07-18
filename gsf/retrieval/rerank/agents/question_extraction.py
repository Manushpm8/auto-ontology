# SPDX-FileCopyrightText: Copyright (c) 2026, NVIDIA CORPORATION & AFFILIATES.
# All rights reserved.
# SPDX-License-Identifier: Apache-2.0

"""Question extraction node for the rerank flow.

Uses the (non-reasoning) LLM to produce a normalized question and to extract the
search entities into three buckets (``search_for``, ``terms``,
``numeric_concepts``).
"""

from typing import Any, Dict

from langchain_core.messages import SystemMessage
from pydantic import BaseModel, ConfigDict, Field

from gsf.retrieval.rerank.prompts import create_question_extraction_prompt
from gsf.retrieval.rerank.state import RerankState, get_original_question
from gsf.retrieval.text_to_sql.base import BaseAgent
from gsf.utils.llm_invoke import invoke_with_structured_output


class ExtractedEntities(BaseModel):
    """Entities extracted from the question, split into three buckets."""

    model_config = ConfigDict(extra="forbid")

    search_for: list[str] = Field(
        default_factory=list,
        description=(
            "The core item(s) the user wants to find. Nouns only: drop verbs, "
            "prepositions, and articles (e.g. 'box to hold playing cards' -> "
            "'box playing cards'). Keep words describing one item in one phrase. "
            "Always use the SINGULAR form of the item, never plural."
        ),
    )
    terms: list[str] = Field(
        default_factory=list,
        description=(
            "Descriptive, non-numeric qualifiers such as colors, brands, and "
            "materials (e.g. 'red', 'Panini'). Nouns/adjectives only — no verbs "
            "or connective words."
        ),
    )
    numeric_concepts: list[str] = Field(
        default_factory=list,
        description=(
            "Measurable/numeric attribute concepts (e.g. 'price', 'quantity'). "
            "Concept names only — never literal numbers."
        ),
    )


class QuestionExtractionModel(BaseModel):
    """Normalized question plus extracted entities."""

    model_config = ConfigDict(extra="forbid")

    normalized_question: str = Field(
        ...,
        description=(
            "Concise, search-ready rewrite of the request. Preserve factual "
            "constraints and remove narrative fluff."
        ),
    )
    entities: ExtractedEntities = Field(
        ...,
        description="Search entities extracted from the question.",
    )


class QuestionExtractionAgent(BaseAgent):
    """Normalize the question and extract search entities via the LLM."""

    def __init__(self):
        super().__init__("question_extraction")

    def validate_input(self, state: RerankState) -> bool:
        """Validate that a question is available."""
        question = get_original_question(state)
        if not question:
            self.logger.warning("No question found, skipping question extraction")
            return False
        return True

    def execute(self, state: RerankState) -> Dict[str, Any]:
        """Extract the normalized question and entities from the question."""
        llm = state["llm"]
        path_state = state.get("path_state", {})
        question = get_original_question(state)

        result: Dict[str, Any] = {"path_state": path_state}

        messages = [SystemMessage(content=create_question_extraction_prompt(question))]
        extraction = invoke_with_structured_output(
            llm,
            messages,
            QuestionExtractionModel,
        )

        if extraction is None:
            self.logger.warning(
                "Question extraction returned None, using fallback values"
            )
            path_state["normalized_question"] = question
            path_state["entities"] = {
                "search_for": [],
                "terms": [],
                "numeric_concepts": [],
            }
            return result

        normalized = (extraction.normalized_question or "").strip() or question
        path_state["normalized_question"] = normalized
        path_state["entities"] = extraction.entities.model_dump()

        self.logger.info(
            "Extracted normalized_question=%s entities=%s",
            normalized,
            path_state["entities"],
        )
        return result
