# SPDX-FileCopyrightText: Copyright (c) 2024-2026, NVIDIA CORPORATION & AFFILIATES.
# All rights reserved.
# SPDX-License-Identifier: Apache-2.0

"""Data Access Layer — Neo4j queries for ``CustomAnalysis`` nodes."""

from __future__ import annotations

import uuid
from typing import Any

from nemo_retriever.tabular_data.ingestion.model.reserved_words import (
    Edges,
    Labels,
    Props,
)
from nemo_retriever.tabular_data.neo4j import get_neo4j_conn


def list_custom_analyses() -> list[dict[str, Any]]:
    """Return all ``CustomAnalysis`` nodes with their attached SQL.

    Each row contains ``id``, ``name``, ``description`` and ``sql`` — the
    ``sql_full_query`` of the related :class:`Sql` node reached via
    ``CustomAnalysis -[:HAS_SQL]-> Sql``. When a ``CustomAnalysis`` has no
    ``Sql`` neighbour the ``sql`` field is ``None`` (the node is still
    returned, so a partially ingested catalog stays visible in the UI).
    """
    neo4j_conn = get_neo4j_conn()

    rows = neo4j_conn.query_read(
        f"""
        MATCH (ca:{Labels.CUSTOM_ANALYSIS})
        OPTIONAL MATCH (ca)-[:{Edges.HAS_SQL}]->(sql:{Labels.SQL})
        WITH ca, sql
        ORDER BY ca.name
        RETURN ca.id AS id,
               ca.name AS name,
               ca.description AS description,
               sql.sql_full_query AS sql
        """,
    )

    return [
        {
            "id": r["id"],
            "name": r["name"],
            "description": r["description"],
            "sql": r["sql"],
        }
        for r in rows
    ]


def create_custom_analysis(
    name: str,
    description: str,
    sql: str,
) -> dict[str, Any]:
    """Create (or upsert by ``name``) a ``CustomAnalysis`` linked to a ``Sql``.

    ``name`` is the natural key — re-running with the same ``name`` updates
    ``description`` and re-points the ``HAS_SQL`` edge to the (possibly new)
    ``Sql`` node instead of producing duplicates. The ``Sql`` node is itself
    matched by ``sql_full_query`` so two analyses sharing the same query
    reuse the same ``Sql`` node.

    ``name``, ``description`` and ``sql`` are all required — a blank value
    is rejected by the caller (router) and never reaches this function.

    Returns ``{id, name, description, sql}`` — the same shape used by
    :func:`list_custom_analyses`.
    """
    neo4j_conn = get_neo4j_conn()

    new_id = str(uuid.uuid4())
    new_sql_id = str(uuid.uuid4())

    rows = neo4j_conn.query_write(
        f"""
        MERGE (ca:{Labels.CUSTOM_ANALYSIS} {{name: $name}})
        ON CREATE SET ca.id = $new_id, ca.description = $description
        ON MATCH  SET ca.description = $description
        MERGE (sql:{Labels.SQL} {{sql_full_query: $sql}})
        ON CREATE SET sql.id = $new_sql_id,
                      sql.name = 'query_' + $new_sql_id,
                      sql.total_counter = 0,
                      sql.last_query_timestamp = datetime()
        MERGE (ca)-[r:{Edges.HAS_SQL}]->(sql)
        ON CREATE SET r.{Props.ANALYSIS_ID} = ca.id
        RETURN ca.id              AS id,
               ca.name            AS name,
               ca.description     AS description,
               sql.sql_full_query AS sql
        """,
        {
            "name": name,
            "new_id": new_id,
            "description": description,
            "sql": sql,
            "new_sql_id": new_sql_id,
        },
    )

    record = rows[0]
    return {
        "id": record["id"],
        "name": record["name"],
        "description": record["description"],
        "sql": record["sql"],
    }
