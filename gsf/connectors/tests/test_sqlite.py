# SPDX-FileCopyrightText: Copyright (c) 2026, NVIDIA CORPORATION & AFFILIATES.
# All rights reserved.
# SPDX-License-Identifier: Apache-2.0

"""The SQLite statement cap.

SQLite runs in-process with no server to enforce a timeout, so a plan the
planner mis-costs runs until it finishes. Two Spider2 questions took 18 and 25
minutes each on a query whose intended shape ran in well under a second, which
is what these tests guard: a cap exists, it fires on a runaway plan, and it
stays out of the way of ingestion.
"""

from __future__ import annotations

import sqlite3
import time

import pytest

from gsf.connectors.sqlite import SQLiteDatabase


@pytest.fixture
def db(tmp_path) -> SQLiteDatabase:
    path = tmp_path / "t.sqlite"
    conn = sqlite3.connect(path)
    conn.execute("CREATE TABLE t (id INTEGER PRIMARY KEY, label TEXT)")
    conn.executemany(
        "INSERT INTO t VALUES (?, ?)", [(i, f"row{i}") for i in range(2000)]
    )
    conn.commit()
    conn.close()
    return SQLiteDatabase(str(path))


# A cross join of four copies is 1.6e13 rows; ORDER BY denies SQLite any chance
# to return early, so this cannot finish within any cap under test.
RUNAWAY = "SELECT COUNT(*) FROM (SELECT a.id FROM t a, t b, t c, t d ORDER BY 1)"


def test_the_connector_advertises_a_statement_cap() -> None:
    """``execute_chat_sql`` only passes ``timeout_s`` to connectors claiming it."""
    assert SQLiteDatabase.supports_statement_timeout is True


def test_a_runaway_query_is_cancelled_at_the_cap(db: SQLiteDatabase) -> None:
    started = time.monotonic()

    with pytest.raises(sqlite3.OperationalError, match="statement timeout"):
        db.execute(RUNAWAY, timeout_s=1)

    # Generous upper bound: the point is that it returns at all, not that the
    # cap is precise. Uncapped this query runs for years.
    assert time.monotonic() - started < 30


def test_results_are_returned_normally_under_the_cap(db: SQLiteDatabase) -> None:
    frame = db.execute("SELECT label FROM t WHERE id = 7", timeout_s=30)

    assert frame["label"].tolist() == ["row7"]


def test_omitting_the_cap_leaves_the_query_uncapped(db: SQLiteDatabase) -> None:
    """Ingestion's metadata scans legitimately outlast a chat-turn cap."""
    frame = db.execute("SELECT COUNT(*) AS n FROM t")

    assert frame["n"].tolist() == [2000]


def test_an_ordinary_error_is_not_relabelled_as_a_timeout(db: SQLiteDatabase) -> None:
    """A cap must not disguise the syntax and missing-column errors agents rely on."""
    with pytest.raises(sqlite3.OperationalError, match="no such column"):
        db.execute("SELECT nope FROM t", timeout_s=30)


def test_the_guard_is_removed_after_the_cap_fires(db: SQLiteDatabase) -> None:
    """A leaked progress handler would abort every later query on this thread."""
    with pytest.raises(sqlite3.OperationalError):
        db.execute(RUNAWAY, timeout_s=1)

    assert db.execute("SELECT COUNT(*) AS n FROM t")["n"].tolist() == [2000]
