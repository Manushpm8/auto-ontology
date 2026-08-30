# SPDX-FileCopyrightText: Copyright (c) 2026, NVIDIA CORPORATION & AFFILIATES.
# All rights reserved.
# SPDX-License-Identifier: Apache-2.0

from __future__ import annotations

import sqlite3
from typing import Any
from unittest.mock import MagicMock

import pytest

from gsf.retrieval.value_search import main


def _context(
    database_name: str = "people_db",
    sample_values: str | None = None,
) -> dict[str, Any]:
    context = {
        "database_name": database_name,
        "schema_name": "main",
        "table_name": "people",
        "col_name": "person",
    }
    if sample_values is not None:
        context["sample_values"] = sample_values
    return context


def test_lookup_combines_inputs_and_auto_selects_hit_database(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    captured: dict[str, Any] = {}

    def fake_search(
        _retriever: object,
        query: str,
        *,
        label_filter: list[str],
        per_label_k: int,
        database_name: str | None,
    ) -> list[dict[str, str]]:
        captured.update(
            {
                "query": query,
                "label_filter": label_filter,
                "per_label_k": per_label_k,
                "database_name": database_name,
            }
        )
        return [{"id": "attribute-1"}]

    executor = MagicMock()
    executor.__enter__.return_value = executor
    executor.run.return_value = {
        "ok": True,
        "rows": [{"matched_value": "Alex Shaked Hamelech"}],
    }
    monkeypatch.setattr(main, "search_semantic_index", fake_search)
    monkeypatch.setattr(
        main,
        "fetch_attr_column_contexts",
        lambda ids, database_name: {"attribute-1": _context()},
    )
    monkeypatch.setattr(main, "ProbeExecutor", lambda *args, **kwargs: executor)
    connector = MagicMock(database_name="people_db", dialect="sqlite")

    result = main.find_column_value(
        retriever=object(),
        connectors=[connector],
        value="alex shaked",
        description="a manager in NVIDIA working on GPUs",
    )

    assert captured["query"] == (
        "Description: a manager in NVIDIA working on GPUs\nValue: alex shaked"
    )
    assert captured["database_name"] is None
    assert captured["per_label_k"] == 1
    assert result == {
        "field": "main.people.person",
        "value": "Alex Shaked Hamelech",
    }
    assert executor.run.call_count == 1


def test_lookup_returns_matching_sample_without_database_probe(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(
        main,
        "search_semantic_index",
        lambda *args, **kwargs: [{"id": "attribute-1"}],
    )
    monkeypatch.setattr(
        main,
        "fetch_attr_column_contexts",
        lambda ids, database_name: {
            "attribute-1": _context(
                sample_values='["Someone Else", "Alex Shaked Hamelech"]'
            )
        },
    )
    mock_executor = MagicMock()
    monkeypatch.setattr(main, "ProbeExecutor", mock_executor)

    result = main.find_column_value(
        retriever=object(),
        connectors=[MagicMock(database_name="people_db")],
        value="alex shaked",
        description="a person",
    )

    assert result == {
        "field": "main.people.person",
        "value": "Alex Shaked Hamelech",
    }
    mock_executor.assert_not_called()


def test_lookup_filters_semantic_search_to_explicit_database(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    captured: dict[str, Any] = {}

    def fake_search(
        _retriever: object,
        _query: str,
        **kwargs: Any,
    ) -> list[dict[str, str]]:
        captured.update(kwargs)
        return []

    monkeypatch.setattr(main, "search_semantic_index", fake_search)
    connector = MagicMock(database_name="regional_sales")

    result = main.find_column_value(
        retriever=object(),
        connectors=[connector],
        value="Weimei",
        description="a customer",
        database_name="REGIONAL_SALES",
    )

    assert captured["database_name"] == "regional_sales"
    assert result == {"field": None, "value": None}


def test_lookup_returns_no_match_for_incomplete_column_context(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(
        main,
        "search_semantic_index",
        lambda *args, **kwargs: [{"id": "attribute-1"}],
    )
    monkeypatch.setattr(
        main,
        "fetch_attr_column_contexts",
        lambda ids, database_name: {},
    )
    mock_executor = MagicMock()
    monkeypatch.setattr(main, "ProbeExecutor", mock_executor)

    result = main.find_column_value(
        retriever=object(),
        connectors=[MagicMock(database_name="people_db")],
        value="Alex",
        description="a person",
    )

    assert result == {"field": None, "value": None}
    mock_executor.assert_not_called()


def test_generated_sql_is_quoted_bounded_and_matches_partial_value() -> None:
    sql = main.build_value_lookup_sql(
        _context(),
        "alex shaked",
        "sqlite",
    )

    assert sql is not None
    assert 'FROM "main"."people"' in sql
    assert sql.endswith("LIMIT 1")
    assert "LOWER(CAST(\"person\" AS TEXT)) LIKE '%alex%'" in sql
    assert "LOWER(CAST(\"person\" AS TEXT)) LIKE '%shaked%'" in sql

    connection = sqlite3.connect(":memory:")
    connection.execute('CREATE TABLE people ("person" TEXT)')
    connection.executemany(
        'INSERT INTO people ("person") VALUES (?)',
        [
            ("Alex Shaked Hamelech",),
            ("Professor Alex Shaked Hamelech",),
            ("Someone Else",),
        ],
    )
    row = connection.execute(sql).fetchone()

    assert row == ("Alex Shaked Hamelech",)


def test_generated_sql_does_not_interpolate_raw_sql_syntax() -> None:
    sql = main.build_value_lookup_sql(
        _context(),
        "alex' OR 1=1 --",
        "postgres",
    )

    assert sql is not None
    assert "alex' OR 1=1 --" not in sql
    assert sql.endswith("LIMIT 1")
