# SPDX-FileCopyrightText: Copyright (c) 2024-2026, NVIDIA CORPORATION & AFFILIATES.
# All rights reserved.
# SPDX-License-Identifier: Apache-2.0

"""Snowflake connector implementing the NeMo-Retriever SQLDatabase ABC."""

from __future__ import annotations

import json
import logging
import os
import re
from pathlib import Path
from typing import Any, Optional
from urllib.parse import unquote, urlparse

import pandas as pd
import snowflake.connector
from nemo_retriever.tabular_data.sql_database import SQLDatabase

from gsf.connectors.url_utils import metadata_database_from_query, parse_query, query_param

logger = logging.getLogger(__name__)

# Table / column allowlist loaded from enrichment metadata.json when present.
MetadataAllowlist = tuple[set[str], dict[str, set[str]]]


def _quoted_identifier(name: str) -> str:
    """Return a Snowflake-quoted identifier (preserves case and special chars)."""
    return '"' + name.replace('"', '""') + '"'


def _slugify(name: str) -> str:
    """Normalize a database name into a filesystem-safe slug."""
    slug = re.sub(r"[^a-z0-9]+", "_", name.lower())
    return slug.strip("_")


def _sql_string_list(values: set[str]) -> str:
    """Render uppercase identifiers as a Snowflake ``IN (...)`` list literal."""
    return ", ".join("'" + value.replace("'", "''") + "'" for value in sorted(values))


def _datasets_root() -> Path:
    """Return the datasets root used to resolve enrichment metadata files."""
    override = os.environ.get("DATASETS_DIR", "").strip()
    if override:
        return Path(override).expanduser()
    return Path.cwd() / "datasets"


def resolve_metadata_path(
    *,
    database_name: str,
    physical_database: str,
    metadata_file: str | None = None,
    datasets_root: Path | None = None,
) -> Path | None:
    """Locate enrichment ``metadata.json`` for a Snowflake database, if any.

    Resolution order:

    1. Explicit ``?metadata_file=`` path from the connection string.
    2. ``<datasets>/<database_name>/metadata.json`` (``database_name`` may be a
       logical name such as ``spider2/adventureworks``).
    3. ``<datasets>/spider2/<slug(physical_database)>/metadata.json`` so Spider2
       Snowflake URLs without ``metadata_database`` still pick up seeded files.
    """
    if metadata_file:
        path = Path(metadata_file).expanduser()
        return path if path.is_file() else None

    root = datasets_root if datasets_root is not None else _datasets_root()
    candidates = [
        root / database_name / "metadata.json",
        root / "spider2" / _slugify(physical_database) / "metadata.json",
    ]
    if database_name != physical_database:
        candidates.append(root / "spider2" / _slugify(database_name) / "metadata.json")

    for path in candidates:
        if path.is_file():
            return path
    return None


def load_metadata_allowlist(metadata_path: Path) -> MetadataAllowlist:
    """Parse enrichment metadata into uppercase table / column allowlists."""
    raw = json.loads(metadata_path.read_text(encoding="utf-8"))
    if not isinstance(raw, dict):
        raise ValueError(f"Expected object in metadata file: {metadata_path}")

    tables: set[str] = set()
    columns_by_table: dict[str, set[str]] = {}
    for table_name, table_meta in raw.items():
        table_key = str(table_name).upper()
        tables.add(table_key)
        column_names: set[str] = set()
        if isinstance(table_meta, dict):
            for column in table_meta.get("columns") or []:
                if not isinstance(column, dict):
                    continue
                column_name = column.get("name")
                if column_name is None or str(column_name).strip() == "":
                    continue
                column_names.add(str(column_name).upper())
        columns_by_table[table_key] = column_names
    return tables, columns_by_table


