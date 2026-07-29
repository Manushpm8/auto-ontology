# SPDX-FileCopyrightText: Copyright (c) 2026, NVIDIA CORPORATION & AFFILIATES.
# All rights reserved.
# SPDX-License-Identifier: Apache-2.0

import sqlite3

import pandas as pd
import pytest

from gsf.retrieval.text_to_sql.db_probe.executor import ProbeExecutor
from gsf.retrieval.text_to_sql.db_probe.join_overlap_check import (
    build_join_repair_error,
    find_empty_joins,
)


class _SqliteConnector:
    """Minimal stand-in for the pipeline's SQLDatabase connector."""

    dialect = "sqlite"

    def __init__(self, connection: sqlite3.Connection) -> None:
        self._connection = connection

    def execute(self, sql: str) -> pd.DataFrame:
        return pd.read_sql_query(sql, self._connection)


@pytest.fixture
def city_legislation() -> _SqliteConnector:
    """The sf_local070 shape: a date dimension that predates the fact table.

    Both columns are well-formed ISO dates with the same name shape, so nothing
    about the SQL looks wrong — the ranges simply do not intersect.
    """
    connection = sqlite3.connect(":memory:")
    connection.execute("CREATE TABLE cities (city_name TEXT, country_code_2 TEXT, insert_date TEXT)")
    connection.execute("CREATE TABLE legislation_date_dim (date TEXT, month_name TEXT)")
    connection.executemany(
        "INSERT INTO cities VALUES (?, ?, ?)",
        [("Gaotan", "cn", "2021-07-12"), ("Xiaoganzhan", "cn", "2021-07-13")],
    )
    connection.executemany(
        "INSERT INTO legislation_date_dim VALUES (?, ?)",
        [("1917-01-01", "January"), ("1999-12-31", "December")],
    )
    connection.commit()
    return _SqliteConnector(connection)


@pytest.fixture
def orders() -> _SqliteConnector:
    """A pair of tables that do share join-key values."""
    connection = sqlite3.connect(":memory:")
    connection.execute("CREATE TABLE orders (order_id TEXT, customer_id TEXT)")
    connection.execute("CREATE TABLE customers (customer_id TEXT, city TEXT)")
    connection.executemany(
        "INSERT INTO orders VALUES (?, ?)", [("o1", "c1"), ("o2", "c2")]
    )
    connection.executemany(
        "INSERT INTO customers VALUES (?, ?)", [("c1", "Rio"), ("c2", "Lima")]
    )
    connection.commit()
    return _SqliteConnector(connection)


def test_flags_a_join_whose_sides_never_intersect(city_legislation) -> None:
    sql = """
        SELECT d.date, c.city_name
        FROM legislation_date_dim AS d
        JOIN cities AS c ON c.insert_date = d.date
        WHERE c.country_code_2 = 'cn'
          AND d.date BETWEEN '2021-07-01' AND '2021-07-31'
    """
    with ProbeExecutor(city_legislation) as executor:
        findings = find_empty_joins(executor, "sqlite", sql)

    assert len(findings) == 1
    key = findings[0]["keys"][0]
    assert {key["left"]["table"], key["right"]["table"]} == {
        "cities",
        "legislation_date_dim",
    }
    # The ranges are what let the model see the dimension cannot cover 2021.
    profiles = {side["table"]: side for side in (key["left"], key["right"])}
    assert profiles["legislation_date_dim"]["max"] == "1999-12-31"
    assert profiles["cities"]["min"] == "2021-07-12"


def test_overlapping_join_is_not_flagged(orders) -> None:
    sql = """
        SELECT o.order_id, c.city
        FROM orders AS o
        JOIN customers AS c ON o.customer_id = c.customer_id
    """
    with ProbeExecutor(orders) as executor:
        assert find_empty_joins(executor, "sqlite", sql) == []


def test_filters_that_empty_the_result_do_not_trigger_a_join_finding(orders) -> None:
    """A filter-caused empty result must not be blamed on the join."""
    sql = """
        SELECT o.order_id
        FROM orders AS o
        JOIN customers AS c ON o.customer_id = c.customer_id
        WHERE c.city = 'Atlantis'
    """
    with ProbeExecutor(orders) as executor:
        assert find_empty_joins(executor, "sqlite", sql) == []


def test_joins_between_ctes_are_skipped(city_legislation) -> None:
    """CTE names parse as tables but cannot be probed, so they must be ignored."""
    sql = """
        WITH a AS (SELECT insert_date FROM cities),
             b AS (SELECT date FROM legislation_date_dim)
        SELECT a.insert_date FROM a JOIN b ON a.insert_date = b.date
    """
    with ProbeExecutor(city_legislation) as executor:
        assert find_empty_joins(executor, "sqlite", sql) == []


def test_real_join_inside_a_cte_is_still_checked(city_legislation) -> None:
    """The sf_local070 SQL wrapped its bad join in a CTE; that must still count."""
    sql = """
        WITH daily AS (
            SELECT d.date AS dt, c.city_name
            FROM legislation_date_dim AS d
            JOIN cities AS c ON c.insert_date = d.date
            WHERE c.country_code_2 = 'cn'
        )
        SELECT dt, city_name FROM daily ORDER BY dt
    """
    with ProbeExecutor(city_legislation) as executor:
        findings = find_empty_joins(executor, "sqlite", sql)

    assert len(findings) == 1


def test_unqualified_join_columns_are_skipped(city_legislation) -> None:
    """Without a qualifier a column cannot be attributed to one side."""
    sql = """
        SELECT city_name
        FROM legislation_date_dim
        JOIN cities ON insert_date = date
    """
    with ProbeExecutor(city_legislation) as executor:
        assert find_empty_joins(executor, "sqlite", sql) == []


def test_unparseable_sql_returns_no_findings(city_legislation) -> None:
    with ProbeExecutor(city_legislation) as executor:
        assert find_empty_joins(executor, "sqlite", "SELECT FROM WHERE ((") == []


def test_exhausted_budget_is_survivable(city_legislation) -> None:
    sql = """
        SELECT d.date FROM legislation_date_dim AS d
        JOIN cities AS c ON c.insert_date = d.date
    """
    executor = ProbeExecutor(city_legislation, max_calls=0)
    assert find_empty_joins(executor, "sqlite", sql) == []


def test_error_explains_the_ranges_and_the_way_out(city_legislation) -> None:
    sql = """
        SELECT d.date, c.city_name FROM legislation_date_dim AS d
        JOIN cities AS c ON c.insert_date = d.date
    """
    with ProbeExecutor(city_legislation) as executor:
        message = build_join_repair_error(find_empty_joins(executor, "sqlite", sql))

    assert "matches no rows at all" in message
    assert "1999-12-31" in message and "2021-07-12" in message
    assert "no WHERE clause can rescue" in message
