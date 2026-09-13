# SPDX-FileCopyrightText: Copyright (c) 2026, NVIDIA CORPORATION & AFFILIATES.
# All rights reserved.
# SPDX-License-Identifier: Apache-2.0

"""Carrying an answered step forward as evidence for the next one."""

import json

import pytest

from gsf.retrieval.text_to_sql import decomposition
from gsf.retrieval.text_to_sql.decomposition import (
    SubAnswer,
    build_step_evidence,
    is_decomposition_enabled,
    summarize_sub_answer,
)


def _db_result(rows: list[dict]) -> list[str]:
    """Shape ``SQLExecutionAgent`` stores: one JSON string in a list."""
    return [json.dumps(rows)]


def test_no_answered_steps_leaves_the_callers_evidence_untouched() -> None:
    assert build_step_evidence("Gap = max - min.", []) == "Gap = max - min."


def test_answered_steps_are_appended_below_the_callers_evidence() -> None:
    evidence = build_step_evidence(
        "Gap = highest average salary - lowest average salary.",
        [
            SubAnswer(
                question="What is the lowest average salary?",
                sql="SELECT MIN(A11) FROM district",
                result='[{"min": 8110}]',
            )
        ],
    )

    # The caller's own evidence keeps the top slot so a domain formula still
    # reads as the primary instruction.
    assert evidence.startswith("Gap = highest average salary")
    assert "## Results of earlier steps" in evidence
    assert "### Step 1: What is the lowest average salary?" in evidence
    assert "SELECT MIN(A11) FROM district" in evidence
    assert '[{"min": 8110}]' in evidence
    assert "Identifiers" in evidence
    assert "Measures" in evidence
    assert "do not paste the number" in evidence
    assert "further row(s) omitted" in evidence
    assert "reuse them instead of" not in evidence
    assert "Do not paste a prior result" not in evidence


def test_steps_are_numbered_in_the_order_they_were_answered() -> None:
    evidence = build_step_evidence(
        "",
        [
            SubAnswer(question="First?", sql="SELECT 1", result="[]"),
            SubAnswer(question="Second?", sql="SELECT 2", result="[]"),
        ],
    )

    assert evidence.index("### Step 1: First?") < evidence.index("### Step 2: Second?")
    # Nothing to prepend, so the section is the whole evidence string.
    assert evidence.startswith("## Results of earlier steps")


def test_rows_are_reparsed_into_compact_json() -> None:
    answer = summarize_sub_answer(
        "What is the lowest average salary?",
        {
            "sql_code": "SELECT MIN(A11) AS m FROM district",
            "sql_response_from_db": _db_result([{"m": 8110}]),
        },
    )

    assert answer.question == "What is the lowest average salary?"
    assert answer.sql == "SELECT MIN(A11) AS m FROM district"
    assert json.loads(answer.result) == [{"m": 8110}]


@pytest.mark.parametrize(
    "sql_response_from_db",
    [
        pytest.param(None, id="never_executed"),
        pytest.param([], id="no_payload"),
        pytest.param(["[]"], id="query_matched_nothing"),
    ],
)
def test_a_step_that_returned_nothing_says_so(sql_response_from_db: object) -> None:
    """An empty step must not read as a missing step.

    The next step's prompt needs "this was computed and found nothing", which
    is a fact it can act on, rather than a blank that invites recomputation.
    """
    answer = summarize_sub_answer(
        "Which schools closed before 2000?",
        {"sql_code": "SELECT 1", "sql_response_from_db": sql_response_from_db},
    )

    assert answer.result == "(no rows)"


def test_a_long_result_is_truncated_on_a_row_boundary(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(decomposition, "MAX_RESULT_CHARS", 120)
    rows = [{"city": f"City {i}", "count": i} for i in range(200)]

    answer = summarize_sub_answer(
        "How many schools per city?",
        {"sql_code": "SELECT 1", "sql_response_from_db": _db_result(rows)},
    )

    kept, note = answer.result.split("\n", 1)
    # Truncation must leave parseable JSON behind, not a severed row.
    assert json.loads(kept) == rows[: len(json.loads(kept))]
    assert "further row(s) omitted" in note
    assert len(json.loads(kept)) < len(rows)


def test_unparseable_payload_is_passed_through_rather_than_dropped() -> None:
    answer = summarize_sub_answer(
        "Anything?", {"sql_code": "", "sql_response_from_db": ["not json at all"]}
    )

    assert answer.result == "not json at all"


@pytest.mark.parametrize(
    ("value", "expected"),
    [
        ("true", True),
        ("1", True),
        ("on", True),
        ("false", False),
        ("", False),
        ("maybe", False),
    ],
)
def test_the_feature_is_opt_in(
    monkeypatch: pytest.MonkeyPatch, value: str, expected: bool
) -> None:
    monkeypatch.setenv("QUESTION_DECOMPOSITION", value)

    assert is_decomposition_enabled() is expected


def test_the_feature_is_off_when_unset(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("QUESTION_DECOMPOSITION", raising=False)

    assert is_decomposition_enabled() is False
