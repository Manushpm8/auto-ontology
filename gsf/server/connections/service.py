# SPDX-FileCopyrightText: Copyright (c) 2024-2026, NVIDIA CORPORATION & AFFILIATES.
# All rights reserved.
# SPDX-License-Identifier: Apache-2.0

"""Connection lifecycle: test, persist credentials, and link catalog databases."""

from __future__ import annotations

import logging
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

    database_name = str(database.get("database_name") or "").strip()
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
            connection_string=connection_string,
            database_name=database_name,
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

    from gsf.ingestion_service.ingest import trigger_ingest

    trigger_ingest(connection_id, connection_string)

    return row


def delete_connection(connection_id: str) -> dict[str, str] | None:
    """Delete a UI-managed connection and tear down its ingested database graph."""
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

    from gsf.ingestion_service.ingest import trigger_ingest_delete

    trigger_ingest_delete(database_name)

    return {"id": connection_id}
