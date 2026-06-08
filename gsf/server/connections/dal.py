# SPDX-FileCopyrightText: Copyright (c) 2024-2026, NVIDIA CORPORATION & AFFILIATES.
# All rights reserved.
# SPDX-License-Identifier: Apache-2.0

"""Data access for UI-managed database connections (Neo4j only)."""

from __future__ import annotations

import json
import logging
import os
from datetime import datetime, timezone
from typing import Any

from nemo_retriever.tabular_data.ingestion.model.reserved_words import Edges, Labels
from nemo_retriever.tabular_data.neo4j import get_neo4j_conn

logger = logging.getLogger(__name__)


def _utc_now() -> datetime:
    return datetime.now(timezone.utc)


def _schema_counts_by_database_name() -> dict[str, int]:
    rows = get_neo4j_conn().query_read(
        f"""
        MATCH (db:{Labels.DB})-[:{Edges.CONTAINS}]->(s:{Labels.SCHEMA})
        RETURN db.name AS name, count(s) AS schema_count
        """
    )
    return {str(r["name"]): int(r["schema_count"]) for r in rows}


def _connection_node_to_public(
    props: dict[str, Any], schema_counts: dict[str, int]
) -> dict[str, Any]:
    catalog_db = catalog_database_name(props)
    last_pulled = props.get("last_pulled")
    create_date = props.get("create_date")
    return {
        "id": str(props["id"]),
        "name": props["name"],
        "type": props["type"],
        "create_date": create_date,
        "last_pulled": last_pulled,
        "num_of_schemas": schema_counts.get(catalog_db, 0),
        "database": catalog_db,
    }


def list_connections() -> list[dict[str, Any]]:
    """Return all connections for the settings UI (no credentials)."""
    schema_counts = _schema_counts_by_database_name()
    rows = get_neo4j_conn().query_read(
        """
        MATCH (c:connection)
        RETURN properties(c) AS props
        ORDER BY c.name
        """
    )
    return [
        _connection_node_to_public(dict(row["props"]), schema_counts) for row in rows
    ]


def list_connections_for_ingest() -> list[tuple[str | None, str]]:
    """Return ``(connection_id, connection_string)`` pairs for ingestion."""
    pairs: list[tuple[str | None, str]] = []
    seen_databases: set[str] = set()
    try:
        rows = get_neo4j_conn().query_read(
            """
            MATCH (c:connection)
            WHERE c.connection_string IS NOT NULL
            RETURN c.id AS id,
                   c.connection_string AS connection_string,
                   c.database_name AS database_name
            ORDER BY c.name
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
            WHERE db.connection IS NOT NULL
            RETURN DISTINCT db.connection AS connection_string
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

    return [
        connection_string
        for _, connection_string in list_connections_for_ingest()
        if connection_string and connection_string not in seen
    ]


def update_last_pulled_at(connection_id: str) -> None:
    """Record a successful ingest pass for a UI-managed connection."""
    get_neo4j_conn().query_write(
        """
        MATCH (c:connection {id: $connection_id})
        SET c.last_pulled = $last_pulled
        """,
        {
            "connection_id": connection_id,
            "last_pulled": _utc_now().isoformat(),
        },
    )


def find_connection_for_database(database_name: str) -> str | None:
    """Return the name of an existing UI connection for *database_name*, if any."""
    rows = get_neo4j_conn().query_read(
        """
        MATCH (c:connection)
        WHERE c.database_name = $database_name
           OR $database_name IN c.selected_databases
        RETURN c.name AS name
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
    selected_databases: list[str],
    pull_info: list[dict[str, Any]],
) -> dict[str, Any]:
    """Create a connection node in Neo4j and return the public payload."""
    now = _utc_now().isoformat()
    rows = get_neo4j_conn().query_write(
        """
        CREATE (c:connection {
            id: $id,
            name: $name,
            type: $type,
            connection_string: $connection_string,
            database_name: $database_name,
            selected_databases: $selected_databases,
            pull_info: $pull_info,
            create_date: $create_date
        })
        RETURN properties(c) AS props
        """,
        {
            "id": connection_id,
            "name": name,
            "type": connection_type,
            "connection_string": connection_string,
            "database_name": database_name,
            "selected_databases": selected_databases,
            "pull_info": json.dumps(pull_info),
            "create_date": now,
        },
    )
    assert rows
    return _connection_node_to_public(
        dict(rows[0]["props"]), _schema_counts_by_database_name()
    )


def link_database_connection(*, db_name: str, connection_string: str) -> None:
    """Attach a connection string to a catalog database node."""
    get_neo4j_conn().query_write(
        f"""
        MERGE (db:{Labels.DB} {{name: $db_name}})
        ON CREATE SET db.id = randomUUID()
        SET db.connection = $connection
        """,
        {"db_name": db_name, "connection": connection_string},
    )


def get_connection_by_id(connection_id: str) -> dict[str, Any] | None:
    """Return raw connection node properties, or ``None`` when missing."""
    rows = get_neo4j_conn().query_read(
        """
        MATCH (c:connection {id: $connection_id})
        RETURN properties(c) AS props
        LIMIT 1
        """,
        {"connection_id": connection_id},
    )
    if not rows:
        return None
    return dict(rows[0]["props"])


def catalog_database_name(props: dict[str, Any]) -> str:
    """Return the catalog ``Database`` name linked to a connection node."""
    selected = props.get("selected_databases") or []
    if selected:
        return str(selected[0])
    return str(props.get("database_name") or "")


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
    """Remove a connection node."""
    get_neo4j_conn().query_write(
        """
        MATCH (c:connection {id: $connection_id})
        DETACH DELETE c
        """,
        {"connection_id": connection_id},
    )
