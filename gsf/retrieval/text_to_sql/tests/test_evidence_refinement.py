# SPDX-FileCopyrightText: Copyright (c) 2026, NVIDIA CORPORATION & AFFILIATES.
# All rights reserved.
# SPDX-License-Identifier: Apache-2.0

import pytest

from gsf.retrieval.text_to_sql.agents import evidence_refinement
from gsf.retrieval.text_to_sql.agents.evidence_refinement import (
    EvidenceRefinementAgent,
    EvidenceRefinementResult,
    EvidenceRepairPatch,
    apply_evidence_repairs,
)
from gsf.retrieval.text_to_sql.text_to_sql_graph import (
    create_graph,
    route_evidence_refinement,
    route_prediction_or_evidence,
)


def _tables() -> list[dict]:
    return [
        {
            "database_name": "db",
            "schema_name": "public",
            "name": "accounts",
            "columns": [
                {
                    "name": "status",
                    "data_type": "text",
                    "sample_values": ["Restricted", "Active"],
                },
                {
                    "name": "amount",
                    "data_type": "integer",
                    "sample_values": [50, 100, 150],
                },
            ],
        }
    ]


def _patch(
    *,
    old_text: str,
    new_text: str,
    kind: str,
    line_number: int = 1,
    table_name: str = "",
    column_name: str = "",
) -> EvidenceRepairPatch:
    return EvidenceRepairPatch(
        line_number=line_number,
        old_text=old_text,
        new_text=new_text,
        kind=kind,
        table_name=table_name,
        column_name=column_name,
        reason="The question or samples prove the correction.",
    )


def test_repairs_string_representation_only_when_sample_proves_it() -> None:
    evidence = "restricted refers to accounts.status = 'restricted';\nkeep this line"
    repair = _patch(
        old_text="'restricted'",
        new_text="'Restricted'",
        kind="string_representation",
        table_name="accounts",
        column_name="status",
    )

    refined, accepted = apply_evidence_repairs(
        evidence, [repair], "Show restricted accounts", _tables()
    )

    assert refined == (
        "restricted refers to accounts.status = 'Restricted';\nkeep this line"
    )
    assert len(accepted) == 1


def test_repairs_constant_only_when_question_supplies_replacement() -> None:
    repair = _patch(old_text="10", new_text="100", kind="constant_value")

    refined, accepted = apply_evidence_repairs(
        "amount > 10", [repair], "Show amounts larger than 100", _tables()
    )

    assert refined == "amount > 100"
    assert len(accepted) == 1


def test_rejects_unverified_constant_and_formula_edits() -> None:
    unverified = _patch(old_text="10", new_text="999", kind="constant_value")
    formula = _patch(old_text="10", new_text="100", kind="constant_value")

    refined_unverified, accepted_unverified = apply_evidence_repairs(
        "amount > 10", [unverified], "Show large amounts", _tables()
    )
    refined_formula, accepted_formula = apply_evidence_repairs(
        "percentage = wins / games * 10",
        [formula],
        "Calculate percentage using 100",
        _tables(),
    )

    assert refined_unverified == "amount > 10"
    assert accepted_unverified == []
    assert refined_formula == "percentage = wins / games * 10"
    assert accepted_formula == []


def test_rejects_entire_response_when_any_patch_is_invalid() -> None:
    evidence = "status = 'restricted'\namount > 10"
    valid = _patch(
        old_text="'restricted'",
        new_text="'Restricted'",
        kind="string_representation",
        table_name="accounts",
        column_name="status",
    )
    invalid = _patch(
        old_text="10",
        new_text="999",
        kind="constant_value",
        line_number=2,
    )

    refined, accepted = apply_evidence_repairs(
        evidence, [valid, invalid], "Show restricted accounts", _tables()
    )

    assert refined == evidence
    assert accepted == []


@pytest.mark.parametrize(
    ("question", "new_operator", "expected"),
    [
        ("Show records starting from today", ">=", "created_at >= today"),
        ("Show records after today", ">=", "created_at > today"),
    ],
)
def test_repairs_predicate_only_when_question_unambiguously_supports_it(
    question: str,
    new_operator: str,
    expected: str,
) -> None:
    repair = _patch(old_text=">", new_text=new_operator, kind="predicate_operator")

    refined, _ = apply_evidence_repairs(
        "created_at > today", [repair], question, _tables()
    )

    assert refined == expected


def test_llm_failure_keeps_original_evidence(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(
        evidence_refinement, "safe_invoke_structured_nr", lambda *_args: None
    )
    state = {
        "initial_question": "Show restricted accounts",
        "evidence": "status = 'restricted'",
        "path_state": {"relevant_tables": _tables()},
    }

    result = EvidenceRefinementAgent().execute(state)

    assert result["evidence"] == "status = 'restricted'"
    assert "evidence_original" not in result["path_state"]


def test_agent_records_original_and_accepted_repairs(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    captured: dict = {}
    response = EvidenceRefinementResult(
        reasoning="The stored status casing is certain.",
        repairs=[
            _patch(
                old_text="'restricted'",
                new_text="'Restricted'",
                kind="string_representation",
                table_name="accounts",
                column_name="status",
            )
        ],
    )

    def _invoke(messages, schema):
        captured["messages"] = messages
        captured["schema"] = schema
        return response

    monkeypatch.setattr(
        evidence_refinement,
        "safe_invoke_structured_nr",
        _invoke,
    )
    state = {
        "initial_question": "Original question",
        "evidence": "status = 'restricted'",
        "path_state": {
            "normalized_question": "Show restricted accounts",
            "relevant_tables": _tables(),
        },
    }

    result = EvidenceRefinementAgent().execute(state)

    assert result["evidence"] == "status = 'Restricted'"
    assert result["path_state"]["evidence_original"] == "status = 'restricted'"
    assert len(result["path_state"]["evidence_repairs_applied"]) == 1
    assert "Show restricted accounts" in captured["messages"][1].content
    assert "Restricted" in captured["messages"][1].content
    assert captured["schema"] is EvidenceRefinementResult


def test_evidence_routing_runs_only_on_evidence_bearing_sql_paths() -> None:
    assert route_evidence_refinement({"evidence": " status = 'Active' "}) == (
        "refine_evidence"
    )
    assert route_evidence_refinement({"evidence": "  "}) == (
        "construct_sql_from_candidates"
    )
    assert (
        route_prediction_or_evidence(
            {"decision": "sql", "evidence": "status = 'Active'"}
        )
        == "refine_evidence"
    )
    assert (
        route_prediction_or_evidence(
            {"decision": "prediction", "evidence": "status = 'Active'"}
        )
        == "prediction"
    )


def test_graph_wires_refinement_before_sql_construction(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.delenv("KUMO_RFM_API_URL", raising=False)
    edges = {
        (edge.source, edge.target, edge.conditional)
        for edge in create_graph().compile().get_graph().edges
    }

    assert ("prepare_candidates", "refine_evidence", True) in edges
    assert ("prepare_candidates", "construct_sql_from_candidates", True) in edges
    assert ("refine_evidence", "construct_sql_from_candidates", False) in edges


def test_prediction_graph_can_bypass_refinement(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("KUMO_RFM_API_URL", "http://prediction.example")
    edges = {
        (edge.source, edge.target, edge.conditional)
        for edge in create_graph().compile().get_graph().edges
    }

    assert ("prepare_candidates", "classify_prediction", False) in edges
    assert ("classify_prediction", "prepare_prediction_graph", True) in edges
    assert ("classify_prediction", "refine_evidence", True) in edges
