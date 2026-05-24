# SPDX-FileCopyrightText: Copyright (c) 2024-2026, NVIDIA CORPORATION & AFFILIATES.
# All rights reserved.
# SPDX-License-Identifier: Apache-2.0

"""Data Access Layer — Neo4j queries for ``CustomAnalysis`` nodes."""

from __future__ import annotations

from typing import Any

from nemo_retriever.tabular_data.ingestion.model.reserved_words import Edges, Labels
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
