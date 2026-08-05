"""Tests for deterministic column mapping."""

from __future__ import annotations

from unittest.mock import patch

from gsf.semantic.constants import MAX_SAMPLE_VALUE_LEN
from gsf.semantic.deterministic import (
    blank_column_descriptions,
    column_attribute_specs,
    fk_source_columns,
    fk_target_table_names,
    to_term_name,
)


@patch("gsf.semantic.deterministic._generate_column_descriptions", return_value={})
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


@patch("gsf.semantic.deterministic._generate_column_descriptions", return_value={})
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
    "gsf.semantic.deterministic._generate_column_descriptions",
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
    "gsf.semantic.deterministic._generate_column_descriptions",
    return_value={
        "status": "The operational status of the school.",
        "email": "The contact email address.",
    },
)
def test_profiled_values_reach_llm_descriptions(_mock_desc) -> None:
    """An LLM-written description carries its values too, labelled by completeness.

    The primary retrieval path projects only the description, so an LLM-described
    column loses its values unless they are folded into the prose — and the
    generated prose rarely enumerates them on its own.
    """
    columns = [
        {"name": "status", "data_type": "text"},
        {"name": "email", "data_type": "text"},
    ]
    profiling = {
        "status": {"sample_values": ["Active", "Closed"], "exhaustive": True},
        "email": {"sample_values": ["a@x.com", "b@y.com"], "exhaustive": False},
    }
    specs = {
        s.source_column: s
        for s in column_attribute_specs(
            columns, [], columns_profiling_samples=profiling
        )
    }
    assert specs["status"].description == (
        "The operational status of the school. — one of: Active, Closed"
    )
    assert specs["email"].description == (
        "The contact email address. — samples: a@x.com, b@y.com"
    )


@patch("gsf.semantic.deterministic._generate_column_descriptions", return_value={})
def test_over_length_values_are_dropped_from_the_description(_mock_desc) -> None:
    """A free-text column must not paste a whole row into the schema block.

    The description is the only channel the primary retrieval path carries, so an
    uncapped value here reaches the prompt in full — a real ``users.AboutMe``
    sample ran to 2.5k characters.
    """
    long_value = "<p>" + "x" * MAX_SAMPLE_VALUE_LEN + "</p>"
    columns = [
        {"name": "about_me", "data_type": "text", "description": "User bio."},
        {"name": "status", "data_type": "text", "description": "Row status."},
    ]
    profiling = {
        "about_me": {"sample_values": [long_value, "ok"], "exhaustive": False},
        # Exhaustive, but one value is too long to carry: the surviving list is no
        # longer the complete set, so it must not be stated as a constraint.
        "status": {"sample_values": ["Active", long_value], "exhaustive": True},
    }
    specs = {
        s.source_column: s
        for s in column_attribute_specs(
            columns, [], columns_profiling_samples=profiling
        )
    }
    assert specs["about_me"].description == "User bio. — samples: ok"
    assert specs["status"].description == "Row status. — samples: Active"


@patch("gsf.semantic.deterministic._generate_column_descriptions", return_value={})
def test_values_are_labelled_by_completeness(_mock_desc) -> None:
    """A complete value set is a constraint; an incomplete one is examples.

    Both must be appended: the description is the only field the primary retrieval
    path carries to the prompt, so a value left out here is one the model cannot see.
    """
    columns = [
        {"name": "status", "data_type": "text", "description": "School status."},
        {"name": "city", "data_type": "text", "description": "City name."},
        {"name": "notes", "data_type": "text", "description": "Free text."},
    ]
    profiling = {
        "status": {"sample_values": ["Active", "Closed"], "exhaustive": True},
        "city": {"sample_values": ["Prague", "Brno"], "exhaustive": False},
        "notes": {"sample_values": [], "exhaustive": False},
    }
    specs = {
        s.source_column: s
        for s in column_attribute_specs(
            columns, [], columns_profiling_samples=profiling
        )
    }
    assert specs["status"].description == "School status. — one of: Active, Closed"
    assert specs["city"].description == "City name. — samples: Prague, Brno"
    # No profiled values: the description is passed through untouched rather than
    # gaining a dangling separator.
    assert specs["notes"].description == "Free text."


@patch("gsf.semantic.deterministic._generate_column_descriptions", return_value={})
def test_exhaustive_values_survive_a_failed_description_batch(_mock_desc) -> None:
    """No description to append to must not yield a dangling separator."""
    columns = [{"name": "status", "data_type": "text"}]
    profiling = {"status": {"sample_values": ["Active"], "exhaustive": True}}
    specs = column_attribute_specs(columns, [], columns_profiling_samples=profiling)
    assert specs[0].description == "one of: Active"


@patch(
    "gsf.semantic.deterministic._generate_column_descriptions",
    return_value={"uuid": "The unique identifier of a card."},
)
def test_only_blank_columns_are_described(mock_desc) -> None:
    """Ingest describes the blank columns, including foreign keys.

    Foreign keys matter here because the semantic layer excludes them from
    ColumnAttributes, so this is the only stage that can describe them.
    """
    columns = [
        {"name": "uuid", "data_type": "text"},
        {"name": "set_code", "data_type": "text", "description": "The set code."},
    ]
    out = blank_column_descriptions(columns)
    assert out == {"uuid": "The unique identifier of a card."}
    # The documented column was not sent to the LLM.
    assert [c["name"] for c in mock_desc.call_args[0][0]] == ["uuid"]


@patch("gsf.semantic.deterministic._generate_column_descriptions")
def test_blank_descriptions_skip_the_llm_when_nothing_is_blank(mock_desc) -> None:
    columns = [{"name": "set_code", "data_type": "text", "description": "The set."}]
    assert blank_column_descriptions(columns) == {}
    mock_desc.assert_not_called()


def test_to_term_name() -> None:
    assert to_term_name("purchase_orders") == "PurchaseOrders"


def test_fk_targets_deduped() -> None:
    fks = [
        {"target_table": "customers"},
        {"target_table": "customers"},
        {"target_table": "products"},
    ]
    assert fk_target_table_names(fks) == ["customers", "products"]
