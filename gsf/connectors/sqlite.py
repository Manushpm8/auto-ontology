# SPDX-FileCopyrightText: Copyright (c) 2024-2026, NVIDIA CORPORATION & AFFILIATES.
# All rights reserved.
# SPDX-License-Identifier: Apache-2.0

"""SQLite connector implementing the NeMo-Retriever SQLDatabase ABC."""

from __future__ import annotations

import logging
import os
import sqlite3
import threading
import time
from pathlib import Path
from typing import Optional
from urllib.parse import unquote, urlparse

import pandas as pd

from nemo_retriever.tabular_data.ingestion.model.reserved_words import TableTypes
from nemo_retriever.tabular_data.sql_database import SQLDatabase

logger = logging.getLogger(__name__)


def _exec_timeout_s() -> float:
    """Wall-clock budget for a single query, ``0`` to disable (the default)."""
    try:
        return max(0.0, float(os.environ.get("SQL_EXEC_TIMEOUT_S", "0")))
    except ValueError:
        return 0.0


def _sqlite_path_from_connection_string(connection_string: str) -> Path:
    """Resolve a ``sqlite:///`` URI or bare filesystem path to a ``.sqlite`` file."""
    if connection_string.startswith("sqlite:"):
        parsed = urlparse(connection_string)
        path = unquote(parsed.path)
        if not path:
            raise ValueError(f"Invalid SQLite connection string: {connection_string!r}")
        return Path(path)
    return Path(connection_string)


