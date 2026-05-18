# SPDX-FileCopyrightText: Copyright (c) 2024-2026, NVIDIA CORPORATION & AFFILIATES.
# All rights reserved.
# SPDX-License-Identifier: Apache-2.0

"""PostgreSQL connector implementing the NeMo-Retriever SQLDatabase ABC."""

from __future__ import annotations

import logging
from typing import Optional

import pandas as pd
import psycopg
import sqlglot
from psycopg.rows import dict_row

from nemo_retriever.tabular_data.sql_database import SQLDatabase

logger = logging.getLogger(__name__)


class PostgresDatabase(SQLDatabase):
    """Concrete :class:`SQLDatabase` backed by ``psycopg`` (v3).

    Parameters
    ----------
    connection_string:
        A ``libpq``-style connection URI, e.g.
        ``postgresql://user:pass@host:5432/dbname``.

    Notes
    -----
    ``execute`` auto-qualifies unqualified table refs against the live
    Postgres catalog (``pg_class``/``pg_namespace``) using ``sqlglot``: if
    the SQL says ``FROM orders`` and ``orders`` exists in exactly one user
    schema (say, ``sales``), the query is rewritten to ``FROM sales.orders``
    before execution. This lets the text-to-SQL agent produce unqualified
    names (which is what it tends to do when the prompt's ``TABLE:`` lines
    arrive without a schema) without depending on Postgres ``search_path``.
    Ambiguous names (same table in multiple schemas) and already-qualified
    refs are left untouched.
    """

    def __init__(self, connection_string: str) -> None:
        self._connection_string = connection_string
        self._table_schemas: dict[str, list[str]] | None = None
        self._conn: psycopg.Connection = psycopg.connect(connection_string)
        self._database_name: str = self.execute("SELECT current_database()").iloc[0, 0]

    @property
    def dialect(self) -> str:
        return "postgres"

    @property
    def database_name(self) -> str:
        return self._database_name

    # ------------------------------------------------------------------
    # Execution
    # ------------------------------------------------------------------

    def execute(self, sql: str, parameters: Optional[list] = None) -> pd.DataFrame:
        # Parameterised queries are always emitted by us (introspection,
        # ingest); only LLM-generated SQL arrives without parameters and
        # benefits from schema-qualification.
        if parameters is None and sql:
            sql = self._qualify_unqualified_tables(sql)
        try:
            with self._conn.cursor(row_factory=dict_row) as cur:
                cur.execute(sql, parameters)
                if cur.description is None:
                    self._conn.commit()
                    return pd.DataFrame()
                rows = cur.fetchall()
            return pd.DataFrame(rows)
        except psycopg.Error:
            # Postgres poisons the current transaction on any failure: every
            # subsequent statement raises InFailedSqlTransaction until ROLLBACK.
            # The text-to-SQL agent retries with a corrected query, so we must
            # leave the connection in a clean state for the next attempt.
            self._conn.rollback()
            raise

    # ------------------------------------------------------------------
    # Table-name qualification
    # ------------------------------------------------------------------

    def _load_table_catalog(self) -> dict[str, list[str]]:
        """Map lowercased table name → list of schemas that contain it.

        Uses ``pg_class``/``pg_namespace`` directly via a raw cursor (not
        ``self.execute``) to avoid recursing through ``_qualify_*`` while
        the catalog is being built.
        """
        catalog: dict[str, list[str]] = {}
        with self._conn.cursor() as cur:
            cur.execute(
                """
                SELECT n.nspname AS table_schema, c.relname AS table_name
                FROM pg_class c
                JOIN pg_namespace n ON n.oid = c.relnamespace
                WHERE c.relkind IN ('r', 'v', 'm', 'p', 'f')
                  AND n.nspname NOT IN ('pg_catalog', 'information_schema')
                  AND c.relispartition = false
                """
            )
            for schema, table in cur.fetchall():
                catalog.setdefault(table.lower(), []).append(schema)
        return catalog

    def _qualify_unqualified_tables(self, sql: str) -> str:
        """Rewrite ``FROM orders`` to ``FROM sales.orders`` when unambiguous.

        Already-qualified refs (``public.orders``, ``sales.orders``) are
        left untouched, as are bare names that exist in multiple schemas
        (where guessing would silently pick the wrong table) and names
        absent from the catalog entirely (Postgres will raise a clear
        ``UndefinedTable`` and the agent loop will retry).
        """
        if self._table_schemas is None:
            try:
                self._table_schemas = self._load_table_catalog()
            except psycopg.Error:
                logger.exception(
                    "Failed to load Postgres table catalog; "
                    "skipping auto-qualification for this session"
                )
                self._conn.rollback()
                self._table_schemas = {}
        if not self._table_schemas:
            return sql

        try:
            tree = sqlglot.parse_one(sql, dialect="postgres")
        except sqlglot.errors.ParseError:
            return sql
        if tree is None:
            return sql

        rewrote = False
        for table_expr in tree.find_all(sqlglot.exp.Table):
            if table_expr.db:
                continue
            bare = table_expr.name
            if not bare:
                continue
            candidates = self._table_schemas.get(bare.lower(), [])
            if len(candidates) == 1:
                table_expr.set("db", sqlglot.exp.to_identifier(candidates[0]))
                rewrote = True
        return tree.sql(dialect="postgres") if rewrote else sql

    # ------------------------------------------------------------------
    # Schema introspection
    # ------------------------------------------------------------------

    def get_tables(self) -> pd.DataFrame:
        # Filter tables that are part of partitioned tables
        return self.execute("""
            SELECT
                t.table_schema    AS table_schema,
                t.table_name      AS table_name,
                t.table_type      AS table_type
            FROM information_schema.tables t
            JOIN pg_namespace n ON n.nspname = t.table_schema
            JOIN pg_class c ON c.relname = t.table_name AND c.relnamespace = n.oid
            WHERE t.table_schema NOT IN ('pg_catalog', 'information_schema')
              AND c.relispartition = false
            ORDER BY t.table_schema, t.table_name
        """)

    def get_columns(self) -> pd.DataFrame:
        return self.execute("""
            SELECT
                c.table_schema       AS table_schema,
                c.table_name         AS table_name,
                c.column_name        AS column_name,
                c.data_type          AS data_type,
                c.is_nullable        AS is_nullable,
                c.ordinal_position   AS ordinal_position
            FROM information_schema.columns c
            JOIN pg_namespace n ON n.nspname = c.table_schema
            JOIN pg_class pc ON pc.relname = c.table_name AND pc.relnamespace = n.oid
            WHERE c.table_schema NOT IN ('pg_catalog', 'information_schema')
              AND pc.relispartition = false
            ORDER BY c.table_schema, c.table_name, c.ordinal_position
        """)

    def get_queries(self, hours: int = 24) -> pd.DataFrame:
        """Return recent queries from ``pg_stat_activity``.

        Uses ``state_change`` as ``end_time`` and filters to the last ``hours``
        hours. Excludes the current backend and rows with no recorded query.
        """
        try:
            # Todo: Add filter of exclude information schema and pg_catalog tables
            return self.execute(
                """
                SELECT
                    state_change AS end_time,
                    query        AS query_text
                FROM pg_stat_activity
                WHERE pid != pg_backend_pid()
                  AND query IS NOT NULL
                  AND query <> ''
                  AND state_change >= now() - make_interval(hours => %s)
                ORDER BY state_change DESC
                """,
                [hours],
            )
        except psycopg.Error:
            self._conn.rollback()
            return pd.DataFrame(columns=["end_time", "query_text"])

    def get_views(self) -> pd.DataFrame:
        return self.execute("""
            SELECT
                v.table_schema     AS table_schema,
                v.table_name       AS table_name,
                v.view_definition  AS view_definition
            FROM information_schema.views v
            WHERE v.table_schema NOT IN ('pg_catalog', 'information_schema')
            ORDER BY v.table_schema, v.table_name
        """)

    def get_pks(self) -> pd.DataFrame:
        return self.execute("""
            SELECT
                kcu.table_schema         AS table_schema,
                kcu.table_name           AS table_name,
                kcu.column_name          AS column_name,
                kcu.ordinal_position     AS ordinal_position
            FROM information_schema.table_constraints tc
            JOIN information_schema.key_column_usage kcu
              ON tc.constraint_name = kcu.constraint_name
             AND tc.table_schema    = kcu.table_schema
            WHERE tc.constraint_type = 'PRIMARY KEY'
              AND tc.table_schema NOT IN ('pg_catalog', 'information_schema')
            ORDER BY kcu.table_schema, kcu.table_name, kcu.ordinal_position
        """)

    def get_fks(self) -> pd.DataFrame:
        return self.execute("""
            SELECT
                kcu.table_schema         AS table_schema,
                kcu.table_name           AS table_name,
                kcu.column_name          AS column_name,
                ccu.table_schema         AS referenced_schema,
                ccu.table_name           AS referenced_table,
                ccu.column_name          AS referenced_column
            FROM information_schema.table_constraints tc
            JOIN information_schema.key_column_usage kcu
              ON tc.constraint_name = kcu.constraint_name
             AND tc.table_schema    = kcu.table_schema
            JOIN information_schema.constraint_column_usage ccu
              ON tc.constraint_name = ccu.constraint_name
             AND tc.table_schema    = ccu.table_schema
            WHERE tc.constraint_type = 'FOREIGN KEY'
              AND tc.table_schema NOT IN ('pg_catalog', 'information_schema')
            ORDER BY kcu.table_schema, kcu.table_name, kcu.column_name
        """)

    # ------------------------------------------------------------------
    # Lifecycle
    # ------------------------------------------------------------------

    def close(self) -> None:
        if self._conn and not self._conn.closed:
            self._conn.close()
