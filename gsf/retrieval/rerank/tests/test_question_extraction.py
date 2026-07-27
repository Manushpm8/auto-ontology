"""Smoke tests for question extraction prompt, schema, and agent wiring."""

from __future__ import annotations

import logging
from unittest.mock import MagicMock, patch

from gsf.retrieval.rerank.agents.column_resolution import _vector_search_query
from gsf.retrieval.rerank.agents.question_extraction import (
    ExtractedEntities,
    QuestionExtractionAgent,
    QuestionExtractionModel,
    _resolve_target_entity_type,
)
from gsf.retrieval.rerank.prompts import create_question_extraction_prompt


def test_prompt_includes_term_catalog() -> None:
    term_names = ["Brand", "Product"]
    prompt = create_question_extraction_prompt("find red shoes", term_names)

    assert "Available entity types (Term names from the catalog):" in prompt
    assert "- Brand" in prompt
    assert "- Product" in prompt
    assert "target_entity_type" in prompt


def test_extracted_entities_includes_target_entity_type() -> None:
    entities = ExtractedEntities(
        search_for=["shoe"],
        target_entity_type="Product",
    )
    dumped = entities.model_dump()

    assert dumped["target_entity_type"] == "Product"
    assert dumped["search_for"] == ["shoe"]


def test_resolve_target_entity_type_maps_name_to_id() -> None:
    logger = logging.getLogger("test")
    term_id_by_name = {"Product": "term-1", "Brand": "term-2"}

    assert _resolve_target_entity_type("Product", term_id_by_name, logger) == (
        "Product",
        "term-1",
    )
    assert _resolve_target_entity_type("Unknown", term_id_by_name, logger) == ("", "")
    assert _resolve_target_entity_type("", term_id_by_name, logger) == ("", "")


def test_vector_search_query_prefixes_selected_buckets() -> None:
    assert _vector_search_query("shoe", "search_for", "Product") == "Product shoe"
    assert (
        _vector_search_query("hanger", "reference_entity", "Product")
        == "Product hanger"
    )
    assert _vector_search_query("red", "search_for_details", "Product") == "red"
    assert _vector_search_query("shoe", "search_for", "") == "shoe"
    assert (
        _vector_search_query("Product shoe", "search_for", "Product") == "Product shoe"
    )


@patch("gsf.retrieval.rerank.agents.question_extraction.invoke_with_structured_output")
@patch("gsf.retrieval.rerank.agents.question_extraction.fetch_all_terms")
def test_agent_passes_term_names_and_returns_target_entity_type(
    mock_fetch_terms: MagicMock,
    mock_invoke: MagicMock,
) -> None:
    mock_fetch_terms.return_value = [
        {"id": "term-product", "name": "Product"},
        {"id": "term-brand", "name": "Brand"},
    ]
    mock_invoke.return_value = QuestionExtractionModel(
        normalized_question="find red shoes",
        entities=ExtractedEntities(
            search_for=["shoe"],
            search_for_details=["red"],
            reference_entity=["derailleur hanger"],
            target_entity_type="Product",
        ),
    )

    agent = QuestionExtractionAgent()
    result = agent.execute(
        {
            "llm": MagicMock(),
            "path_state": {},
            "initial_question": "I'm looking for red shoes",
        },
    )

    prompt_arg = mock_invoke.call_args[0][1][0].content
    assert "- Product" in prompt_arg
    assert "- Brand" in prompt_arg
    entities = result["path_state"]["entities"]
    assert entities["target_entity_type"] == "Product"
    assert entities["target_entity_type_id"] == "term-product"
    assert entities["search_for"] == ["shoe"]
    assert entities["reference_entity"] == ["derailleur hanger"]


@patch("gsf.retrieval.rerank.agents.question_extraction.invoke_with_structured_output")
@patch("gsf.retrieval.rerank.agents.question_extraction.fetch_all_terms")
def test_agent_clears_invented_target_entity_type(
    mock_fetch_terms: MagicMock,
    mock_invoke: MagicMock,
) -> None:
    mock_fetch_terms.return_value = [{"id": "term-product", "name": "Product"}]
    mock_invoke.return_value = QuestionExtractionModel(
        normalized_question="find shoes",
        entities=ExtractedEntities(
            search_for=["shoe"],
            target_entity_type="InventedType",
        ),
    )

    agent = QuestionExtractionAgent()
    result = agent.execute(
        {
            "llm": MagicMock(),
            "path_state": {},
            "initial_question": "find shoes",
        },
    )

    entities = result["path_state"]["entities"]
    assert entities["target_entity_type"] == ""
    assert entities["target_entity_type_id"] == ""
    assert entities["search_for"] == ["shoe"]
