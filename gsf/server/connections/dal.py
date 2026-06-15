# SPDX-FileCopyrightText: Copyright (c) 2024-2026, NVIDIA CORPORATION & AFFILIATES.
# All rights reserved.
# SPDX-License-Identifier: Apache-2.0

"""Data access for UI-managed database connections (Neo4j only).

Connection metadata is stored directly on ``Labels.DB`` nodes so that the UI
connection and the catalog database share a single node.  A DB node is treated
as a UI-managed connection when it has a ``connection_string`` set; the catalog
database name (``db.name``) doubles as the connection's identity and label.
"""

from __future__ import annotations

import logging
from typing import Any
from nemo_retriever.tabular_data.ingestion.model.reserved_words import Edges, Labels
from nemo_retriever.tabular_data.neo4j import get_neo4j_conn

logger = logging.getLogger(__name__)


def list_connections() -> list[dict[str, Any]]:
    """Return all connections for the settings UI."""
    rows = get_neo4j_conn().query_read(
        f"""
        MATCH (db:{Labels.DB})
        WHERE db.connection_string IS NOT NULL
        RETURN properties(db) AS props
        ORDER BY db.name
        """
    )
    return [dict(row["props"]) for row in rows]


def find_connection_for_database(database_name: str) -> str | None:
    """Return the name of an existing UI connection for *database_name*, if any."""
    rows = get_neo4j_conn().query_read(
        f"""
        MATCH (db:{Labels.DB} {{name: $database_name}})
        WHERE db.connection_string IS NOT NULL
        RETURN db.name AS name
        LIMIT 1
        """,
        {"database_name": database_name},
    )
    if not rows:
        return None
    return str(rows[0]["name"])


def insert_connection(
    *,
    connection_string: str,
    database_name: str,
) -> dict[str, Any]:
    """Attach connection metadata to the catalog DB node and return the public payload."""
    rows = get_neo4j_conn().query_write(
        f"""
        MERGE (db:{Labels.DB} {{name: $database_name}})
        ON CREATE SET db.id = randomUUID()
        SET db.connection_string = $connection_string
        RETURN properties(db) AS props
        """,
        {
            "database_name": database_name,
            "connection_string": connection_string,
        },
    )
    assert rows
    return dict(rows[0]["props"])


def link_database_connection(*, database_name: str, connection_string: str) -> None:
    """Attach a connection string to a catalog database node (idempotent)."""
    get_neo4j_conn().query_write(
        f"""
        MERGE (db:{Labels.DB} {{name: $database_name}})
        ON CREATE SET db.id = randomUUID()
        SET db.connection_string = $connection_string
        """,
        {"database_name": database_name, "connection_string": connection_string},
    )


def get_connection_by_database_name(database_name: str) -> dict[str, Any] | None:
    """Return raw DB node properties for a connection, or ``None`` when missing."""
    rows = get_neo4j_conn().query_read(
        f"""
        MATCH (db:{Labels.DB} {{name: $database_name}})
        WHERE db.connection_string IS NOT NULL
        RETURN properties(db) AS props
        LIMIT 1
        """,
        {"database_name": database_name},
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


def delete_database_and_analyses(database_name: str) -> None:
    """Remove a Database node, its schema subtree, and related Sql/CustomAnalysis nodes."""
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


def delete_connection(database_name: str) -> None:
    """Remove connection metadata from the catalog DB node.

    The DB node itself is kept so that ``delete_database_and_analyses`` can
    still clean up the schema subtree in a subsequent call.
    """
    get_neo4j_conn().query_write(
        f"""
        MATCH (db:{Labels.DB} {{name: $database_name}})
        REMOVE db.connection_string
        """,
        {"database_name": database_name},
    )
