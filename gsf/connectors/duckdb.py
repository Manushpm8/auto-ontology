"""DuckDB connector for in-process SQL execution."""

from __future__ import annotations

import logging
from typing import Optional
import duckdb
import pandas as pd

from nemo_retriever.tabular_data.sql_database import SQLDatabase

logger = logging.getLogger(__name__)


class DuckDBDatabase(SQLDatabase):
    """In-process DuckDB connection implementing :class:`SQLDatabase`.

    Parameters
    ----------
    connection_string:
        Path to a persistent DuckDB database file, or ``":memory:"``
        for an ephemeral in-memory database.
    read_only:
        Open the database in read-only mode (default: True).
    """

    def __init__(self, connection_string: str, *, read_only: bool = True) -> None:
        self.conn = duckdb.connect(database=connection_string, read_only=read_only)
        logger.debug("DuckDB connected (database=%r, read_only=%s).", connection_string, read_only)

    @property
    def dialect(self) -> str:
        return "duckdb"

    # ------------------------------------------------------------------
    # Execution
    # ------------------------------------------------------------------

    def execute(self, sql: str, parameters: Optional[list] = None) -> pd.DataFrame:
        logger.debug("DuckDB executing: %s", sql[:200])
        if parameters:
            rel = self.conn.execute(sql, parameters)
        else:
            rel = self.conn.execute(sql)
        return rel.df()

    # ------------------------------------------------------------------
    # Schema introspection
    # ------------------------------------------------------------------

    def get_tables(self) -> pd.DataFrame:
        return self.execute(
            """
            SELECT
                table_catalog AS "database",
                table_schema  AS "schema",
                table_name    AS "table_name"
            FROM information_schema.tables
            ORDER BY table_catalog, table_schema, table_name
        """
        )

    def get_columns(self) -> pd.DataFrame:
        return self.execute(
            """
            SELECT
                table_catalog    AS "database",
                table_schema     AS "schema",
                table_name       AS "table_name",
                column_name      AS "column_name",
                data_type        AS "data_type",
                is_nullable      AS "is_nullable"
            FROM information_schema.columns
            ORDER BY table_catalog, table_schema, table_name, ordinal_position
        """
        )

    def get_queries(self) -> pd.DataFrame:
        return pd.DataFrame(columns=["end_time", "query_text"])

    def get_views(self) -> pd.DataFrame:
        return self.execute(
            """
            SELECT
                table_catalog   AS database,
                table_schema    AS schema,
                table_name,
                view_definition
            FROM information_schema.views
            ORDER BY table_catalog, table_schema, table_name
        """
        )

    def get_pks(self) -> pd.DataFrame:
        empty = pd.DataFrame(
            columns=["database", "schema", "table_name", "column_name", "ordinal_position"]
        )
        try:
            df = self.execute(
                """
                SELECT
                    current_database() AS "database",
                    c.schema_name      AS "schema",
                    c.table_name       AS "table_name",
                    unnest(c.constraint_column_names) AS "column_name",
                    unnest(range(1, len(c.constraint_column_names) + 1)) AS "ordinal_position"
                FROM duckdb_constraints() c
                WHERE c.constraint_type = 'PRIMARY KEY'
                ORDER BY c.schema_name, c.table_name, "ordinal_position"
            """
            )
            return df if not df.empty else empty
        except Exception:
            return empty

    def get_fks(self) -> pd.DataFrame:
        empty = pd.DataFrame(
            columns=[
                "database", "schema", "table_name", "column_name",
                "referenced_schema", "referenced_table", "referenced_column",
            ]
        )
        try:
            df = self.execute(
                """
                SELECT
                    current_database() AS "database",
                    c.schema_name      AS "schema",
                    c.table_name       AS "table_name",
                    unnest(c.constraint_column_names) AS "column_name"
                FROM duckdb_constraints() c
                WHERE c.constraint_type = 'FOREIGN KEY'
                ORDER BY c.schema_name, c.table_name
            """
            )
            return df if not df.empty else empty
        except Exception:
            return empty

    # ------------------------------------------------------------------
    # Lifecycle
    # ------------------------------------------------------------------

    def close(self) -> None:
        self.conn.close()
