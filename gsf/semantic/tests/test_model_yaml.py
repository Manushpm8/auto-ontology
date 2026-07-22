# SPDX-FileCopyrightText: Copyright (c) 2026, NVIDIA CORPORATION & AFFILIATES.
# All rights reserved.
# SPDX-License-Identifier: Apache-2.0

"""Tests for standalone GSF semantic-model YAML."""

from __future__ import annotations

import json
from typing import Any

import pytest
import yaml

from gsf.semantic import model_yaml


def _native_model_yaml() -> str:
    return yaml.safe_dump(
        {
            "version": "1.0",
            "model": {
                "name": "sales",
                "database": "analytics",
                "description": "Sales model",
            },
            "terms": [
                {
                    "name": "orders",
                    "source": {
                        "database": "analytics",
                        "schema": "public",
                        "table": "orders",
                    },
                    "description": "Orders",
                    "synonyms": ["purchases"],
                    "column_attributes": [
                        {"name": "order_id", "source_column": "order_id"},
                        {
                            "name": "customer_id",
                            "source_column": "customer_id",
                        },
                        {
                            "name": "order_date",
                            "source_column": "order_date",
                        },
                    ],
                    "sql_attributes": [
                        {
                            "name": "net_total",
                            "kind": "field",
                            "expressions": [
                                {
                                    "dialect": "ANSI_SQL",
                                    "expression": "subtotal - discount",
                                }
                            ],
                            "sql": (
                                'SELECT subtotal - discount AS "net_total" '
                                'FROM "analytics"."public"."orders" '
                                'AS "orders"'
                            ),
                            "table_refs": ["orders"],
                        },
                        {
                            "name": "revenue_per_customer",
                            "kind": "metric",
                            "expressions": [
                                {
                                    "dialect": "ANSI_SQL",
                                    "expression": (
                                        "SUM(orders.subtotal) / "
                                        "COUNT(DISTINCT "
                                        "customers.customer_id)"
                                    ),
                                }
                            ],
                            "sql": (
                                'SELECT SUM("orders"."subtotal") / '
                                'COUNT(DISTINCT "customers"."customer_id") '
                                'AS "revenue_per_customer" '
                                'FROM "analytics"."public"."orders" '
                                'AS "orders" JOIN '
                                '"analytics"."public"."customers" '
                                'AS "customers" ON '
                                '"orders"."customer_id" = '
                                '"customers"."customer_id"'
                            ),
                            "table_refs": ["orders", "customers"],
                        },
                    ],
                },
                {
                    "name": "customers",
                    "source": {
                        "database": "analytics",
                        "schema": "public",
                        "table": "customers",
                    },
                    "column_attributes": [
                        {
                            "name": "customer_id",
                            "source_column": "customer_id",
                        },
                        {"name": "name", "source_column": "name"},
                    ],
                },
            ],
            "semantic_foreign_keys": [
                {
                    "name": "orders_to_customers",
                    "from_term": "orders",
                    "to_term": "customers",
                    "from_columns": ["customer_id"],
                    "to_columns": ["customer_id"],
                }
            ],
        },
        sort_keys=False,
    )


class FakeDal:
    def __init__(self) -> None:
        self.plan: dict[str, Any] | None = None

    def resolve_catalog_sources(
        self,
        rows: list[dict[str, Any]],
    ) -> list[dict[str, Any]]:
        return [
            {
                "name": row["name"],
                "database": row["database"] or "analytics",
                "schema": row["schema"] or "public",
                "table": row["table"],
                "table_id": f"{row['name']}-table",
            }
            for row in rows
        ]

    def resolve_catalog_columns(
        self,
        rows: list[dict[str, Any]],
    ) -> list[dict[str, Any]]:
        return [
            {
                "dataset": row["dataset"],
                "column": row["column"],
                "column_id": f"{row['dataset']}-{row['column']}",
                "datatype": "text",
            }
            for row in rows
        ]

    def find_term_conflicts(self, *_args: Any, **_kwargs: Any) -> list:
        return []

    def find_table_conflicts(self, *_args: Any, **_kwargs: Any) -> list:
        return []

    def find_sql_attribute_conflicts(self, *_args: Any, **_kwargs: Any) -> list:
        return []

    def apply_import_plan(self, **kwargs: Any) -> dict[str, Any]:
        self.plan = kwargs
        return {
            "datasets": [
                {
                    "name": row["name"],
                    "term_id": f"{row['name']}-term",
                    "table_id": row["table_id"],
                }
                for row in kwargs["datasets"]
            ],
            "column_attributes": [
                {
                    "id": f"column-{index}",
                    "name": row["name"],
                    "term_name": row["dataset"],
                    "source_column": row["source_column"],
                    "description": row["description"],
                }
                for index, row in enumerate(kwargs["column_attributes"])
            ],
            "sql_attributes": [
                {
                    "id": f"sql-{index}",
                    "name": row["name"],
                    "term_name": row["term"],
                }
                for index, row in enumerate(kwargs["sql_attributes"])
            ],
            "relationships": kwargs["relationships"],
            "stale_ids": [],
        }


def _install_fake_dal(
    monkeypatch: pytest.MonkeyPatch,
    fake: FakeDal,
) -> None:
    for name in (
        "resolve_catalog_sources",
        "resolve_catalog_columns",
        "find_term_conflicts",
        "find_table_conflicts",
        "find_sql_attribute_conflicts",
        "apply_import_plan",
    ):
        monkeypatch.setattr(model_yaml.model_dal, name, getattr(fake, name))


