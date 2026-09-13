# SPDX-FileCopyrightText: Copyright (c) 2026, NVIDIA CORPORATION & AFFILIATES.
# All rights reserved.
# SPDX-License-Identifier: Apache-2.0

"""The planner that splits a multi-step request into single-step questions.

Every failure mode here has the same required outcome: fall back to a
one-element list holding the original question, so the caller answers it in a
single pass exactly as it would have without a planner.
"""

from unittest.mock import MagicMock

import pytest

from gsf.retrieval.text_to_sql.agents import question_decomposition
from gsf.retrieval.text_to_sql.agents.question_decomposition import (
    QuestionDecompositionAgent,
)
from gsf.retrieval.text_to_sql.models import QuestionDecompositionModel
from gsf.retrieval.text_to_sql.prompts import (
    create_question_decomposition_prompt,
)

_QUESTION = (
    "List out the account numbers of female clients who are oldest and has "
    "lowest average salary, calculate the gap between this lowest average "
    "salary with the highest average salary?"
)


def _state(question: str = _QUESTION, **overrides: object) -> dict:
    state: dict = {
        "llm": MagicMock(),
        "initial_question": question,
        "evidence": "",
        "path_state": {},
        "glossary": [],
    }
    state.update(overrides)
    return state


def _returning(*sub_questions: str):
    """Patch-in that makes the LLM call yield *sub_questions*."""

    def fake_invoke(_llm: object, _messages: list, _schema: object):
        return QuestionDecompositionModel(sub_questions=list(sub_questions))

    return fake_invoke


def test_prompt_carries_the_step_cap_and_the_question() -> None:
    prompt = create_question_decomposition_prompt(_QUESTION, max_sub_questions=3)

    assert "Emit at most 3 sub-questions" in prompt
    assert _QUESTION in prompt
    assert "## Evidence" not in prompt


def test_prompt_keeps_a_single_step_as_the_original_question() -> None:
    """No split means no rewrite; later nodes see the user's words."""
    prompt = create_question_decomposition_prompt(_QUESTION)

    assert "Copy the input question verbatim" in prompt
    assert "do not rephrase" in prompt
    assert "Make an implicit scope explicit" not in prompt
    assert "return the request verbatim" not in prompt


def test_prompt_keeps_subquery_scalars_as_one_step() -> None:
    """Superlatives and averages are nested SQL, not a lookup-then-paste split."""
    prompt = create_question_decomposition_prompt(_QUESTION)

    assert "The intermediate is a scalar a subquery can compute" in prompt
    assert 'Do not first ask what "fastest" is' in prompt
    assert "Which repository has the most forks?" not in prompt
    assert "What is the average number of pages across all books?" not in prompt
    assert "Did 2014 have any delayed shipments?" in prompt


def test_prompt_includes_evidence_and_glossary_when_given() -> None:
    prompt = create_question_decomposition_prompt(
        "Show MRR by region",
        [{"name": "MRR", "description": "monthly recurring revenue"}],
        evidence="Gap = highest average salary - lowest average salary.",
    )

    assert "## Glossary" in prompt
    assert "monthly recurring revenue" in prompt
    assert "## Evidence" in prompt
    assert "Gap = highest average salary" in prompt


def test_multi_step_question_is_split_in_order(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(
        question_decomposition,
        "invoke_with_structured_output",
        _returning(
            "What is the lowest average salary among districts of the oldest "
            "female clients?",
            "What is the gap between that salary and the highest average salary?",
        ),
    )

    result = QuestionDecompositionAgent().execute(_state())

    assert result["path_state"]["sub_questions"] == [
        "What is the lowest average salary among districts of the oldest "
        "female clients?",
        "What is the gap between that salary and the highest average salary?",
    ]


def test_single_step_question_yields_one_entry(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    question = "How many shipments were delivered last month?"
    monkeypatch.setattr(
        question_decomposition,
        "invoke_with_structured_output",
        _returning(question),
    )

    result = QuestionDecompositionAgent().execute(_state(question))

    assert result["path_state"]["sub_questions"] == [question]


def test_a_one_step_paraphrase_is_replaced_with_the_original(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    question = "How many shipments were delivered last month?"
    monkeypatch.setattr(
        question_decomposition,
        "invoke_with_structured_output",
        _returning("Count last month's delivered shipments."),
    )

    result = QuestionDecompositionAgent().execute(_state(question))

    assert result["path_state"]["sub_questions"] == [question]


def test_blank_and_duplicate_entries_are_dropped(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(
        question_decomposition,
        "invoke_with_structured_output",
        _returning(
            "What is the lowest average salary?",
            "   ",
            "what is the lowest average salary?",
            "What is the highest average salary?",
        ),
    )

    result = QuestionDecompositionAgent().execute(_state())

    assert result["path_state"]["sub_questions"] == [
        "What is the lowest average salary?",
        "What is the highest average salary?",
    ]


def test_list_is_capped_at_the_configured_maximum(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(
        question_decomposition,
        "invoke_with_structured_output",
        _returning(*[f"What is metric number {i}?" for i in range(9)]),
    )

    result = QuestionDecompositionAgent(max_sub_questions=3).execute(_state())

    assert len(result["path_state"]["sub_questions"]) == 3


def test_a_fragment_invalidates_the_whole_split(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A chain with a hole in it is worse than no chain.

    Dropping only the unusable entry would leave a later step referring to a
    value that nothing computed, so the entire decomposition is abandoned.
    """
    monkeypatch.setattr(
        question_decomposition,
        "invoke_with_structured_output",
        _returning("the gap", "What is the highest average salary by district?"),
    )

    result = QuestionDecompositionAgent().execute(_state())

    assert result["path_state"]["sub_questions"] == [_QUESTION]


@pytest.mark.parametrize(
    "fake_invoke",
    [
        pytest.param(lambda *_args: None, id="llm_returned_none"),
        pytest.param(
            lambda *_args: QuestionDecompositionModel(sub_questions=[]),
            id="llm_returned_empty",
        ),
    ],
)
def test_unusable_llm_output_falls_back_to_the_original_question(
    monkeypatch: pytest.MonkeyPatch, fake_invoke: object
) -> None:
    monkeypatch.setattr(
        question_decomposition, "invoke_with_structured_output", fake_invoke
    )

    result = QuestionDecompositionAgent().execute(_state())

    assert result["path_state"]["sub_questions"] == [_QUESTION]


def test_llm_failure_falls_back_to_the_original_question(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    def boom(*_args: object) -> None:
        raise RuntimeError("model unavailable")

    monkeypatch.setattr(question_decomposition, "invoke_with_structured_output", boom)

    result = QuestionDecompositionAgent().execute(_state())

    assert result["path_state"]["sub_questions"] == [_QUESTION]


def test_the_follow_up_rewrite_is_what_gets_split(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A follow-up must be planned from its standalone form, not "and for 2013?"."""
    captured: list = []

    def fake_invoke(_llm: object, messages: list, _schema: object):
        captured.extend(messages)
        return QuestionDecompositionModel(sub_questions=["What were 2013 sales?"])

    monkeypatch.setattr(
        question_decomposition, "invoke_with_structured_output", fake_invoke
    )
    state = _state(
        "and for 2013?",
        path_state={"processing_question": "What were the total sales in 2013?"},
    )

    QuestionDecompositionAgent().execute(state)

    assert "What were the total sales in 2013?" in captured[0].content
