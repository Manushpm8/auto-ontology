"""Data loading for the Rigor pipeline.

Primary source: Neo4j graph (tables, columns, FKs, SQL queries).
Supplementary: BIRD evidence strings and value_description CSVs.
"""

from __future__ import annotations

import csv
import json
import logging
from pathlib import Path
from typing import Any

from nemo_retriever.tabular_data.ingestion.model.reserved_words import (
    Edges,
    Labels,
)
from nemo_retriever.tabular_data.neo4j import get_neo4j_conn

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Neo4j read queries (reused from attributes_extraction.py)
# ---------------------------------------------------------------------------

_FETCH_TABLES_QUERY = f"""
MATCH (db:{Labels.DB} {{name: $db_name}})-[:{Edges.CONTAINS}]->
      (s:{Labels.SCHEMA})-[:{Edges.CONTAINS}]->(t:{Labels.TABLE})
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
MATCH (db:{Labels.DB} {{name: $db_name}})-[:{Edges.CONTAINS}]->
      (:{Labels.SCHEMA})-[:{Edges.CONTAINS}]->(:{Labels.TABLE})
      <-[:{Edges.SQL}]-(sql:{Labels.SQL})
RETURN DISTINCT sql.sql_full_query AS sql_text,
       sql.total_counter           AS total_counter
ORDER BY sql.total_counter DESC
LIMIT $limit
"""

_FETCH_JOINS_QUERY = f"""
MATCH (db:{Labels.DB} {{name: $db_name}})-[:{Edges.CONTAINS}]->
      (:{Labels.SCHEMA})-[:{Edges.CONTAINS}]->(t1:{Labels.TABLE})
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
) -> list[dict[str, Any]]:
    """Return tables sorted by query count (descending)."""
    conn = get_neo4j_conn()
    rows = conn.query_read(_FETCH_TABLES_QUERY, {"db_name": database_name})
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
) -> list[dict[str, Any]]:
    """Fetch all SQL query texts across the database for Phase 2."""
    conn = get_neo4j_conn()
    return conn.query_read(
        _FETCH_ALL_SQL_QUERY,
        {"db_name": database_name, "limit": limit},
    )


def fetch_existing_joins(
    database_name: str,
) -> list[dict[str, Any]]:
    """Return all [:JOIN] edges already in the graph."""
    conn = get_neo4j_conn()
    return conn.query_read(_FETCH_JOINS_QUERY, {"db_name": database_name})


# ---------------------------------------------------------------------------
# BIRD supplementary loaders
# ---------------------------------------------------------------------------


def load_evidence(
    db_id: str,
    bird_root: str,
) -> dict[str, list[str]]:
    """Load BIRD evidence strings grouped by question for a database.

    Returns a flat list of unique, non-empty evidence strings.
    The dict is keyed "all" for the flat list, but future versions
    could group by table name.
    """
    bird_path = Path(bird_root)
    candidates = [
        bird_path / "mini_dev_postgresql.json",
        bird_path / "mini_dev_sqlite.json",
        bird_path / "dev.json",
    ]
    json_path = next((p for p in candidates if p.exists()), None)
    if json_path is None:
        logger.warning("No BIRD JSON found under %s", bird_root)
        return {}

    with open(json_path) as f:
        data = json.load(f)

    evidence_strings: list[str] = []
    for item in data:
        if item.get("db_id") != db_id:
            continue
        ev = (item.get("evidence") or "").strip()
        if ev and ev not in evidence_strings:
            evidence_strings.append(ev)

    logger.info(
        "Loaded %d evidence strings for db_id=%s from %s",
        len(evidence_strings),
        db_id,
        json_path.name,
    )
    return {"all": evidence_strings}


def load_value_descriptions(
    db_id: str,
    bird_root: str,
) -> dict[str, dict[str, str]]:
    """Load value_description from BIRD database_description CSVs.

    Returns {table_name: {column_name: value_description}}.
    Only includes columns with non-empty value_description.
    """
    db_desc_dir = Path(bird_root) / "dev_databases" / db_id / "database_description"
    if not db_desc_dir.exists():
        logger.warning("No database_description dir at %s", db_desc_dir)
        return {}

    result: dict[str, dict[str, str]] = {}
    for csv_path in sorted(db_desc_dir.glob("*.csv")):
        table_name = csv_path.stem
        col_descs: dict[str, str] = {}
        with open(csv_path, newline="", encoding="utf-8") as f:
            reader = csv.DictReader(f)
            for row in reader:
                col_name = row.get("original_column_name", "").strip()
                val_desc = row.get("value_description", "").strip()
                col_desc = row.get("column_description", "").strip()
                if col_name and (val_desc or col_desc):
                    parts = []
                    if col_desc:
                        parts.append(col_desc)
                    if val_desc:
                        parts.append(val_desc)
                    col_descs[col_name] = "; ".join(parts)
        if col_descs:
            result[table_name] = col_descs

    logger.info(
        "Loaded value_descriptions for %d tables from %s",
        len(result),
        db_desc_dir,
    )
    return result


def enrich_context_with_bird(
    ctx: dict[str, Any],
    table_name: str,
    evidence: dict[str, list[str]],
    value_descs: dict[str, dict[str, str]],
) -> None:
    """Enrich a table context dict with BIRD supplementary data (in-place).

    Adds:
      ctx["evidence"] — evidence strings mentioning this table's columns
      ctx["value_descriptions"] — per-column value descriptions
    """
    col_names = {c["name"].lower() for c in ctx.get("columns", [])}

    relevant_evidence: list[str] = []
    for ev in evidence.get("all", []):
        ev_lower = ev.lower()
        if table_name.lower() in ev_lower or any(cn in ev_lower for cn in col_names):
            relevant_evidence.append(ev)

    ctx["evidence"] = relevant_evidence
    ctx["value_descriptions"] = value_descs.get(table_name, {})
