# SPDX-FileCopyrightText: Copyright (c) 2024-2026, NVIDIA CORPORATION & AFFILIATES.
# All rights reserved.
# SPDX-License-Identifier: Apache-2.0

"""Tests for the pure helpers in auto_ontology.semantic.embed."""

from __future__ import annotations

from auto_ontology.semantic.embed import _format_sample_values


def test_attribute_embedding_includes_value_description() -> None:
    from auto_ontology.semantic.embed import _build_rows

    rows = _build_rows(
        "schools",
        {"name": "School", "description": "A school"},
        [
            {
                "name": "streetAbr",
                "description": "Abbreviated street.",
                "value_description": "Some closed schools have no value.",
                "term_name": "School",
                "source_column": "StreetAbr",
                "id": "a1",
            }
        ],
    )
    attribute = next(
        row for row in rows if row["metadata"]["label"] == "ColumnAttribute"
    )
    assert "Abbreviated street." in attribute["text"]
    assert "Some closed schools have no value." in attribute["text"]


def test_format_sample_values_handles_legacy_json_string_and_native_list() -> None:
    # Legacy Column nodes still store sample_values as a JSON string.
    assert _format_sample_values('["a", "b"]') == " Sample values: a, b."
    # Current writers store a native list.
    assert _format_sample_values(["a", "b"]) == " Sample values: a, b."


def test_format_sample_values_filters_long_values_and_handles_empty() -> None:
    long_value = "x" * 31
    assert _format_sample_values([long_value, "ok"]) == " Sample values: ok."
    assert _format_sample_values(None) == ""
    assert _format_sample_values([]) == ""
    assert _format_sample_values(["a", None, "b"]) == " Sample values: a, b."
    assert _format_sample_values('["a", null, "b"]') == " Sample values: a, b."
