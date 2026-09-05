# SPDX-FileCopyrightText: Copyright (c) 2026, NVIDIA CORPORATION & AFFILIATES.
# All rights reserved.
# SPDX-License-Identifier: Apache-2.0

"""Literal-vs-database checks on the empty-result repair path.

Two tiers, split by what the probe can honestly conclude. On a column whose
values can be enumerated, a near-miss is correctable and the real value is
named. On a column too large to enumerate the value set is unknown, but "do any
of these values occur at all" is still answerable — which is the case
``sf_local062``/``sf_local067`` needed: twenty invented Italian region names
against a ``cust_state_province`` column holding 145 unrelated values, where
the old low-cardinality gate skipped the column and let the empty result pass
through as if it were the answer.
"""

from __future__ import annotations

import sqlite3

import pandas as pd
import pytest

from gsf.retrieval.text_to_sql.db_probe.executor import ProbeExecutor
from gsf.retrieval.text_to_sql.db_probe.literal_check import (
    build_value_repair_error,
    find_literal_mismatches,
)


class _SqliteConnector:
    """Minimal stand-in for the pipeline's SQLDatabase connector."""

    dialect = "sqlite"

    def __init__(self, connection: sqlite3.Connection) -> None:
        self._connection = connection

    def execute(self, sql: str) -> pd.DataFrame:
        return pd.read_sql_query(sql, self._connection)


@pytest.fixture
def customers() -> _SqliteConnector:
    """``cust_state_province`` holds far more values than can be enumerated.

    Deliberately over the low-cardinality threshold and containing nothing
    Italian, mirroring the real table.
    """
    connection = sqlite3.connect(":memory:")
    connection.execute(
        "CREATE TABLE customers (cust_id INTEGER, cust_state_province TEXT, "
        "cust_credit_limit TEXT)"
    )
    provinces = [f"Province {n:02d}" for n in range(40)] + ["Zeeland", "Utrecht"]
    connection.executemany(
        "INSERT INTO customers VALUES (?, ?, ?)",
        [(i, p, "1500") for i, p in enumerate(provinces)],
    )
    connection.commit()
    return _SqliteConnector(connection)


@pytest.fixture
def orders() -> _SqliteConnector:
    """``status`` is small enough to enumerate, so near-misses are correctable."""
    connection = sqlite3.connect(":memory:")
    connection.execute("CREATE TABLE orders (order_id INTEGER, status TEXT)")
    connection.executemany(
        "INSERT INTO orders VALUES (?, ?)",
        [(1, "Shipped"), (2, "Pending"), (3, "Cancelled")],
    )
    connection.commit()
    return _SqliteConnector(connection)


def _run(connector: _SqliteConnector, sql: str):
    with ProbeExecutor(connector) as executor:
        return find_literal_mismatches(executor, "sqlite", sql), executor.calls


def test_a_filter_matching_nothing_is_caught_on_a_large_column(customers) -> None:
    """The sf_local062 regression: 20 invented values, none of them present."""
    findings, _ = _run(
        customers,
        "SELECT cust_id FROM customers WHERE cust_state_province IN "
        "('Abruzzo', 'Lazio', 'Veneto', 'Toscana')",
    )

    assert len(findings) == 1
    assert findings[0]["column"] == "cust_state_province"
    assert findings[0]["exhaustive"] is False
    assert findings[0]["used_values"] == ["Abruzzo", "Lazio", "Veneto", "Toscana"]
    # No real value set to score against, so nothing may be suggested.
    assert findings[0]["suggested"] == []


def test_a_large_column_is_left_alone_when_the_filter_does_match(customers) -> None:
    """A satisfiable filter is not the cause, so it must not be reported.

    This is the guard against repairing away a legitimately empty answer: the
    rows the filter selects exist, and the emptiness came from somewhere the
    literal check has no opinion about.
    """
    findings, _ = _run(
        customers,
        "SELECT cust_id FROM customers WHERE cust_state_province IN "
        "('Zeeland', 'Utrecht')",
    )

    assert findings == []


def test_detecting_on_a_large_column_costs_two_probes(customers) -> None:
    """One probe to resolve the column, one to count matches — not a value dump."""
    _, calls = _run(
        customers,
        "SELECT cust_id FROM customers WHERE cust_state_province = 'Lazio'",
    )

    assert calls == 2


def test_a_near_miss_on_a_small_column_still_names_the_real_value(orders) -> None:
    findings, _ = _run(orders, "SELECT order_id FROM orders WHERE status = 'shipped'")

    assert len(findings) == 1
    assert findings[0]["exhaustive"] is True
    assert findings[0]["suggested"] == ["Shipped"]


def test_an_absent_value_on_a_small_column_is_not_repaired(orders) -> None:
    """Enumerable column, no near-miss: the empty result is the honest answer."""
    findings, _ = _run(orders, "SELECT order_id FROM orders WHERE status = 'Refunded'")

    assert findings == []


def test_the_large_column_message_forbids_guessing_from_the_examples(
    customers,
) -> None:
    """Sample values illustrate the shape; they are not replacement candidates."""
    findings, _ = _run(
        customers, "SELECT cust_id FROM customers WHERE cust_state_province = 'Lazio'"
    )

    message = build_value_repair_error(findings)

    assert "Do not guess a replacement" in message
    assert "join to that table" in message
    # The swap-this-literal closing would be wrong here: no value was found.
    assert "Change ONLY the mismatched literal(s)" not in message


def test_the_small_column_message_keeps_the_swap_instruction(orders) -> None:
    findings, _ = _run(orders, "SELECT order_id FROM orders WHERE status = 'shipped'")

    message = build_value_repair_error(findings)

    assert "Closest real value(s): Shipped" in message
    assert "Change ONLY the mismatched literal(s)" in message
