# SPDX-FileCopyrightText: Copyright (c) 2024-2026, NVIDIA CORPORATION & AFFILIATES.
# All rights reserved.
# SPDX-License-Identifier: Apache-2.0

"""VAST Data connector implementing the NeMo-Retriever SQLDatabase ABC.

Executes SQL through the ``adbc-driver-vastdb`` ADBC driver over its DB-API 2.0
interface and emits the ``trino`` dialect. Schema introspection goes through the
``vastdb`` SDK -- it walks the bucket -> schema -> table hierarchy directly,
because the ADBC engine does not expose Trino's ``information_schema``.

Both the driver and the SDK authenticate with S3-style credentials (endpoint +
access key + secret key), not a username/password, and address a single VAST
``bucket`` (used here as the ``database_name``). The ADBC native library only
ships for Linux/x86_64 (and macOS universal2 for the Python bindings), so both
the driver and the SDK are imported lazily -- importing this module never
requires either to be present.
"""

from __future__ import annotations

import logging
import os
import tempfile
from contextlib import contextmanager
from typing import TYPE_CHECKING, Any, Iterator, Optional
from urllib.parse import parse_qs, unquote, urlparse

import pandas as pd
import sqlglot
from nemo_retriever.tabular_data.ingestion.model.reserved_words import TableTypes
from nemo_retriever.tabular_data.sql_database import SQLDatabase
from sqlglot import exp

if TYPE_CHECKING:
    from adbc_driver_manager.dbapi import Connection

logger = logging.getLogger(__name__)


def _ensure_writable_driver_home() -> None:
    """Point the native ADBC driver's log dir at a writable ``HOME``.

    On load, ``adbc-driver-vastdb`` creates a log directory under
    ``$HOME/.local/share/VastAdbcDriver`` -- it derives the path from ``HOME``
    and ignores ``XDG_DATA_HOME``. In containers ``HOME`` is often a root-owned
    WORKDIR (e.g. ``/app``) that the unprivileged runtime user cannot write, so
    the driver fails at connect with::

        IO: Failed to create log directory: "/app/.local/share/VastAdbcDriver"
        Permission denied (os error 13)

    If ``$HOME/.local/share`` is missing/unwritable, redirect ``HOME`` to a temp
    dir the driver can write. A writable ``HOME`` is left untouched.
    """
    home = os.environ.get("HOME") or os.path.expanduser("~")
    share = os.path.join(home, ".local", "share")
    try:
        os.makedirs(share, exist_ok=True)
        if os.access(share, os.W_OK):
            return
    except OSError:
        pass
    fallback = os.path.join(tempfile.gettempdir(), "vast-adbc-home")
    os.makedirs(os.path.join(fallback, ".local", "share"), exist_ok=True)
    os.environ["HOME"] = fallback
    logger.info("Redirected HOME to %s for the VAST ADBC driver log dir", fallback)


def _parse_connection_string(
    connection_string: str,
) -> tuple[dict[str, Any], dict[str, str], str]:
    """Parse a VAST URL into ADBC ``db_kwargs``, ``conn_kwargs``, and a bucket.

    Expected format::

        vast://ACCESS_KEY:SECRET_KEY@HOST[:PORT]/BUCKET?secure=1&end_user=USER

    ``secure`` selects ``https`` (``1``/``true``) or ``http`` (default) for the
    reconstructed ``vast.db.endpoint``. ``end_user`` is optional and maps to the
    driver's impersonation option. The path segment is the VAST ``bucket`` the
    SDK introspects and the connector reports as its ``database_name``.
    """
    parsed = urlparse(connection_string)
    if parsed.scheme.split("+", 1)[0].lower() != "vast":
        raise ValueError(f"Not a VAST URL: {connection_string}")

    if not parsed.hostname:
        raise ValueError(
            "VAST connection string requires an endpoint host, e.g. "
            "vast://access-key:secret-key@172.200.207.121/my_catalog?secure=1"
        )

    access_key = unquote(parsed.username or "")
    if not access_key:
        raise ValueError("VAST connection string requires an access key")

    if parsed.password is None:
        raise ValueError("VAST connection string requires a secret key")
    secret_key = unquote(parsed.password)
    if not secret_key:
        raise ValueError("VAST connection string requires a non-empty secret key")

    bucket = unquote(parsed.path.lstrip("/"))
    if not bucket:
        raise ValueError(
            "VAST connection string requires a bucket name, e.g. "
            "vast://access-key:secret-key@172.200.207.101/my_bucket"
        )

    query = parse_qs(parsed.query)
    secure = (query.get("secure", ["0"])[0] or "").strip().lower() in {
        "1",
        "true",
        "yes",
    }
    scheme = "https" if secure else "http"
    port = f":{parsed.port}" if parsed.port else ""
    endpoint = f"{scheme}://{parsed.hostname}{port}"

    db_kwargs: dict[str, Any] = {
        "vast.db.endpoint": endpoint,
        "vast.db.access_key": access_key,
        "vast.db.secret_key": secret_key,
    }

    conn_kwargs: dict[str, str] = {}
    end_user = query.get("end_user", [None])[0]
    if end_user:
        conn_kwargs["vast.db.end_user"] = unquote(end_user)

    return db_kwargs, conn_kwargs, bucket


