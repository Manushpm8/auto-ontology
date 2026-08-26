# SPDX-FileCopyrightText: Copyright (c) 2024-2026, NVIDIA CORPORATION & AFFILIATES.
# All rights reserved.
# SPDX-License-Identifier: Apache-2.0

"""Tests for gsf.dal.candidates."""

from __future__ import annotations

from gsf.dal.candidates import _normalize_sample_values_in_place


def test_normalize_sample_values_in_place_handles_legacy_string_and_list() -> None:
    results = {
        "id-1": [
            {
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
        ],
    }

    _normalize_sample_values_in_place(results)

    columns = results["id-1"][0]["relevant_tables"][0]["columns"]
    assert columns[0]["sample_values"] == ["a", "b"]
    assert columns[1]["sample_values"] == ["a", "b"]
    assert columns[2]["sample_values"] is None


def test_normalize_sample_values_in_place_tolerates_missing_nested_keys() -> None:
    # No `relevant_tables` / `columns` key at all — should not raise.
    results = {"id-1": [{}], "id-2": [{"relevant_tables": [{}]}]}
    _normalize_sample_values_in_place(results)