def _parse_connection_string(
    connection_string: str,
) -> tuple[dict[str, Any], str, str, str, str | None]:
    """Parse a Snowflake URL into connector kwargs, warehouse, and names.

    Returns
    ``(connect_kwargs, warehouse, physical_database, database_name, metadata_file)``.

    Multi-database loading matches SQLite: put one URL per Snowflake database in
    ``CONNECTION_STRINGS`` (different ``?database=``). ``database_name`` defaults
    to that physical database (same role as the SQLite file stem). Optional
    ``?metadata_database=`` overrides the routing name when needed. Optional
    ``?metadata_file=`` points at an enrichment metadata JSON used to restrict
    introspection to the listed tables and columns.

    Expected format::

        snowflake://USER:PASSWORD@ACCOUNT?warehouse=WH&database=SF_DB
    """
    parsed = urlparse(connection_string)
    if parsed.scheme.split("+", 1)[0].lower() != "snowflake":
        raise ValueError(f"Not a Snowflake URL: {connection_string}")
    if not parsed.hostname:
        raise ValueError(
            f"Invalid Snowflake connection string (missing account): {connection_string}"
        )

    user = unquote(parsed.username or "")
    if not user:
        raise ValueError(
            "Snowflake connection string requires user in the URL, e.g. "
            "snowflake://user:pass@account?warehouse=COMPUTE_WH&database=MY_DB"
        )

    if parsed.password is None:
        raise ValueError(
            "Snowflake connection string requires password in the URL, e.g. "
            "snowflake://user:pass@account?warehouse=COMPUTE_WH&database=MY_DB"
        )
    password = unquote(parsed.password)
    if not password:
        raise ValueError("Snowflake connection string requires a non-empty password")

    query = parse_query(connection_string)
    warehouse = query_param(query, "warehouse")
    if not warehouse:
        raise ValueError("Snowflake connection string requires ?warehouse=COMPUTE_WH")

    database = query_param(query, "database")
    if not database:
        database = unquote(parsed.path.lstrip("/")) or None
    if not database:
        raise ValueError(
            "Snowflake connection string requires ?database=SNOWFLAKE_DB, e.g. "
            "snowflake://user:pass@account?warehouse=COMPUTE_WH&database=MY_DB"
        )

    connect_kwargs: dict[str, Any] = {
        "user": user,
        "password": password,
        "account": parsed.hostname,
        "database": database,
        "warehouse": warehouse,
        "login_timeout": 10,
    }

    role = query_param(query, "role")
    if role:
        connect_kwargs["role"] = role

    schema = query_param(query, "schema")
    if schema:
        connect_kwargs["schema"] = schema

    logical_name = metadata_database_from_query(query) or database
    metadata_file = query_param(query, "metadata_file")
    return connect_kwargs, warehouse, database, logical_name, metadata_file


