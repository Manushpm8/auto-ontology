"""Read metadata layer (Table, Column, fk, join) from Neo4j."""

from __future__ import annotations

import logging
from typing import Any

from nemo_retriever.tabular_data.ingestion.model.reserved_words import (
    Edges,
    Labels,
)
from nemo_retriever.tabular_data.neo4j import get_neo4j_conn

logger = logging.getLogger(__name__)

_FETCH_TABLES_QUERY = f"""
MATCH (s:{Labels.SCHEMA})-[:{Edges.CONTAINS}]->(t:{Labels.TABLE})
OPTIONAL MATCH (t)<-[:{Edges.SQL}]-(sql:{Labels.SQL})
WITH t, s, count(DISTINCT sql) AS query_count
RETURN t.id AS id,
       t.name AS name,
       s.name AS schema_name,
       t.description AS description,
       t.pk as pk,
       query_count
ORDER BY query_count DESC
"""

_FETCH_COLUMNS_QUERY = f"""
MATCH (t:{Labels.TABLE} {{id: $table_id}})
OPTIONAL MATCH (t)-[:{Edges.CONTAINS}]->(c:{Labels.COLUMN})
OPTIONAL MATCH (c)-[fk:{Edges.FOREIGN_KEY}]->(:{Labels.COLUMN})
RETURN c.id AS id,
       c.name AS name,
       c.data_type AS data_type,
       c.description AS description,
       c.ordinal_position AS ordinal_position,
       c.sample_values AS sample_values,
       fk IS NOT NULL AS is_foreign_key
ORDER BY c.ordinal_position
"""

_FETCH_FKS_QUERY = f"""
MATCH (t:{Labels.TABLE} {{id: $table_id}})-[:{Edges.CONTAINS}]->(src:{Labels.COLUMN})
      -[:{Edges.FOREIGN_KEY}]->(tgt:{Labels.COLUMN})<-[:{Edges.CONTAINS}]-
      (tgt_table:{Labels.TABLE})
RETURN src.name AS source_column,
       tgt.name AS target_column,
       tgt_table.name AS target_table,
       tgt_table.id AS target_table_id
"""

_FETCH_JOINS_QUERY = f"""
MATCH (t1:{Labels.TABLE})-[j:{Edges.JOIN}]->(t2:{Labels.TABLE})
RETURN t1.name AS source_table,
       t1.id AS source_table_id,
       t2.name AS target_table,
       t2.id AS target_table_id,
       j.join_columns AS join_columns
"""

_FETCH_TABLE_BY_ID = f"""
MATCH (t:{Labels.TABLE} {{id: $table_id}})
OPTIONAL MATCH (s:{Labels.SCHEMA})-[:{Edges.CONTAINS}]->(t)
RETURN t.id AS id,
       t.name AS name,
       coalesce(s.name, '') AS schema_name,
       t.description AS description,
       t.pk as pk
"""

_FETCH_TABLE_BY_NAME = f"""
MATCH (t:{Labels.TABLE} {{name: $name}})
OPTIONAL MATCH (s:{Labels.SCHEMA})-[:{Edges.CONTAINS}]->(t)
RETURN t.id AS id,
       t.name AS name,
       coalesce(s.name, '') AS schema_name,
       t.description AS description,
       t.pk as pk
LIMIT 1
"""

_FETCH_JOIN_NEIGHBORS = f"""
MATCH (t:{Labels.TABLE} {{id: $table_id}})-[:{Edges.JOIN}]-(other:{Labels.TABLE})
RETURN DISTINCT other.id AS id,
                other.name AS name,
                other.description AS description
"""


def fetch_sorted_tables() -> list[dict[str, Any]]:
    """All tables in Neo4j ordered by query_count descending."""
    rows = get_neo4j_conn().query_read(_FETCH_TABLES_QUERY)
    return [
        {
            "id": r["id"],
            "name": r["name"],
            "schema_name": r["schema_name"],
            "description": r.get("description") or "",
            "query_count": int(r.get("query_count") or 0),
            "pk": r.get("pk") or [],
        }
        for r in rows
    ]


def fetch_table_by_id(table_id: str) -> dict[str, Any] | None:
    rows = get_neo4j_conn().query_read(_FETCH_TABLE_BY_ID, {"table_id": table_id})
    return rows[0] if rows else None


def fetch_table_by_name(name: str) -> dict[str, Any] | None:
    rows = get_neo4j_conn().query_read(_FETCH_TABLE_BY_NAME, {"name": name})
    return rows[0] if rows else None


def fetch_join_neighbors(table_id: str) -> list[dict[str, Any]]:
    """JOIN-adjacent tables (undirected), one row per neighbour."""
    return get_neo4j_conn().query_read(_FETCH_JOIN_NEIGHBORS, {"table_id": table_id})


def fetch_table_context(table_id: str) -> dict[str, Any]:
    """Columns and FKs for one table."""
    conn = get_neo4j_conn()
    rows = conn.query_read(_FETCH_COLUMNS_QUERY, {"table_id": table_id})
    columns = [
        {
            "id": r["id"],
            "name": r["name"],
            "data_type": r["data_type"],
            "description": r.get("description"),
            "ordinal_position": r.get("ordinal_position"),
            "sample_values": r.get("sample_values"),
        }
        for r in rows
        if r.get("id") is not None
    ]
    fks = conn.query_read(_FETCH_FKS_QUERY, {"table_id": table_id})
    return {"columns": columns, "fks": fks}


def fetch_join_edges() -> list[dict[str, Any]]:
    return get_neo4j_conn().query_read(_FETCH_JOINS_QUERY)


def build_tables_index() -> tuple[list[dict[str, Any]], dict[str, dict[str, Any]]]:
    """All tables indexed by name (first wins if names collide across schemas)."""
    tables = fetch_sorted_tables()
    by_name: dict[str, dict[str, Any]] = {}
    for table in tables:
        by_name.setdefault(table["name"], table)
    return tables, by_name
