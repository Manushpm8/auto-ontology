# SPDX-FileCopyrightText: Copyright (c) 2024-2026, NVIDIA CORPORATION & AFFILIATES.
# All rights reserved.
# SPDX-License-Identifier: Apache-2.0

"""Tests for gsf.dal.candidates."""

from __future__ import annotations

from gsf.dal.candidates import _normalize_sample_values_in_place


def test_normalize_sample_values_in_place_handles_legacy_string_and_list() -> None:
    # `apoc.map.groupBy` maps each id to a single item, not to a list.
    results = {
        "id-1": {
            "relevant_tables": [
                {
                    "columns": [
                        {"name": "legacy_col", "sample_values": '["a", "b"]'},
                        {"name": "current_col", "sample_values": ["a", "b"]},
                        {"name": "no_samples_col", "sample_values": None},
                    ],
                },
            ],
        },
    }

    _normalize_sample_values_in_place(results)

    columns = results["id-1"]["relevant_tables"][0]["columns"]
    assert columns[0]["sample_values"] == ["a", "b"]
    assert columns[1]["sample_values"] == ["a", "b"]
    assert columns[2]["sample_values"] is None


def test_normalize_sample_values_in_place_normalizes_column_item_itself() -> None:
    # A Column item exposes its own properties alongside the parent table.
    results = {
        "col-1": {
            "name": "legacy_col",
            "sample_values": '["a", "b"]',
            "relevant_tables": [
                {"columns": [{"name": "legacy_col", "sample_values": '["a", "b"]'}]},
            ],
        },
    }

    _normalize_sample_values_in_place(results)

    assert results["col-1"]["sample_values"] == ["a", "b"]
    assert results["col-1"]["relevant_tables"][0]["columns"][0]["sample_values"] == [
        "a",
        "b",
    ]


def test_normalize_sample_values_in_place_accepts_list_values() -> None:
    results = {"id-1": [{"sample_values": '["a"]'}]}

    _normalize_sample_values_in_place(results)

    assert results["id-1"][0]["sample_values"] == ["a"]


def test_normalize_sample_values_in_place_tolerates_missing_nested_keys() -> None:
    # No `relevant_tables` / `columns` key at all — should not raise.
    results = {"id-1": {}, "id-2": {"relevant_tables": [{}]}, "id-3": None}
    _normalize_sample_values_in_place(results)