def test_import_maps_native_yaml_to_graph_plan(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    fake = FakeDal()
    _install_fake_dal(monkeypatch, fake)
    monkeypatch.setattr(
        model_yaml,
        "get_dialects",
        lambda _database: ["postgres"],
    )

    summary = model_yaml.import_model_yaml(
        _native_model_yaml(),
        database_name="analytics",
        validate_queries=False,
        embed=False,
    )

    assert summary.to_dict() == {
        "model": "sales",
        "terms": 2,
        "column_attributes": 5,
        "sql_attributes": 2,
        "semantic_foreign_keys": 1,
        "embeddings": 0,
    }
    assert fake.plan is not None
    assert fake.plan["replace"] is True
    assert {row["name"] for row in fake.plan["sql_attributes"]} == {
        "net_total",
        "revenue_per_customer",
    }
    computed_field = next(
        row for row in fake.plan["sql_attributes"] if row["name"] == "net_total"
    )
    assert computed_field["description"] is None
    metric = next(row for row in fake.plan["sql_attributes"] if row["kind"] == "metric")
    assert len(metric["table_refs"]) == 2


def test_import_rejects_duplicate_column_mapping(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    fake = FakeDal()
    _install_fake_dal(monkeypatch, fake)
    root = yaml.safe_load(_native_model_yaml())
    root["terms"][0]["column_attributes"].append(
        {"name": "alternate_id", "source_column": "order_id"}
    )

    with pytest.raises(
        model_yaml.SemanticModelYamlError,
        match="Multiple attributes",
    ):
        model_yaml.import_model_yaml(
            yaml.safe_dump(root),
            validate_queries=False,
            embed=False,
        )


def test_import_rejects_unknown_sql_table_reference(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    fake = FakeDal()
    _install_fake_dal(monkeypatch, fake)
    root = yaml.safe_load(_native_model_yaml())
    root["terms"][0]["sql_attributes"][0]["table_refs"] = ["missing"]

    with pytest.raises(
        model_yaml.SemanticModelYamlError,
        match="unknown terms",
    ):
        model_yaml.import_model_yaml(
            yaml.safe_dump(root),
            validate_queries=False,
            embed=False,
        )


def test_import_rejects_table_represented_by_another_term(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    fake = FakeDal()
    _install_fake_dal(monkeypatch, fake)
    monkeypatch.setattr(
        model_yaml.model_dal,
        "find_table_conflicts",
        lambda *_args, **_kwargs: [{"name": "orders"}],
    )

    with pytest.raises(
        model_yaml.SemanticModelYamlError,
        match="already represented",
    ):
        model_yaml.import_model_yaml(
            _native_model_yaml(),
            validate_queries=False,
            embed=False,
        )


def test_parse_rejects_wrong_native_version() -> None:
    root = yaml.safe_load(_native_model_yaml())
    root["version"] = "2.0"

    with pytest.raises(
        model_yaml.SemanticModelYamlError,
        match="Unsupported GSF model version",
    ):
        model_yaml._parse_model_yaml(yaml.safe_dump(root))


def test_export_builds_native_gsf_document(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(
        model_yaml.model_dal,
        "read_models",
        lambda **_kwargs: [
            {
                "name": "sales",
                "description": "Sales model",
                "ai_context": None,
            }
        ],
    )
    monkeypatch.setattr(
        model_yaml.model_dal,
        "read_datasets",
        lambda **_kwargs: [
            {
                "term_name": "orders",
                "description": "Orders",
                "synonyms": ["purchases"],
                "ai_context": None,
                "database_name": "analytics",
                "schema_name": "public",
                "table_name": "orders",
            },
            {
                "term_name": "customers",
                "description": None,
                "synonyms": [],
                "ai_context": None,
                "database_name": "analytics",
                "schema_name": "public",
                "table_name": "customers",
            },
        ],
    )
    monkeypatch.setattr(
        model_yaml.model_dal,
        "read_column_attributes",
        lambda **_kwargs: [
            {
                "term_name": "orders",
                "name": "order_id",
                "source_column": "order_id",
                "description": None,
                "ai_context": None,
                "original": json.dumps(
                    {"name": "order_id", "source_column": "order_id"}
                ),
            }
        ],
    )
    monkeypatch.setattr(
        model_yaml.model_dal,
        "read_sql_attributes",
        lambda **_kwargs: [
            {
                "term_name": "orders",
                "name": "revenue",
                "kind": "metric",
                "description": "Revenue",
                "expression": "SUM(orders.amount)",
                "sql": "SELECT SUM(orders.amount) FROM orders",
                "table_refs": ["orders"],
                "original": json.dumps(
                    {
                        "name": "revenue",
                        "kind": "metric",
                        "expressions": [
                            {
                                "dialect": "ANSI_SQL",
                                "expression": "SUM(orders.amount)",
                            }
                        ],
                    }
                ),
            },
            {
                "term_name": "customers",
                "name": "native_attribute",
                "kind": None,
                "description": None,
                "expression": "UPPER(customers.name)",
                "sql": "SELECT UPPER(customers.name) FROM customers",
                "table_refs": ["customers"],
                "original": None,
            },
        ],
    )
    monkeypatch.setattr(
        model_yaml.model_dal,
        "read_relationships",
        lambda **_kwargs: [
            {
                "name": "orders_to_customers",
                "from_dataset": "orders",
                "to_dataset": "customers",
                "from_columns": ["customer_id"],
                "to_columns": ["customer_id"],
            }
        ],
    )

    output = model_yaml.export_model_yaml(
        database_name="analytics",
        model_name="sales",
    )
    root = yaml.safe_load(output)

    assert set(root) == {
        "version",
        "model",
        "terms",
        "semantic_foreign_keys",
    }
    assert root["model"]["database"] == "analytics"
    assert root["terms"][0]["sql_attributes"][0]["kind"] == "metric"
    assert root["terms"][1]["sql_attributes"][0]["kind"] == "attribute"
    assert root["semantic_foreign_keys"][0]["from_term"] == "orders"
