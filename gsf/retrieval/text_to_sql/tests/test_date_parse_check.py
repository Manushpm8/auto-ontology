# SPDX-FileCopyrightText: Copyright (c) 2026, NVIDIA CORPORATION & AFFILIATES.
# All rights reserved.
# SPDX-License-Identifier: Apache-2.0

import sqlite3

import pandas as pd
import pytest

from gsf.retrieval.text_to_sql.db_probe.date_parse_check import (
    build_date_repair_error,
    find_date_faults,
)
from gsf.retrieval.text_to_sql.db_probe.executor import ProbeExecutor

# The year extraction that emptied sf_local299/301/302: a two-digit year is
# zero-padded to four characters, so '31/8/20' yields year '0020'.
_BUGGY_YEAR_PARSE = """
    substr('0000' || CAST(substr(week_date, instr(week_date, '/') +
        instr(substr(week_date, instr(week_date, '/') + 1), '/') + 1)
        AS INTEGER), -4, 4)
    || '-08-31'
"""


class _SqliteConnector:
    """Minimal stand-in for the pipeline's SQLDatabase connector."""

    dialect = "sqlite"

    def __init__(self, connection: sqlite3.Connection) -> None:
        self._connection = connection

    def execute(self, sql: str) -> pd.DataFrame:
        return pd.read_sql_query(sql, self._connection)


@pytest.fixture
def weekly_sales() -> _SqliteConnector:
    connection = sqlite3.connect(":memory:")
    connection.execute("CREATE TABLE weekly_sales (week_date TEXT, sales INTEGER)")
    connection.executemany(
        "INSERT INTO weekly_sales VALUES (?, ?)",
        [("31/8/20", 100), ("24/8/20", 200), ("31/8/19", 300)],
    )
    connection.commit()
    return _SqliteConnector(connection)


@pytest.fixture
def cleaned_weekly_sales() -> _SqliteConnector:
    connection = sqlite3.connect(":memory:")
    connection.execute(
        "CREATE TABLE cleaned_weekly_sales (week_date TEXT, sales INTEGER)"
    )
    connection.executemany(
        "INSERT INTO cleaned_weekly_sales VALUES (?, ?)",
        [("2020-08-31", 100), ("2019-08-31", 300)],
    )
    connection.commit()
    return _SqliteConnector(connection)


@pytest.fixture
def decoy_column_table() -> _SqliteConnector:
    """The sf_local301/302 trap: the column named 'formatted' is the broken one."""
    connection = sqlite3.connect(":memory:")
    connection.execute(
        "CREATE TABLE cleaned_weekly_sales ("
        "week_date TEXT, week_date_formatted TEXT, sales INTEGER)"
    )
    connection.executemany(
        "INSERT INTO cleaned_weekly_sales VALUES (?, ?, ?)",
        [
            ("2020-08-31", "2020-8-31", 100),
            ("2020-08-24", "2020-8-24", 200),
            ("2019-08-31", "2019-8-31", 300),
        ],
    )
    connection.commit()
    return _SqliteConnector(connection)


def _findings(connector: _SqliteConnector, sql: str) -> list[dict]:
    with ProbeExecutor(connector) as executor:
        return find_date_faults(executor, "sqlite", sql)


def test_reports_two_digit_year_parsed_as_year_twenty(weekly_sales) -> None:
    sql = f"""
        SELECT SUM(sales) AS total FROM weekly_sales
        WHERE {_BUGGY_YEAR_PARSE} BETWEEN '2020-06-01' AND '2020-06-30'
    """

    findings = _findings(weekly_sales, sql)

    assert len(findings) == 1
    assert findings[0]["table"] == "weekly_sales"
    assert findings[0]["column"] == "week_date"
    derived = [derived for _, derived in findings[0]["samples"]]
    assert "0020-08-31" in derived


def test_correct_parse_on_clean_dates_is_not_flagged(cleaned_weekly_sales) -> None:
    sql = """
        SELECT SUM(sales) AS total FROM cleaned_weekly_sales
        WHERE substr(week_date, 1, 7) = '2020-06'
    """

    assert _findings(cleaned_weekly_sales, sql) == []


def test_silent_null_parse_is_flagged(weekly_sales) -> None:
    # strftime cannot read '31/8/20' and returns NULL rather than erroring.
    sql = """
        SELECT SUM(sales) AS total FROM weekly_sales
        WHERE strftime('%Y', week_date) = '2020'
    """

    findings = _findings(weekly_sales, sql)

    assert len(findings) == 1
    assert all(derived is None for _, derived in findings[0]["samples"])


