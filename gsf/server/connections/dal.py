# SPDX-FileCopyrightText: Copyright (c) 2024-2026, NVIDIA CORPORATION & AFFILIATES.
# All rights reserved.
# SPDX-License-Identifier: Apache-2.0

"""Data access for UI-managed database connections (Neo4j only).

Connection metadata is stored directly on ``Labels.DB`` nodes so that the UI
connection and the catalog database share a single node.  The connection-specific
fields are prefixed with ``connection_`` to avoid collisions with the built-in
``name`` / ``id`` fields of the DB node.
"""

from __future__ import annotations

import logging
import os
from datetime import datetime, timezone
from typing import Any

from nemo_retriever.tabular_data.ingestion.model.reserved_words import Edges, Labels
from nemo_retriever.tabular_data.neo4j import get_neo4j_conn

logger = logging.getLogger(__name__)


def _utc_now() -> datetime:
    return datetime.now(timezone.utc)


def _connection_node_to_public(props: dict[str, Any]) -> dict[str, Any]:
    return {
        "id": str(props["connection_id"]),
        "type": props["connection_type"],
        "create_date": props.get("connection_create_date"),
        "last_pulled": props.get("connection_last_pulled"),
        "database_name": str(props.get("name") or "").strip(),
    }


def list_connections() -> list[dict[str, Any]]:
    """Return all connections for the settings UI (no credentials)."""
    rows = get_neo4j_conn().query_read(
        f"""
        MATCH (db:{Labels.DB})
        WHERE db.connection_id IS NOT NULL
        RETURN properties(db) AS props
        ORDER BY db.connection_name
        """
    )
    return [_connection_node_to_public(dict(row["props"])) for row in rows]


def list_connections_for_ingest() -> list[tuple[str | None, str]]:
    """Return ``(connection_id, connection_string)`` pairs for ingestion."""
    pairs: list[tuple[str | None, str]] = []
    seen_databases: set[str] = set()
    try:
        rows = get_neo4j_conn().query_read(
            f"""
            MATCH (db:{Labels.DB})
            WHERE db.connection_string IS NOT NULL AND db.connection_id IS NOT NULL
            RETURN db.connection_id AS id,
                   db.connection_string AS connection_string,
                   db.name AS database_name
            ORDER BY db.connection_name
            """
        )
        for row in rows:
            database_name = str(row.get("database_name") or "")
            if database_name and database_name in seen_databases:
                continue
            if database_name:
                seen_databases.add(database_name)
            pairs.append((str(row["id"]), str(row["connection_string"])))
    except Exception:
        logger.exception("Failed to load connection strings from Neo4j")

    if pairs:
        return pairs

    raw = os.environ.get("CONNECTION_STRINGS", "")
    return [(None, cs.strip()) for cs in raw.split(",") if cs.strip()]


def get_all_connection_strings() -> list[str]:
    """Return connection strings from Neo4j DB nodes plus env fallback."""
    strings: list[str] = []
    seen: set[str] = set()
    try:
        rows = get_neo4j_conn().query_read(
            f"""
            MATCH (db:{Labels.DB})
            WHERE db.connection_string IS NOT NULL
            RETURN DISTINCT db.connection_string AS connection_string
            """
        )
        for row in rows:
            connection_string = str(row["connection_string"])
            if connection_string not in seen:
                seen.add(connection_string)
                strings.append(connection_string)
    except Exception:
        logger.exception("Failed to load connection strings from Neo4j DB nodes")

    if strings:
        return strings

    raw = os.environ.get("CONNECTION_STRINGS", "")
    return [cs.strip() for cs in raw.split(",") if cs.strip()]


def update_last_pulled_at(connection_id: str) -> None:
    """Record a successful ingest pass for a UI-managed connection."""
    get_neo4j_conn().query_write(
        f"""
        MATCH (db:{Labels.DB} {{connection_id: $connection_id}})
        SET db.connection_last_pulled = $last_pulled
        """,
        {
            "connection_id": connection_id,
            "last_pulled": _utc_now().isoformat(),
        },
    )


def find_connection_for_database(database_name: str) -> str | None:
    """Return the display name of an existing UI connection for *database_name*, if any."""
    rows = get_neo4j_conn().query_read(
        f"""
        MATCH (db:{Labels.DB} {{name: $database_name}})
        WHERE db.connection_id IS NOT NULL
        RETURN db.connection_name AS name
        LIMIT 1
        """,
        {"database_name": database_name},
    )
    if not rows:
        return None
    return str(rows[0]["name"])


