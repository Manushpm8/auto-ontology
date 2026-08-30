# SPDX-FileCopyrightText: Copyright (c) 2024-2026, NVIDIA CORPORATION & AFFILIATES.
# All rights reserved.
# SPDX-License-Identifier: Apache-2.0

"""Tests for Column.sample_values normalization."""

from __future__ import annotations

from gsf.utils.sample_values import parse_sample_values


def test_parse_sample_values_drops_none_before_stringify() -> None:
    """JSON null / Python None must not become the literal string 'None'."""
    assert parse_sample_values(["a", None, "b"]) == ["a", "b"]
    assert parse_sample_values('["a", null, "b"]') == ["a", "b"]
    assert parse_sample_values([None, None]) == []


def test_parse_sample_values_handles_legacy_json_and_native_list() -> None:
    assert parse_sample_values(["1", "2"]) == ["1", "2"]
    assert parse_sample_values('["1", "2"]') == ["1", "2"]
    assert parse_sample_values(None) is None
    assert parse_sample_values([]) == []
    assert parse_sample_values("not json") is None
    assert parse_sample_values(42) is None