class VastDatabase(SQLDatabase):
    """Concrete :class:`SQLDatabase` backed by VAST Data.

    SQL runs through the ADBC driver; schema introspection runs through the
    ``vastdb`` SDK against the same bucket.

    Parameters
    ----------
    connection_string:
        ``vast://ACCESS_KEY:SECRET_KEY@HOST[:PORT]/BUCKET?secure=1``
    schemas:
        Optional ingestion allowlist. Introspection is restricted to these
        schemas (case-insensitively); ``None``/empty means "all schemas".
    """

    def __init__(
        self,
        connection_string: str,
        schemas: list[str] | None = None,
    ) -> None:
        (
            self._db_kwargs,
            self._conn_kwargs,
            self._database_name,
        ) = _parse_connection_string(connection_string)
        self._schema_filter: set[str] | None = (
            {schema.casefold() for schema in schemas if schema.strip()}
            if schemas
            else None
        )

    @property
    def dialect(self) -> str:
        return "trino"

    @property
    def database_name(self) -> str:
        return self._database_name

    # ------------------------------------------------------------------
    # Connection
    # ------------------------------------------------------------------

    @contextmanager
    def _connect(self) -> Iterator["Connection"]:
        # The driver writes a log directory under $HOME on load; make sure that
        # target is writable before it initialises.
        _ensure_writable_driver_home()

        # Imported lazily: the native driver library only ships for Linux, so
        # importing this module on other platforms must not require it.
        import adbc_driver_manager.dbapi
        import adbc_driver_vastdb

        connection = adbc_driver_manager.dbapi.connect(
            driver=adbc_driver_vastdb.get_driver_path(),
            db_kwargs=self._db_kwargs,
            conn_kwargs=self._conn_kwargs or None,
            autocommit=True,
        )
        try:
            yield connection
        finally:
            connection.close()

    @contextmanager
    def _open_bucket(self) -> Iterator[Any]:
        """Yield the VAST bucket via the ``vastdb`` SDK for introspection.

        The SDK walks the bucket -> schema -> table hierarchy directly; the ADBC
        engine does not expose Trino's ``information_schema``, so introspection
        goes through the SDK instead of SQL. The transaction stays open for the
        lifetime of the ``with`` block, so callers must materialise anything they
        need before it exits.
        """
        # Imported lazily to mirror the ADBC driver: neither native dependency is
        # required merely to import this module (e.g. on unsupported platforms).
        import vastdb

        session = vastdb.connect(
            endpoint=self._db_kwargs["vast.db.endpoint"],
            access=self._db_kwargs["vast.db.access_key"],
            secret=self._db_kwargs["vast.db.secret_key"],
        )
        with session.transaction() as tx:
            yield tx.bucket(self._database_name)

    def _filter_by_schema(self, frame: pd.DataFrame) -> pd.DataFrame:
        if (
            self._schema_filter is None
            or frame.empty
            or "table_schema" not in frame.columns
        ):
            return frame
        return frame[
            frame["table_schema"].astype(str).str.casefold().isin(self._schema_filter)
        ]

    # ------------------------------------------------------------------
    # Execution
    # ------------------------------------------------------------------

    # Schemas that are not VAST buckets and must never be bucket-qualified.
    _SYSTEM_SCHEMAS = frozenset(
        {"information_schema", "system", "main", "memory", "pg_catalog"}
    )

    def _qualify_query(self, sql: str) -> str:
        """Rewrite ``schema.table`` refs to VAST's ``"bucket/schema".table`` form.

        VAST's QueryEngine addresses a table through a single bucket-qualified
        schema identifier (``"gsf-db-bucket/tpcds_sf1"."call_center"``). A bare
        ``schema.table`` resolves against the default catalog (``vastdb``) and
        fails with ``Table ... does not exist``; a three-part
        ``"gsf-db-bucket".schema.table`` fails with ``Catalog ... does not
        exist``. Callers (ingestion sampling, text-to-SQL) emit either form, so
        both are folded into ``"bucket/schema".table`` here rather than leaking
        the bucket into the schema names surfaced during introspection.
        Unparsable SQL is passed through untouched.
        """
        try:
            expressions = sqlglot.parse(sql, read=self.dialect)
        except Exception:
            return sql
        bucket = self._database_name
        changed = False
        for expression in expressions:
            if expression is None:
                continue
            for table in expression.find_all(exp.Table):
                db = table.args.get("db")
                if db is None:
                    continue
                schema_name = db.name
                if schema_name in self._SYSTEM_SCHEMAS or schema_name.startswith(
                    f"{bucket}/"
                ):
                    continue
                catalog = table.args.get("catalog")
                if catalog is not None:
                    # Only fold when the LLM used the bucket as the catalog
                    # (``"bucket".schema.table``); leave foreign catalogs alone.
                    if catalog.name != bucket:
                        continue
                    table.set("catalog", None)
                table.set(
                    "db",
                    exp.to_identifier(f"{bucket}/{schema_name}", quoted=True),
                )
                changed = True
        if not changed:
            return sql
        return ";\n".join(e.sql(dialect=self.dialect) for e in expressions if e)

    def execute(self, sql: str, parameters: Optional[list] = None) -> pd.DataFrame:
        sql = self._qualify_query(sql)
        with self._connect() as connection:
            with connection.cursor() as cursor:
                cursor.execute(sql, parameters)
                if cursor.description is None:
                    return pd.DataFrame()
                columns = [description[0].lower() for description in cursor.description]
                return pd.DataFrame(cursor.fetchall(), columns=columns)

    # ------------------------------------------------------------------
    # Schema introspection
    # ------------------------------------------------------------------

    def get_schemas(self) -> list[str]:
        with self._open_bucket() as bucket:
            names = [schema.name for schema in bucket.schemas()]
        if self._schema_filter is not None:
            names = [name for name in names if name.casefold() in self._schema_filter]
        return sorted(names)

    def get_tables(self) -> pd.DataFrame:
        base_table = TableTypes.BASE_TABLE
        rows: list[dict[str, Any]] = []
        with self._open_bucket() as bucket:
            for schema in bucket.schemas():
                # ``tablenames()`` lists names without materialising ``Table``
                # objects. Building a ``Table`` validates its arrow schema through
                # ibis, which rejects VAST fixed-size-binary (``CHAR(n)``) columns
                # and would fail the whole schema's listing; names avoid that.
                for table_name in schema.tablenames():
                    rows.append(
                        {
                            "table_schema": schema.name,
                            "table_name": table_name,
                            "table_type": base_table,
                        }
                    )
        frame = pd.DataFrame(rows, columns=["table_schema", "table_name", "table_type"])
        return self._filter_by_schema(frame)

    def get_columns(self) -> pd.DataFrame:
        rows: list[dict[str, Any]] = []
        with self._open_bucket() as bucket:
            # Read columns via the low-level ``list_columns`` RPC rather than the
            # ``Table``/``columns()`` API: materialising a ``Table`` validates its
            # arrow schema through ibis, which raises on VAST fixed-size-binary
            # (``CHAR(n)``) columns. The RPC returns the pyarrow fields directly.
            api = bucket.tx._rpc.api
            txid = bucket.tx.txid
            for schema in bucket.schemas():
                for table_name in schema.tablenames():
                    position = 0
                    next_key = 0
                    while True:
                        fields, next_key, is_truncated, _ = api.list_columns(
                            bucket=bucket.name,
                            schema=schema.name,
                            table=table_name,
                            txid=txid,
                            next_key=next_key,
                        )
                        for field in fields:
                            position += 1
                            rows.append(
                                {
                                    "table_schema": schema.name,
                                    "table_name": table_name,
                                    "column_name": field.name,
                                    "data_type": str(field.type),
                                    "is_nullable": "YES" if field.nullable else "NO",
                                    "ordinal_position": position,
                                }
                            )
                        if not is_truncated:
                            break
        frame = pd.DataFrame(
            rows,
            columns=[
                "table_schema",
                "table_name",
                "column_name",
                "data_type",
                "is_nullable",
                "ordinal_position",
            ],
        )
        return self._filter_by_schema(frame)

    def get_views(self) -> pd.DataFrame:
        # VAST DB has no views, so introspection returns an empty frame.
        return pd.DataFrame(columns=["table_schema", "table_name", "view_definition"])

    def get_pks(self) -> pd.DataFrame:
        # Trino's information_schema exposes no key/constraint metadata, so the
        # QueryEngine reports no primary keys.
        return pd.DataFrame(
            columns=["table_schema", "table_name", "column_name", "ordinal_position"]
        )

    def get_fks(self) -> pd.DataFrame:
        # Trino's information_schema exposes no key/constraint metadata, so the
        # QueryEngine reports no foreign keys.
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

    def get_queries(self, hours: int = 24) -> pd.DataFrame:
        """Return an empty frame: VAST exposes no usable query history.

        The ADBC engine is DuckDB-flavoured and has no Trino
        ``system.runtime.queries`` (or equivalent) table, so there is no query
        history to fetch. Returning empty avoids emitting a failing query on
        every ingestion run.
        """
        return pd.DataFrame(columns=["end_time", "query_text"])

    # ------------------------------------------------------------------
    # Lifecycle
    # ------------------------------------------------------------------

    def ping(self) -> None:
        self.execute("SELECT 1")

    def close(self) -> None:
        """No persistent connection to close (connections are per-operation)."""
