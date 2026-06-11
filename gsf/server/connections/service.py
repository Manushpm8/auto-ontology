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


def _validate_connection_string(connection_string: str) -> SQLDatabase:
    """Open a connector and verify the connection string works."""
    connection_string = connection_string.strip()
    if not connection_string:
        raise ValueError("Connection string is required")

    connector = create_connector(connection_string)
    try:
        connector.ping()
    except Exception:
        connector.close()
        raise
    return connector


def test_connection(connection_type: str, connection_string: str) -> None:
    """Validate credentials for the settings UI test action."""
    del connection_type  # scheme is resolved from the connection string
    connector = _validate_connection_string(connection_string)
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

            run_ingest(connection_string)
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
    connection_type: str,
    connection_string: str,
    database: DataSource,
) -> dict[str, Any]:
    """Create a UI-managed connection stored in Neo4j."""
    connection_string = connection_string.strip()
    if not connection_string:
        raise ValueError("Connection string is required")

    database_name = str(database.get("db_name") or "").strip()
    if not database_name:
        raise ValueError("Connection test result is required")

    existing = dal.find_connection_for_database(database_name)
    if existing is not None:
        raise ValueError(
            f"Database {database_name!r} already has connection {existing!r}"
        )

    connection_id = str(uuid.uuid4())

    try:
        row = dal.insert_connection(
            connection_id=connection_id,
            name=database_name,
            connection_string=connection_string,
            database_name=database_name,
        )
        dal.link_database_connection(
            db_name=database_name,
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
