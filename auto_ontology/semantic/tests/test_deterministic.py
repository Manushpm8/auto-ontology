# SPDX-FileCopyrightText: Copyright (c) 2026, NVIDIA CORPORATION & AFFILIATES.
# All rights reserved.
# SPDX-License-Identifier: Apache-2.0

"""Tests for deterministic column mapping."""

from __future__ import annotations

from unittest.mock import patch

from auto_ontology.semantic.deterministic import (
    ColumnRead,
    _describe_column_batch,
    build_column_attributes,
    column_attribute_specs,
    fk_source_columns,
    fk_target_table_names,
    to_term_name,
)
from auto_ontology.semantic.models import ColumnDescription, ColumnDescriptionResult


@patch(
    "auto_ontology.semantic.deterministic._generate_column_descriptions",
    return_value={},
)
def test_excludes_suggested_fk_columns(_mock_desc) -> None:
    columns = [
        {"name": "id", "data_type": "integer"},
        {"name": "vendor_id", "data_type": "integer"},
        {"name": "amount", "data_type": "numeric"},
    ]
    specs = column_attribute_specs(
        columns,
        [],
        suggested_fk_columns={"vendor_id"},
    )
    assert {s.source_column for s in specs} == {"id", "amount"}


@patch(
    "auto_ontology.semantic.deterministic._generate_column_descriptions",
    return_value={},
)
def test_excludes_fk_columns(_mock_desc) -> None:
    columns = [
        {"name": "id", "data_type": "integer"},
        {"name": "customer_id", "data_type": "integer"},
        {"name": "amount", "data_type": "numeric"},
    ]
    fks = [{"source_column": "customer_id", "target_table": "customers"}]
    specs = column_attribute_specs(columns, fks)
    assert {s.source_column for s in specs} == {"id", "amount"}
    assert fk_source_columns(fks) == {"customer_id"}


@patch(
    "auto_ontology.semantic.deterministic._generate_column_descriptions",
    return_value={"amount": "The monetary amount of the order."},
)
def test_llm_description_used_as_fallback(_mock_desc) -> None:
    columns = [
        {"name": "id", "data_type": "integer", "description": "Primary key."},
        {"name": "amount", "data_type": "numeric"},
    ]
    specs = {s.source_column: s for s in column_attribute_specs(columns, [])}
    # Existing description wins over the LLM one.
    assert specs["id"].description == "Primary key."
    # LLM description fills in when the column has none.
    assert specs["amount"].description == "The monetary amount of the order."


@patch(
    "auto_ontology.semantic.deterministic._generate_column_descriptions",
    return_value={},
)
def test_date_column_description_states_stored_notation(_mock_desc) -> None:
    columns = [
        {
            "name": "game_date",
            "data_type": "text",
            "description": "Date the match was played.",
        }
    ]
    profiling = {"game_date": {"format": "YYMMDD", "sample_values": []}}
    specs = column_attribute_specs(columns, [], columns_profiling_samples=profiling)
    assert specs[0].description == ("Date the match was played. — format: YYMMDD")


@patch(
    "auto_ontology.semantic.deterministic._generate_column_descriptions",
    return_value={"game_date": "When the match took place."},
)
def test_llm_date_description_also_gets_the_notation(_mock_desc) -> None:
    columns = [{"name": "game_date", "data_type": "date"}]
    profiling = {"game_date": {"format": "YYYY-MM-DD"}}
    specs = column_attribute_specs(columns, [], columns_profiling_samples=profiling)
    assert specs[0].description == ("When the match took place. — format: YYYY-MM-DD")


@patch("auto_ontology.semantic.deterministic._generate_column_descriptions")
def test_unusable_column_is_not_an_attribute(mock_desc) -> None:
    """A deprecated column is flagged and dropped; a partial gap is not."""
    mock_desc.return_value = {
        "retired_code": ColumnRead(
            description="Deprecated. Do not use.", unusable=True
        ),
        "StreetAbr": ColumnRead(description="Abbreviated street.", unusable=False),
        "customer_id": ColumnRead(description="Customer reference.", unusable=True),
    }
    columns = [
        {
            "name": "retired_code",
            "data_type": "text",
            "description": "Deprecated. Do not use. Almost always empty.",
        },
        {
            "name": "StreetAbr",
            "data_type": "text",
            "description": "The abbreviated street address.",
            "value_description": (
                "Note: Some records (primarily records of closed or retired "
                "schools) may not have data in this field."
            ),
        },
        {
            "name": "customer_id",
            "data_type": "integer",
            "description": "Deprecated foreign key. Do not use.",
        },
        {"name": "amount", "data_type": "numeric"},
    ]
    built = build_column_attributes(
        columns,
        [{"source_column": "customer_id", "target_table": "customers"}],
    )
    assert {spec.source_column for spec in built.specs} == {"StreetAbr", "amount"}
    street = next(spec for spec in built.specs if spec.source_column == "StreetAbr")
    assert street.value_description.startswith("Note: Some records")
    assert set(built.unusable_columns) == {"retired_code", "customer_id"}
    sent = {col["name"] for col in mock_desc.call_args.args[0]}
    assert {"retired_code", "StreetAbr", "customer_id", "amount"} <= sent


def test_unusable_prompt_includes_value_description(monkeypatch) -> None:
    seen: dict[str, str] = {}

    def fake_invoke(_llm, messages, _schema):
        seen["system"] = messages[0].content
        seen["human"] = messages[1].content
        return ColumnDescriptionResult(
            descriptions=[
                ColumnDescription(
                    column_name="StreetAbr",
                    description="Abbreviated street.",
                    unusable=False,
                )
            ]
        )

    monkeypatch.setattr(
        "auto_ontology.semantic.deterministic.invoke_with_structured_output",
        fake_invoke,
    )
    monkeypatch.setattr(
        "auto_ontology.semantic.deterministic.get_llm_client", lambda **_k: object()
    )
    reads = _describe_column_batch(
        [
            {
                "name": "StreetAbr",
                "data_type": "text",
                "description": "The abbreviated street address.",
                "value_description": (
                    "Note: Some records (primarily records of closed or retired "
                    "schools) may not have data in this field."
                ),
            }
        ],
        {},
    )
    assert "not enough to mark it unusable" in seen["system"]
    assert "value_description:" in seen["human"]
    assert "closed or retired" in seen["human"]
    assert reads["StreetAbr"].unusable is False


def test_to_term_name() -> None:
    assert to_term_name("purchase_orders") == "Purchase Orders"
    assert to_term_name("delta_lite_event") == "Delta Lite Event"
    assert to_term_name("gtl_imaging_event") == "Gtl Imaging Event"
    assert to_term_name("gtl_ui_event") == "Gtl Ui Event"
    assert to_term_name("GtlImagingEvent") == "Gtl Imaging Event"
    assert to_term_name("XMLHttpRequest") == "XML Http Request"


def test_fk_targets_deduped() -> None:
    fks = [
        {"target_table": "customers"},
        {"target_table": "customers"},
        {"target_table": "products"},
    ]
    assert fk_target_table_names(fks) == ["customers", "products"]
