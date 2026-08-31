"""Tests for typed sample-value decoding."""

from gsf.utils.sample_values import parse_sample_values


def test_parse_sample_values_preserves_json_scalar_types() -> None:
    assert parse_sample_values('[0, 1.5, true, "1"]') == [0, 1.5, True, "1"]


def test_parse_sample_values_preserves_native_lists() -> None:
    values = [0, "1", False]
    assert parse_sample_values(values) == values
