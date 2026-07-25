# SPDX-FileCopyrightText: Copyright (c) 2026, NVIDIA CORPORATION & AFFILIATES.
# All rights reserved.
# SPDX-License-Identifier: Apache-2.0

"""Question extraction node for the rerank flow.

Uses the (non-reasoning) LLM to produce a normalized question and to extract
search entities into five buckets: the item the user wants now
(``search_for`` / ``search_for_details``), an existing item used as context
(``reference_entity`` / ``reference_entity_details``), and how those two
relate (``relation``).
"""

from typing import Any, Dict

from langchain_core.messages import SystemMessage
from pydantic import BaseModel, ConfigDict, Field

from gsf.retrieval.rerank.prompts import create_question_extraction_prompt
from gsf.retrieval.rerank.state import RerankState, get_original_question
from gsf.retrieval.text_to_sql.base import BaseAgent
from gsf.utils.llm_invoke import invoke_with_structured_output


class ExtractedEntities(BaseModel):
    """Entities extracted from the question, split into five buckets."""

    model_config = ConfigDict(extra="forbid")

    search_for: list[str] = Field(
        default_factory=list,
        description=(
            "The SINGLE core item the user wants to find now, as exactly ONE "
            "entry. Nouns only: drop verbs, prepositions, and articles (e.g. "
            "'box to hold playing cards' -> 'box playing cards'). Keep the "
            "words for that one item in a single phrase and use the SINGULAR "
            "form, never plural."
        ),
    )
    search_for_details: list[str] = Field(
        default_factory=list,
        description=(
            "Descriptive qualifiers of the item the user wants to find (everything "
            "about that target item that is not the core item phrase). Keep nouns "
            "and adjectives here (e.g. 'natural ingredient'). Do not repeat "
            "the core search_for item."
        ),
    )
    reference_entity: list[str] = Field(
        default_factory=list,
        description=(
            "The SINGLE existing item the user already owns, bought, or is "
            "referencing as context — exactly ONE entry when present. Empty when "
            "the question has no prior-item context. Nouns only; singular form."
        ),
    )
    reference_entity_details: list[str] = Field(
        default_factory=list,
        description=(
            "Brand, model, color, size, or other identifying qualifiers of the "
            "reference item that help locate it in the database. Do not repeat "
            "the core reference_entity phrase."
        ),
    )
    relation: list[str] = Field(
        default_factory=list,
        description=(
            "How search_for relates to reference_entity — exactly ONE short "
            "phrase when a reference exists (e.g. 'similar to', 'compatible with', "
            "'accessory for', 'upgrade for', 'replacement for'). Empty when "
            "there is no reference_entity."
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
                "search_for_details": [],
                "reference_entity": [],
                "reference_entity_details": [],
                "relation": [],
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
