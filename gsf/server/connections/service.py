# SPDX-FileCopyrightText: Copyright (c) 2024-2026, NVIDIA CORPORATION & AFFILIATES.
# All rights reserved.
# SPDX-License-Identifier: Apache-2.0

"""Connection lifecycle: test, persist credentials, and link catalog databases."""

from __future__ import annotations

import logging
import threading
import uuid
from typing import Any

from nemo_retriever.tabular_data.sql_database import SQLDatabase

from gsf.connectors.registry import create_connector, invalidate_connectors_cache
from gsf.server.connections import dal

logger = logging.getLogger(__name__)

DataSource = dict[str, Any]


def _discover_datasources(connector: SQLDatabase) -> list[DataSource]:
    """Return databases/schemas available for the connection (illumex ``test()``)."""
    dialect = connector.dialect
    if dialect == "snowflake":
        df = connector.execute("SHOW SCHEMAS")
        if df.empty:
            return []
        grouped = (
            df[["database_name", "name"]]
            .groupby("database_name")
            .agg(list)
            .reset_index()
            .rename({"database_name": "db_name", "name": "schemas"}, axis=1)
        )
        return grouped.to_dict(orient="records")

    if dialect == "postgres":
        df = connector.execute(
            """
            SELECT schema_name
            FROM information_schema.schemata
            WHERE schema_name NOT IN ('pg_catalog', 'information_schema', 'pg_toast')
            ORDER BY schema_name
            """
        )
        schemas = [str(s) for s in df["schema_name"].tolist()] if not df.empty else []
        return [{"db_name": connector.database_name, "schemas": schemas}]

    return [{"db_name": connector.database_name, "schemas": []}]


def _resolve_single_database(
    pull_info: list[DataSource],
    connector_database_name: str,
    *,
    auto_discovered: bool = False,
) -> list[DataSource]:
    """Ensure a connection is linked to exactly one catalog database."""
    if not pull_info:
        raise ValueError("No database found for this connection")

    by_name: dict[str, DataSource] = {}
    for item in pull_info:
        by_name.setdefault(item["db_name"], item)

    if len(by_name) == 1:
        return [next(iter(by_name.values()))]

    if auto_discovered and connector_database_name in by_name:
        return [by_name[connector_database_name]]

    names = ", ".join(sorted(by_name))
    raise ValueError(
        f"A connection can be linked to only one database. Choose one of: {names}"
    )


def test_connection(connection_type: str, connection_string: str) -> list[DataSource]:
    """Validate credentials and return discoverable databases/schemas."""
    del connection_type  # scheme is resolved from the connection string
    connection_string = connection_string.strip()
    if not connection_string:
        raise ValueError("Connection string is required")

    connector = create_connector(connection_string)
    try:
        return _discover_datasources(connector)
    finally:
        connector.close()


def _trigger_ingest_delete(database_name: str) -> None:
    """Remove ingested catalog data without blocking the API response."""

    def _run() -> None:
        try:
            from gsf.ingestion_service.ingest import run_ingest_delete

            run_ingest_delete(database_name)
        except Exception:
            logger.exception(
                "Background ingest delete failed for database %s",
                database_name,
            )

    threading.Thread(
        target=_run,
        daemon=True,
        name=f"ingest-delete-{database_name}",
    ).start()


def _trigger_ingest(connection_id: str, connection_string: str) -> None:
    """Run ingest for a new connection without blocking the API response."""

    def _run() -> None:
        try:
            from gsf.ingestion_service.ingest import run_ingest

            run_ingest(connection_string, connection_id=connection_id)
        except Exception:
            logger.exception(
                "Background ingest failed for connection %s",
                connection_id,
            )

    threading.Thread(
        target=_run,
        daemon=True,
        name=f"ingest-{connection_id}",
    ).start()


def create_connection(
    *,
    name: str,
    connection_type: str,
    connection_string: str,
) -> dict[str, Any]:
    """Create a UI-managed connection stored in Neo4j."""
    name = name.strip()
    if not name:
        raise ValueError("Connection name is required")

    connection_string = connection_string.strip()
    if not connection_string:
        raise ValueError("Connection string is required")

    pull_info = test_connection(connection_type, connection_string)
    auto_discovered = True

    connector = create_connector(connection_string)
    try:
        database_name = connector.database_name
    finally:
        connector.close()

    pull_info = _resolve_single_database(
        pull_info,
        database_name,
        auto_discovered=auto_discovered,
    )
    target_db_name = pull_info[0]["db_name"]

    existing = dal.find_connection_for_database(target_db_name)
    if existing is not None:
        raise ValueError(
            f"Database {target_db_name!r} already has connection {existing!r}"
        )

    connection_id = str(uuid.uuid4())
    selected_db_names = [target_db_name]

    try:
        row = dal.insert_connection(
            connection_id=connection_id,
            name=name,
            connection_type=connection_type,
            connection_string=connection_string,
            database_name=database_name,
            selected_databases=selected_db_names,
            pull_info=pull_info,
        )
        dal.link_database_connection(
            db_name=target_db_name,
            connection_string=connection_string,
        )
    except Exception:
        logger.exception(
            "Failed to link connection %s in Neo4j; rolling back connection node",
            connection_id,
        )
        dal.delete_connection(connection_id)
        raise

    invalidate_connectors_cache()
    try:
        from gsf.server.chat.worker import refresh_chat_workers

        refresh_chat_workers()
    except Exception:
        logger.exception("Failed to refresh chat workers after connection create")

    _trigger_ingest(connection_id, connection_string)

    return row


def delete_connection(connection_id: str) -> dict[str, str] | None:
    """Delete a UI-managed connection and tear down its ingested catalog."""
    props = dal.get_connection_by_id(connection_id)
    if props is None:
        return None

    database_name = dal.catalog_database_name(props)
    if not database_name:
        raise ValueError("Connection is missing a linked database name")

    dal.delete_connection(connection_id)

    invalidate_connectors_cache()
    try:
        from gsf.server.chat.worker import refresh_chat_workers

        refresh_chat_workers()
    except Exception:
        logger.exception("Failed to refresh chat workers after connection delete")

    _trigger_ingest_delete(database_name)

    return {"id": connection_id}