class SQLiteDatabase(SQLDatabase):
    """Concrete :class:`SQLDatabase` backed by the stdlib ``sqlite3`` module.

    Parameters
    ----------
    connection_string:
        A SQLite URI or filesystem path, e.g.::

            sqlite:////absolute/path/to/db.sqlite
            /absolute/path/to/db.sqlite
    """

    def __init__(self, connection_string: str) -> None:
        db_path = _sqlite_path_from_connection_string(connection_string)
        if not db_path.exists():
            raise FileNotFoundError(f"SQLite database not found: {db_path}")
        self._db_path = db_path
        self._connection_string = connection_string
        self._database_name = db_path.stem
        # sqlite3 connections are bound to the thread that created them, but the
        # semantic-compilation pipeline profiles tables across a ThreadPoolExecutor.
        # Give each thread its own connection (mirroring the Postgres pool) so a
        # connection is never shared across threads.
        self._local = threading.local()
        self._all_conns: list[sqlite3.Connection] = []
        self._conns_lock = threading.Lock()
        self._unresolvable: set[str] = set()
        # Open eagerly on the constructing thread to surface connection errors early.
        self._conn
        logger.debug(
            "SQLite connected (database=%r, path=%s).", self._database_name, db_path
        )

    @property
    def _conn(self) -> sqlite3.Connection:
        """Return this thread's SQLite connection, creating it on first use."""
        conn = getattr(self._local, "conn", None)
        if conn is None:
            conn = sqlite3.connect(str(self._db_path))
            conn.row_factory = sqlite3.Row
            self._local.conn = conn
            with self._conns_lock:
                self._all_conns.append(conn)
        return conn

    @property
    def dialect(self) -> str:
        return "sqlite"

    @property
    def database_name(self) -> str:
        return self._database_name

    def execute(self, sql: str, parameters: Optional[list] = None) -> pd.DataFrame:
        conn = self._conn
        budget = _exec_timeout_s()
        if not budget:
            return self._execute(conn, sql, parameters)

        # SQLite computes rows lazily during fetch, so the handler has to stay
        # installed across both the execute and the fetch to bound either one.
        deadline = time.monotonic() + budget
        conn.set_progress_handler(
            lambda: 1 if time.monotonic() > deadline else 0, 10_000
        )
        try:
            return self._execute(conn, sql, parameters)
        except sqlite3.OperationalError as exc:
            if time.monotonic() > deadline:
                raise TimeoutError(
                    f"SQLite query exceeded SQL_EXEC_TIMEOUT_S={budget:g}s"
                ) from exc
            raise
        finally:
            conn.set_progress_handler(None, 0)

    @staticmethod
    def _execute(
        conn: sqlite3.Connection, sql: str, parameters: Optional[list] = None
    ) -> pd.DataFrame:
        cur = conn.execute(sql, parameters or [])
        if cur.description is None:
            return pd.DataFrame()
        rows = cur.fetchall()
        return pd.DataFrame(
            [dict(row) for row in rows], columns=[col[0] for col in cur.description]
        )

    def _resolves(self, table_name: str) -> bool:
        """Whether SQLite can resolve *table_name* to a column list.

        A view's definition is validated when it is created, not when its base
        tables change, so a database can ship a view naming a column that no
        longer exists. It is listed in ``sqlite_master`` like any other, and only
        ``PRAGMA table_info`` reveals the breakage. spider2's ``oracle_sql`` has
        one: the Oracle-to-SQLite conversion renamed ``emp_hire_periods``'s
        ``start_date``/``end_date`` to ``start_``/``end_`` without rewriting
        ``emp_hire_periods_with_name``, which still selects the old names.
        Dropping such a relation here keeps one unusable view from failing an
        entire ingest, and covers columns, primary keys and foreign keys at once
        since all three enumerate :meth:`get_tables`.
        """
        if table_name in self._unresolvable:
            return False
        try:
            self._conn.execute(f'PRAGMA table_info("{table_name}")').fetchall()
        except sqlite3.Error as exc:
            self._unresolvable.add(table_name)
            logger.warning(
                "Skipping %r in database %r — SQLite cannot resolve its columns: %s",
                table_name,
                self._database_name,
                exc,
            )
            return False
        return True

    def _drop_unresolvable(self, relations: pd.DataFrame) -> pd.DataFrame:
        """Remove rows naming a relation SQLite cannot introspect."""
        if relations.empty:
            return relations
        keep = relations["table_name"].map(self._resolves)
        return relations[keep].reset_index(drop=True)

    def get_tables(self) -> pd.DataFrame:
        view_type = TableTypes.VIEW
        base_table_type = TableTypes.BASE_TABLE
        return self._drop_unresolvable(
            self.execute(
                f"""
                SELECT
                    'main' AS table_schema,
                    name   AS table_name,
                    CASE type
                        WHEN 'view' THEN '{view_type}'
                        ELSE '{base_table_type}'
                    END AS table_type
                FROM sqlite_master
                WHERE type IN ('table', 'view')
                  AND name NOT LIKE 'sqlite_%'
                ORDER BY name
                """
            )
        )

    def get_columns(self) -> pd.DataFrame:
        tables = self.get_tables()
        rows: list[dict[str, object]] = []
        for table_name in tables["table_name"]:
            info = self._conn.execute(f'PRAGMA table_info("{table_name}")').fetchall()
            for cid, name, data_type, notnull, _default, _pk in info:
                rows.append(
                    {
                        "table_schema": "main",
                        "table_name": table_name,
                        "column_name": name,
                        "data_type": data_type or "TEXT",
                        "is_nullable": "NO" if notnull else "YES",
                        "ordinal_position": cid + 1,
                    }
                )
        return pd.DataFrame(rows)

    def get_queries(self, hours: int = 24) -> pd.DataFrame:
        return pd.DataFrame(columns=["end_time", "query_text"])

    def get_views(self) -> pd.DataFrame:
        return self._drop_unresolvable(
            self.execute(
                """
                SELECT
                    'main' AS table_schema,
                    name   AS table_name,
                    sql    AS view_definition
                FROM sqlite_master
                WHERE type = 'view'
                ORDER BY name
                """
            )
        )

    def get_pks(self) -> pd.DataFrame:
        tables = self.get_tables()
        rows: list[dict[str, object]] = []
        for table_name in tables["table_name"]:
            info = self._conn.execute(f'PRAGMA table_info("{table_name}")').fetchall()
            for cid, name, _type, _notnull, _default, pk in info:
                if pk > 0:
                    rows.append(
                        {
                            "table_schema": "main",
                            "table_name": table_name,
                            "column_name": name,
                            "ordinal_position": pk,
                        }
                    )
        return pd.DataFrame(rows)

    def get_fks(self) -> pd.DataFrame:
        tables = self.get_tables()
        rows: list[dict[str, object]] = []
        for table_name in tables["table_name"]:
            fks = self._conn.execute(
                f'PRAGMA foreign_key_list("{table_name}")'
            ).fetchall()
            for _id, _seq, ref_table, from_col, ref_col, *_rest in fks:
                rows.append(
                    {
                        "table_schema": "main",
                        "table_name": table_name,
                        "column_name": from_col,
                        "referenced_schema": "main",
                        "referenced_table": ref_table,
                        "referenced_column": ref_col,
                    }
                )
        return pd.DataFrame(rows)

    def close(self) -> None:
        with self._conns_lock:
            for conn in self._all_conns:
                try:
                    conn.close()
                except Exception:
                    logger.exception("Failed to close SQLite connection")
            self._all_conns.clear()
        self._local = threading.local()
