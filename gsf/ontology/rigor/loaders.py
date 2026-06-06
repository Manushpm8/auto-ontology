"""Data loading for the Rigor pipeline.

Primary source: Neo4j graph (tables, columns with descriptions, FKs, SQL queries).
"""

from __future__ import annotations

import logging
from typing import Any

from nemo_retriever.tabular_data.ingestion.model.reserved_words import (
    Edges,
    Labels,
)
from nemo_retriever.tabular_data.neo4j import get_neo4j_conn

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Schema discovery
# ---------------------------------------------------------------------------

_FETCH_SCHEMAS_QUERY = f"""
MATCH (db:{Labels.DB} {{name: $db_name}})-[:{Edges.CONTAINS}]->(s:{Labels.SCHEMA})
RETURN s.name AS schema_name
ORDER BY s.name
"""


def fetch_schemas_for_database(database_name: str) -> list[str]:
    """Return all schema names under a database node in Neo4j."""
    conn = get_neo4j_conn()
    rows = conn.query_read(_FETCH_SCHEMAS_QUERY, {"db_name": database_name})
    return [r["schema_name"] for r in rows]


# ---------------------------------------------------------------------------
# Neo4j read queries (reused from attributes_extraction.py)
# ---------------------------------------------------------------------------

_FETCH_TABLES_QUERY = f"""
MATCH (db:{Labels.DB})-[:{Edges.CONTAINS}]->
      (s:{Labels.SCHEMA} {{name: $schema_name}})-[:{Edges.CONTAINS}]->(t:{Labels.TABLE})
OPTIONAL MATCH (t)<-[:{Edges.SQL}]-(sql:{Labels.SQL})
WITH t, s, count(DISTINCT sql) AS query_count
RETURN t.id          AS id,
       t.name        AS name,
       s.name        AS schema_name,
       t.pk          AS pk,
       t.description AS description,
       query_count
ORDER BY query_count DESC
"""

_FETCH_COLUMNS_QUERY = f"""
MATCH (t:{Labels.TABLE} {{id: $table_id}})-[:{Edges.CONTAINS}]->(c:{Labels.COLUMN})
OPTIONAL MATCH (c)<-[:{Edges.SQL}]-(sql:{Labels.SQL})
WITH c, count(DISTINCT sql) AS sql_ref_count
ORDER BY c.ordinal_position
RETURN c.id              AS id,
       c.name            AS name,
       c.data_type       AS data_type,
       c.description     AS description,
       c.sample_values   AS sample_values,
       c.ordinal_position AS ordinal_position,
       sql_ref_count
"""

_FETCH_FKS_QUERY = f"""
MATCH (t:{Labels.TABLE} {{id: $table_id}})-[:{Edges.CONTAINS}]->(src:{Labels.COLUMN})
      -[:{Edges.FOREIGN_KEY}]->(tgt:{Labels.COLUMN})<-[:{Edges.CONTAINS}]-
      (tgt_table:{Labels.TABLE})
RETURN src.name       AS source_column,
       tgt.name       AS target_column,
       tgt_table.name AS target_table
"""

_FETCH_SQL_TEXTS_QUERY = f"""
MATCH (t:{Labels.TABLE} {{id: $table_id}})<-[:{Edges.SQL}]-(sql:{Labels.SQL})
RETURN sql.sql_full_query AS sql_text,
       sql.total_counter  AS total_counter
ORDER BY sql.total_counter DESC
LIMIT $limit
"""

_FETCH_ALL_SQL_QUERY = f"""
MATCH (:{Labels.DB})-[:{Edges.CONTAINS}]->
      (:{Labels.SCHEMA} {{name: $schema_name}})-[:{Edges.CONTAINS}]->(:{Labels.TABLE})
      <-[:{Edges.SQL}]-(sql:{Labels.SQL})
RETURN DISTINCT sql.sql_full_query AS sql_text,
       sql.total_counter           AS total_counter
ORDER BY sql.total_counter DESC
LIMIT $limit
"""

_FETCH_JOINS_QUERY = f"""
MATCH (:{Labels.DB})-[:{Edges.CONTAINS}]->
      (:{Labels.SCHEMA} {{name: $schema_name}})-[:{Edges.CONTAINS}]->(t1:{Labels.TABLE})
      -[j:{Edges.JOIN}]->(t2:{Labels.TABLE})
RETURN t1.name AS source_table,
       t2.name AS target_table,
       j.join_columns AS join_columns
"""


# ---------------------------------------------------------------------------
# Neo4j read functions
# ---------------------------------------------------------------------------


def fetch_sorted_tables(
    database_name: str,
    skip_threshold: int = 0,
    schema_name: str | None = None,
) -> list[dict[str, Any]]:
    """Return tables sorted by query count (descending).

    *schema_name* is the Neo4j Schema node name (e.g. ``"public"``).
    Defaults to *database_name* when omitted.
    """
    if schema_name is None:
        schema_name = database_name
    conn = get_neo4j_conn()
    rows = conn.query_read(_FETCH_TABLES_QUERY, {"schema_name": schema_name})
    tables = []
    for r in rows:
        qc = int(r.get("query_count") or 0)
        if qc < skip_threshold:
            logger.debug(
                "Skipping table %s (query_count=%d < %d)",
                r["name"],
                qc,
                skip_threshold,
            )
            continue
        tables.append(
            {
                "id": r["id"],
                "name": r["name"],
                "schema_name": r["schema_name"],
                "pk": r.get("pk"),
                "description": r.get("description") or "",
                "query_count": qc,
            }
        )
    return tables


def fetch_table_context(
    table_id: str,
    sql_limit: int = 20,
) -> dict[str, Any]:
    """Fetch columns, FKs, and top SQL texts for a single table."""
    conn = get_neo4j_conn()
    columns = conn.query_read(_FETCH_COLUMNS_QUERY, {"table_id": table_id})
    fks = conn.query_read(_FETCH_FKS_QUERY, {"table_id": table_id})
    sqls = conn.query_read(
        _FETCH_SQL_TEXTS_QUERY,
        {"table_id": table_id, "limit": sql_limit},
    )
    return {"columns": columns, "fks": fks, "sqls": sqls}


def fetch_all_sql_texts(
    database_name: str,
    limit: int = 500,
    schema_name: str | None = None,
) -> list[dict[str, Any]]:
    """Fetch all SQL query texts across the database for Phase 2."""
    if schema_name is None:
        schema_name = database_name
    conn = get_neo4j_conn()
    return conn.query_read(
        _FETCH_ALL_SQL_QUERY,
        {"schema_name": schema_name, "limit": limit},
    )


def fetch_existing_joins(
    database_name: str,
    schema_name: str | None = None,
) -> list[dict[str, Any]]:
    """Return all [:JOIN] edges already in the graph."""
    if schema_name is None:
        schema_name = database_name
    conn = get_neo4j_conn()
    return conn.query_read(_FETCH_JOINS_QUERY, {"schema_name": schema_name})