def test_bare_date_column_without_derivation_is_ignored(cleaned_weekly_sales) -> None:
    sql = "SELECT sales FROM cleaned_weekly_sales WHERE week_date = '2020-08-31'"

    assert _findings(cleaned_weekly_sales, sql) == []


def test_string_surgery_on_a_non_date_column_is_ignored() -> None:
    connection = sqlite3.connect(":memory:")
    connection.execute("CREATE TABLE people (name TEXT)")
    connection.execute("INSERT INTO people VALUES ('Ada')")
    connection.commit()
    sql = "SELECT substr(name, 1, 1) AS initial FROM people"

    assert _findings(_SqliteConnector(connection), sql) == []


def test_unparseable_sql_is_ignored(weekly_sales) -> None:
    assert _findings(weekly_sales, "SELECT FROM WHERE ((") == []


def test_missing_connector_yields_no_findings() -> None:
    with ProbeExecutor(None) as executor:
        assert find_date_faults(executor, "sqlite", "SELECT 1") == []


def test_names_the_sibling_column_that_actually_parses(decoy_column_table) -> None:
    sql = """
        SELECT SUM(sales) FROM cleaned_weekly_sales
        WHERE date(week_date_formatted) >= '2020-06-15'
    """

    findings = _findings(decoy_column_table, sql)

    assert len(findings) == 1
    assert findings[0]["column"] == "week_date_formatted"
    assert findings[0]["alternative"] == ("week_date", "2020-08-31")


def test_no_alternative_offered_when_no_sibling_is_usable(weekly_sales) -> None:
    sql = "SELECT sales FROM weekly_sales WHERE strftime('%Y', week_date) = '2020'"

    findings = _findings(weekly_sales, sql)

    assert findings[0]["alternative"] is None


def test_flags_lexicographic_comparison_on_a_ragged_date_column(
    decoy_column_table,
) -> None:
    # sf_local301's shape: '2020-8-31' <= '2020-06-15' is false because '8'
    # sorts after '0', so the predicate silently matches nothing.
    sql = """
        SELECT DISTINCT calendar_year FROM cleaned_weekly_sales
        WHERE week_date_formatted <= '2020-06-15'
    """

    findings = _findings(decoy_column_table, sql)

    assert [f["kind"] for f in findings] == ["comparison"]
    assert findings[0]["column"] == "week_date_formatted"
    assert findings[0]["alternative"] == ("week_date", "2020-08-31")


def test_flags_comparison_against_a_concatenated_month_day_suffix(
    decoy_column_table,
) -> None:
    sql = """
        SELECT sales FROM cleaned_weekly_sales
        WHERE week_date_formatted <= '2020' || '-06-15'
    """

    assert [f["kind"] for f in _findings(decoy_column_table, sql)] == ["comparison"]


def test_comparison_on_a_properly_padded_column_is_not_flagged(
    decoy_column_table,
) -> None:
    sql = """
        SELECT sales FROM cleaned_weekly_sales
        WHERE week_date <= '2020-06-15'
    """

    assert _findings(decoy_column_table, sql) == []


def test_comparison_between_two_columns_is_not_flagged(decoy_column_table) -> None:
    sql = (
        "SELECT sales FROM cleaned_weekly_sales WHERE week_date_formatted <= week_date"
    )

    assert _findings(decoy_column_table, sql) == []


def test_comparison_error_explains_the_text_sort() -> None:
    message = build_date_repair_error(
        [
            {
                "kind": "comparison",
                "table": "cleaned_weekly_sales",
                "column": "week_date_formatted",
                "predicate": "week_date_formatted <= '2020-06-15'",
                "samples": ["2020-8-31"],
                "alternative": ("week_date", "2020-08-31"),
            }
        ]
    )

    assert "week_date_formatted <= '2020-06-15'" in message
    assert "sorts after" in message
    assert "Use cleaned_weekly_sales.week_date instead" in message


def test_error_points_at_the_usable_column_when_one_exists() -> None:
    message = build_date_repair_error(
        [
            {
                "table": "cleaned_weekly_sales",
                "column": "week_date_formatted",
                "expression": "date(week_date_formatted)",
                "samples": [("2020-8-31", None)],
                "alternative": ("week_date", "2020-08-31"),
            }
        ]
    )

    assert "Use cleaned_weekly_sales.week_date instead" in message
    assert "'2020-08-31'" in message


def test_error_shows_the_parse_and_the_two_digit_year_advice() -> None:
    message = build_date_repair_error(
        [
            {
                "table": "weekly_sales",
                "column": "week_date",
                "expression": "substr(...)",
                "samples": [("31/8/20", "0020-08-31")],
                "alternative": None,
            }
        ]
    )

    assert "weekly_sales.week_date" in message
    assert "'31/8/20' -> '0020-08-31'" in message
    assert "two-digit year" in message