class SnowflakeDatabase(SQLDatabase):
    """Concrete :class:`SQLDatabase` backed by ``snowflake-connector-python``.

    Multi-database loading matches SQLite: one connection string per Snowflake
    database in ``CONNECTION_STRINGS``. Each connector's ``database_name``
    defaults to ``?database=`` (analogous to the SQLite file stem) so eval can
    route by ``db_id``.

    When an enrichment ``metadata.json`` is found for the database (see
    :func:`resolve_metadata_path`), ``get_tables`` / ``get_columns`` and related
    introspection methods are restricted to the tables and columns listed there.

    Parameters
    ----------
    connection_string:
        ``snowflake://user:password@account?warehouse=COMPUTE_WH&database=MY_DB``
    """

    def __init__(
        self,
        connection_string: str,
        schemas: list[str] | None = None,
    ) -> None:
        (
            self._connect_kwargs,
            self._warehouse,
            self._physical_database,
            self._database_name,
            metadata_file,
        ) = _parse_connection_string(connection_string)
        # Optional ingestion allowlist: an explicit structured-connection
        # selection takes precedence; otherwise a URL ``schema=`` parameter
        # scopes env-var connections such as CONNECTION_STRINGS. Without this
        # fallback, ``schema=`` only sets Snowflake's current schema while
        # introspection still returns every visible schema in the database.
        url_schema = self._connect_kwargs.get("schema")
        filter_schemas = schemas or ([str(url_schema)] if url_schema else None)

        # Empty/None means "all schemas".
        # Compared case-insensitively (Snowflake upper-cases unquoted names).
        self._schema_filter: set[str] | None = (
            {s.upper() for s in filter_schemas if s and s.strip()}
            if filter_schemas
            else None
        )
        if self._schema_filter:
            logger.info(
                "Snowflake ingestion restricted to schemas: %s",
                sorted(self._schema_filter),
            )

        self._metadata_tables: set[str] | None = None
        self._metadata_columns: dict[str, set[str]] | None = None
        self._metadata_path = resolve_metadata_path(
            database_name=self._database_name,
            physical_database=self._physical_database,
            metadata_file=metadata_file,
        )
        if self._metadata_path is not None:
            tables, columns = load_metadata_allowlist(self._metadata_path)
            self._metadata_tables = tables
            self._metadata_columns = columns
            logger.info(
                "Snowflake ingestion restricted to %d table(s) / %d column(s) "
                "from metadata %s",
                len(tables),
                sum(len(cols) for cols in columns.values()),
                self._metadata_path,
            )

    def _filter_by_schema(self, df: pd.DataFrame) -> pd.DataFrame:
        """Restrict a schema-introspection frame to the configured allowlist.

        No-op when no filter is set or the frame lacks a ``table_schema`` column.
        """
        if self._schema_filter is None or df.empty or "table_schema" not in df.columns:
            return df
        return df[df["table_schema"].str.upper().isin(self._schema_filter)]

    def _filter_by_metadata(
        self,
        df: pd.DataFrame,
        *,
        filter_columns: bool = False,
    ) -> pd.DataFrame:
        """Restrict introspection rows to enrichment metadata tables/columns."""
        if self._metadata_tables is None or df.empty or "table_name" not in df.columns:
            return df

        filtered = df[df["table_name"].astype(str).str.upper().isin(self._metadata_tables)]
        if (
            not filter_columns
            or self._metadata_columns is None
            or "column_name" not in filtered.columns
            or filtered.empty
        ):
            return filtered

        def _column_allowed(row: pd.Series) -> bool:
            table_key = str(row["table_name"]).upper()
            allowed = self._metadata_columns.get(table_key)
            if not allowed:
                # Table is allowlisted but lists no columns → keep none.
                return False
            return str(row["column_name"]).upper() in allowed

        return filtered[filtered.apply(_column_allowed, axis=1)]

    @property
    def dialect(self) -> str:
        return "snowflake"

    @property
    def database_name(self) -> str:
        return self._database_name

    # ------------------------------------------------------------------
    # Execution
    # ------------------------------------------------------------------

    def execute(self, sql: str, parameters: Optional[list] = None) -> pd.DataFrame:
        with snowflake.connector.connect(**self._connect_kwargs) as conn:
            with conn.cursor() as cur:
                # Pin warehouse + database per query so multi-DB CONNECTION_STRINGS
                # entries cannot leak session state across connectors.
                cur.execute(f"USE WAREHOUSE {_quoted_identifier(self._warehouse)}")
                cur.execute(
                    f"USE DATABASE {_quoted_identifier(self._physical_database)}"
                )
                if parameters:
                    cur.execute(sql, parameters)
                else:
                    cur.execute(sql)
                if cur.description is None:
                    return pd.DataFrame()
                # Return the real column names as Snowflake reports them (unquoted
                # identifiers come back UPPERCASE). The introspection queries below
                # quote their aliases to keep their lowercase result keys stable.
                columns = [desc[0] for desc in cur.description]
                return pd.DataFrame(cur.fetchall(), columns=columns)

    # ------------------------------------------------------------------
    # Schema introspection
    # ------------------------------------------------------------------

    def get_schemas(self) -> list[str]:
        """List the database's schemas (excluding ``INFORMATION_SCHEMA``).

        Used by the connection UI to let the user pick which schemas to ingest.
        """
        df = self.execute("""
            SELECT SCHEMA_NAME AS "schema_name"
            FROM INFORMATION_SCHEMA.SCHEMATA
            WHERE SCHEMA_NAME != 'INFORMATION_SCHEMA'
            ORDER BY SCHEMA_NAME
        """)
        if df.empty:
            return []
        return [str(name) for name in df["schema_name"].tolist()]

    def get_tables(self) -> pd.DataFrame:
        sql = """
            SELECT
                TABLE_SCHEMA AS "table_schema",
                TABLE_NAME   AS "table_name",
                TABLE_TYPE   AS "table_type"
            FROM INFORMATION_SCHEMA.TABLES
            WHERE TABLE_SCHEMA != 'INFORMATION_SCHEMA'
        """
        if self._metadata_tables:
            sql += (
                f" AND UPPER(TABLE_NAME) IN ({_sql_string_list(self._metadata_tables)})"
            )
        sql += " ORDER BY TABLE_SCHEMA, TABLE_NAME"
        return self._filter_by_metadata(self._filter_by_schema(self.execute(sql)))

    def get_columns(self) -> pd.DataFrame:
        sql = """
            SELECT
                TABLE_SCHEMA     AS "table_schema",
                TABLE_NAME       AS "table_name",
                COLUMN_NAME      AS "column_name",
                DATA_TYPE        AS "data_type",
                IS_NULLABLE      AS "is_nullable",
                ORDINAL_POSITION AS "ordinal_position"
            FROM INFORMATION_SCHEMA.COLUMNS
            WHERE TABLE_SCHEMA != 'INFORMATION_SCHEMA'
        """
        if self._metadata_tables:
            sql += (
                f" AND UPPER(TABLE_NAME) IN ({_sql_string_list(self._metadata_tables)})"
            )
        sql += " ORDER BY TABLE_SCHEMA, TABLE_NAME, ORDINAL_POSITION"
        df = self.execute(sql)
        if df.empty:
            return df

        # https://stackoverflow.com/questions/64667418/snowflake-sys-facade-in-information-schema-columns
        df["column_name"] = df["column_name"].str.removesuffix("$SYS_FACADE$0")
        df["column_name"] = df["column_name"].str.removesuffix("$SYS_FACADE$1")
        df = df.drop_duplicates(subset=["table_schema", "table_name", "column_name"])
        return self._filter_by_metadata(
            self._filter_by_schema(df),
            filter_columns=True,
        )

    def get_queries(self, hours: int = 24) -> pd.DataFrame:
        """Return recent queries from ``INFORMATION_SCHEMA.QUERY_HISTORY``."""
        try:
            df = self.execute(f"""
                SELECT
                    END_TIME   AS "end_time",
                    QUERY_TEXT AS "query_text"
                FROM TABLE(
                    INFORMATION_SCHEMA.QUERY_HISTORY(
                        DATEADD(hour, -{hours}, CURRENT_TIMESTAMP()),
                        CURRENT_TIMESTAMP(),
                        RESULT_LIMIT => 10000
                    )
                )
                WHERE QUERY_TYPE NOT IN (
                    'USE', 'SHOW', 'GRANT', 'CREATE_USER', 'CREATE_ROLE',
                    'DROP', 'COMMIT', 'ALTER_SESSION', 'CALL'
                )
                  AND EXECUTION_STATUS = 'SUCCESS'
                  AND NULLIF(TRIM(QUERY_TEXT), '') IS NOT NULL
                  AND LOWER(QUERY_TEXT) NOT LIKE '%information_schema%'
                ORDER BY END_TIME DESC
            """)
            return df[["end_time", "query_text"]]
        except snowflake.connector.errors.Error:
            logger.exception("Failed to fetch Snowflake query history")
            return pd.DataFrame(columns=["end_time", "query_text"])

    def get_views(self) -> pd.DataFrame:
        db = _quoted_identifier(self._physical_database)
        df = self.execute(f"SHOW VIEWS IN DATABASE {db}")
        if df.empty:
            return pd.DataFrame(
                columns=["table_schema", "table_name", "view_definition"]
            )

        # SHOW VIEWS returns fixed metadata columns; normalize their case here
        # since execute() no longer lower-cases result headers.
        df.columns = [str(c).lower() for c in df.columns]
        df = df.loc[df["schema_name"] != "INFORMATION_SCHEMA"]
        df = df.rename(
            columns={
                "schema_name": "table_schema",
                "name": "table_name",
                "text": "view_definition",
            }
        )[["table_schema", "table_name", "view_definition"]]
        return self._filter_by_metadata(self._filter_by_schema(df))

    def get_pks(self) -> pd.DataFrame:
        db = _quoted_identifier(self._physical_database)
        df = self.execute(f"SHOW PRIMARY KEYS IN DATABASE {db}")
        if df.empty:
            return pd.DataFrame(
                columns=[
                    "table_schema",
                    "table_name",
                    "column_name",
                    "ordinal_position",
                ]
            )

        df.columns = [str(c).lower() for c in df.columns]
        df = df.rename(
            columns={
                "schema_name": "table_schema",
                "key_sequence": "ordinal_position",
            }
        )[["table_schema", "table_name", "column_name", "ordinal_position"]]
        return self._filter_by_metadata(
            self._filter_by_schema(df),
            filter_columns=True,
        )

    def get_fks(self) -> pd.DataFrame:
        db = _quoted_identifier(self._physical_database)
        df = self.execute(f"SHOW IMPORTED KEYS IN DATABASE {db}")
        if df.empty:
            return pd.DataFrame(
                columns=[
                    "table_schema",
                    "table_name",
                    "column_name",
                    "referenced_schema",
                    "referenced_table",
                    "referenced_column",
                ]
            )

        df.columns = [str(c).lower() for c in df.columns]
        df = df.rename(
            columns={
                "fk_schema_name": "table_schema",
                "fk_table_name": "table_name",
                "fk_column_name": "column_name",
                "pk_schema_name": "referenced_schema",
                "pk_table_name": "referenced_table",
                "pk_column_name": "referenced_column",
            }
        )[
            [
                "table_schema",
                "table_name",
                "column_name",
                "referenced_schema",
                "referenced_table",
                "referenced_column",
            ]
        ]
        filtered = self._filter_by_metadata(
            self._filter_by_schema(df),
            filter_columns=True,
        )
        if self._metadata_tables is None or filtered.empty:
            return filtered
        # Drop FKs that point outside the metadata allowlist.
        return filtered[
            filtered["referenced_table"]
            .astype(str)
            .str.upper()
            .isin(self._metadata_tables)
        ]

    # ------------------------------------------------------------------
    # Lifecycle
    # ------------------------------------------------------------------

    def ping(self) -> None:
        """Verify credentials, the warehouse, and that schemas are visible."""
        with snowflake.connector.connect(**self._connect_kwargs) as conn:
            conn.execute_string(
                f"USE WAREHOUSE {_quoted_identifier(self._warehouse)}; SHOW SCHEMAS"
            )

    def close(self) -> None:
        """No persistent connection to close (connections are per-query)."""
