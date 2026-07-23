"""Test that _analyze_error includes path_state['error'] in its prompt."""
from unittest.mock import MagicMock, patch, call
import pytest


def test_analyze_error_includes_path_state_error():
    from gsf.retrieval.text_to_sql.agents.sql_reconstruction import SQLReconstructionAgent

    agent = SQLReconstructionAgent.__new__(SQLReconstructionAgent)

    # Build a mock LLM that captures the prompt
    captured_prompts = []
    mock_structured_output = MagicMock()

    def fake_invoke(prompt):
        captured_prompts.append(str(prompt))
        result = MagicMock()
        result.error_type = "FIXABLE"
        result.search_queries = []
        result.explanation = "test"
        return result

    mock_structured_output.invoke.side_effect = fake_invoke
    mock_llm = MagicMock()
    mock_llm.with_structured_output.return_value = mock_structured_output

    state = {
        "llm": mock_llm,
        "path_state": {
            "error": "Bird external feedback: column aliens_count does not exist",
            "target_db": "alien",
        },
        "data_retriever": MagicMock(),
    }

    agent._analyze_error(
        state=state,
        question="How many aliens?",
        error_context="SQL: SELECT aliens_count FROM aliens\nResponse: column does not exist",
        existing_tables=[],
    )

    assert captured_prompts, "LLM was never invoked"
    combined = " ".join(captured_prompts)
    assert "Bird external feedback" in combined, (
        f"path_state['error'] not found in prompt. Got: {combined[:500]}"
    )


def test_analyze_error_without_path_state_error_still_works():
    """Regression: when path_state has no 'error' key, _analyze_error must not crash."""
    from gsf.retrieval.text_to_sql.agents.sql_reconstruction import SQLReconstructionAgent

    agent = SQLReconstructionAgent.__new__(SQLReconstructionAgent)

    mock_structured_output = MagicMock()
    mock_structured_output.invoke.return_value = MagicMock(
        error_type="FIXABLE", search_queries=[], explanation="ok"
    )
    mock_llm = MagicMock()
    mock_llm.with_structured_output.return_value = mock_structured_output

    state = {
        "llm": mock_llm,
        "path_state": {"target_db": "alien"},
        "data_retriever": MagicMock(),
    }

    # Must not raise
    agent._analyze_error(
        state=state,
        question="test",
        error_context="SQL: SELECT 1\nResponse: ok",
        existing_tables=[],
    )
