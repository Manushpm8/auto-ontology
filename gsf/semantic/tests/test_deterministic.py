"""Tests for deterministic column mapping."""

from __future__ import annotations

from gsf.semantic.deterministic import (
    column_attribute_specs,
    fk_source_columns,
    fk_target_table_names,
    to_term_name,
)


def test_excludes_suggested_fk_columns() -> None:
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


def test_excludes_fk_columns() -> None:
    columns = [
        {"name": "id", "data_type": "integer"},
        {"name": "customer_id", "data_type": "integer"},
        {"name": "amount", "data_type": "numeric"},
    ]
    fks = [{"source_column": "customer_id", "target_table": "customers"}]
    specs = column_attribute_specs(columns, fks)
    assert {s.source_column for s in specs} == {"id", "amount"}
    assert fk_source_columns(fks) == {"customer_id"}


def test_to_term_name() -> None:
    assert to_term_name("purchase_orders") == "PurchaseOrders"


def test_fk_targets_deduped() -> None:
    fks = [
        {"target_table": "customers"},
        {"target_table": "customers"},
        {"target_table": "products"},
    ]
    assert fk_target_table_names(fks) == ["customers", "products"]