def insert_connection(
    *,
    connection_id: str,
    name: str,
    connection_type: str,
    connection_string: str,
    database_name: str,
) -> dict[str, Any]:
    """Attach connection metadata to the catalog DB node and return the public payload."""
    now = _utc_now().isoformat()
    rows = get_neo4j_conn().query_write(
        f"""
        MERGE (db:{Labels.DB} {{name: $database_name}})
        ON CREATE SET db.id = randomUUID()
        SET db.connection_id = $connection_id,
            db.connection_name = $name,
            db.connection_type = $type,
            db.connection_string = $connection_string,
            db.connection_create_date = $create_date
        RETURN properties(db) AS props
        """,
        {
            "database_name": database_name,
            "connection_id": connection_id,
            "name": name,
            "type": connection_type,
            "connection_string": connection_string,
            "create_date": now,
        },
    )
    assert rows
    return _connection_node_to_public(dict(rows[0]["props"]))


def link_database_connection(*, db_name: str, connection_string: str) -> None:
    """Attach a connection string to a catalog database node (idempotent)."""
    get_neo4j_conn().query_write(
        f"""
        MERGE (db:{Labels.DB} {{name: $db_name}})
        ON CREATE SET db.id = randomUUID()
        SET db.connection_string = $connection_string
        """,
        {"db_name": db_name, "connection_string": connection_string},
    )


def get_connection_by_id(connection_id: str) -> dict[str, Any] | None:
    """Return raw DB node properties for a connection, or ``None`` when missing."""
    rows = get_neo4j_conn().query_read(
        f"""
        MATCH (db:{Labels.DB} {{connection_id: $connection_id}})
        RETURN properties(db) AS props
        LIMIT 1
        """,
        {"connection_id": connection_id},
    )
    if not rows:
        return None
    return dict(rows[0]["props"])


def catalog_database_name(props: dict[str, Any]) -> str:
    """Return the catalog ``Database`` name linked to a connection node."""
    return str(props.get("name") or "").strip()


def list_custom_analysis_ids_for_database(database_name: str) -> list[str]:
    """Return CustomAnalysis ids whose SQL references catalog nodes in *database_name*."""
    rows = get_neo4j_conn().query_read(
        f"""
        MATCH (db:{Labels.DB} {{name: $database_name}})-[:{Edges.CONTAINS}*]->(catalog)
        MATCH (sql:{Labels.SQL})-[:{Edges.SQL}]->(catalog)
        MATCH (ca:{Labels.CUSTOM_ANALYSIS})-[:{Edges.HAS_SQL}]->(sql)
        RETURN DISTINCT ca.id AS id
        """,
        {"database_name": database_name},
    )
    return [str(row["id"]) for row in rows]


def delete_catalog_for_database(database_name: str) -> None:
    """Remove a catalog database, its schema subtree, and related Sql/CustomAnalysis nodes."""
    conn = get_neo4j_conn()
    conn.query_write(
        f"""
        MATCH (db:{Labels.DB} {{name: $database_name}})-[:{Edges.CONTAINS}*]->(catalog)
        MATCH (sql:{Labels.SQL})-[:{Edges.SQL}]->(catalog)
        MATCH (ca:{Labels.CUSTOM_ANALYSIS})-[:{Edges.HAS_SQL}]->(sql)
        DETACH DELETE ca, sql
        """,
        {"database_name": database_name},
    )
    conn.query_write(
        f"""
        MATCH (db:{Labels.DB} {{name: $database_name}})
        OPTIONAL MATCH (db)-[:{Edges.CONTAINS}*]->(child)
        DETACH DELETE db, child
        """,
        {"database_name": database_name},
    )


def delete_connection(connection_id: str) -> None:
    """Remove connection metadata from the catalog DB node.

    The DB node itself is kept so that ``delete_catalog_for_database`` can
    still clean up the schema subtree in a subsequent call.
    """
    get_neo4j_conn().query_write(
        f"""
        MATCH (db:{Labels.DB} {{connection_id: $connection_id}})
        REMOVE db.connection_id, db.connection_name, db.connection_type,
               db.connection_string, db.connection_create_date, db.connection_last_pulled
        """,
        {"connection_id": connection_id},
    )
