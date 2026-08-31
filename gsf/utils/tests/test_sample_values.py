# SPDX-FileCopyrightText: Copyright (c) 2024-2026, NVIDIA CORPORATION & AFFILIATES.
# All rights reserved.
# SPDX-License-Identifier: Apache-2.0

"""Tests for Column.sample_values normalization."""

from __future__ import annotations

from gsf.utils.sample_values import parse_sample_values, stringify_sample_values


def test_parse_sample_values_drops_none() -> None:
    """JSON null / Python None must not reach consumers as a value."""
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


def test_parse_sample_values_preserves_scalar_types() -> None:
    """A numeric column must stay distinguishable from a text one."""
    assert parse_sample_values([0, 1.5, True, "1"]) == [0, 1.5, True, "1"]
    assert [type(v) for v in parse_sample_values([0, 1.5, True, "1"]) or []] == [
        int,
        float,
        bool,
        str,
    ]


def test_parse_sample_values_decodes_legacy_json_scalar_types() -> None:
    assert parse_sample_values('[0, 1.5, true, "1"]') == [0, 1.5, True, "1"]


def test_stringify_sample_values_renders_scalars() -> None:
    assert stringify_sample_values([0, 1.5, True, "a"]) == ["0", "1.5", "True", "a"]
    assert stringify_sample_values(None) is None
    assert stringify_sample_values([]) == []


def test_stringify_sample_values_renders_containers_as_json() -> None:
    """Python repr would leak single quotes into prompts and API responses."""
    assert stringify_sample_values([{"a": 1}, ["x", "y"]]) == ['{"a": 1}', '["x", "y"]']


def test_stringify_sample_values_applies_max_len_to_rendered_form() -> None:
    """The cap measures display text, so a non-string is judged once rendered."""
    assert stringify_sample_values(["short", "x" * 31], max_len=30) == ["short"]
    assert stringify_sample_values([12345], max_len=30) == ["12345"]
    assert stringify_sample_values([10**40], max_len=30) == []
