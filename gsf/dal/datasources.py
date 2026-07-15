# SPDX-FileCopyrightText: Copyright (c) 2024-2026, NVIDIA CORPORATION & AFFILIATES.
# All rights reserved.
# SPDX-License-Identifier: Apache-2.0

"""Neo4j data access for catalog nodes: Database, Schema, Table, Column.

Contains only functions that call ``get_neo4j_conn()`` directly.

All read functions use the ``fetch_*`` prefix.
Write functions use ``patch_*``, ``store_*``, or ``apply_*``.

Non-Neo4j helpers that call these functions remain in their original locations:
  - get_schemas_by_ids  →  retrieval/data_access/graph_schemas.py
  - build_tables_index  →  semantic/loaders.py
"""

from __future__ import annotations

import json
import logging
from typing import Any

import pandas as pd

from nemo_retriever.tabular_data.ingestion.model.reserved_words import Edges, Labels
from nemo_retriever.tabular_data.neo4j import get_neo4j_conn

from gsf.dal.cypher_fragments import column_description_expr
from gsf.dal.users import resolve_accessible_catalog_ids, resolve_table_filter

from gsf.semantic.constants import (
    LABEL_COLUMN_ATTRIBUTE,
    LABEL_TERM,
    REL_HAS_ATTRIBUTE,
    REL_PROPERTY_OF,
    REL_REPRESENTS,
    REL_SEMANTIC_FK,
)
from gsf.server.zones.constants import (
    LABEL_ZONE_DISABLED,
    REL_ZONE_OF,
    ZONE_LABEL_PATTERN,
)

logger = logging.getLogger(__name__)

_ALLOWED_NODE_LABELS = frozenset(Labels.LIST_OF_ALL)


# ---------------------------------------------------------------------------
# Database
# ---------------------------------------------------------------------------


def fetch_databases(zone_ids: list[str] | None = None) -> list[dict[str, Any]]:
    """Return Database rows with schema counts only; ``schemas`` is empty for lazy trees.

    When *zone_ids* is supplied the result is restricted to databases (and schema
    counts) reachable through those zones.  Pass ``None`` (or omit) to return
    the full unfiltered catalog (admin / internal callers).
    """
    data_ids_by_zone = resolve_accessible_catalog_ids(zone_ids)
    if data_ids_by_zone is not None:
        db_ids = list(data_ids_by_zone["db_ids"])
        schema_ids = list(data_ids_by_zone["schema_ids"])
        where_clause = "WHERE db.id IN $db_ids AND s.id IN $schema_ids"
        params: dict[str, Any] = {"db_ids": db_ids, "schema_ids": schema_ids}
    else:
        where_clause = ""
        params = {}

    rows = get_neo4j_conn().query_read(
        f"""
        MATCH (db:{Labels.DB})-[:{Edges.CONTAINS}]->(s:{Labels.SCHEMA})
        {where_clause}
        RETURN db.id AS id, db.name AS name, db.description AS description,
               count(s) AS schema_count
        ORDER BY name
        """,
        params,
    )
    return [
        {
            "id": r["id"],
            "name": r["name"],
            "description": r["description"],
            "num_of_schemas": int(r["schema_count"]),
            "schemas": [],
        }
        for r in rows
    ]


# ---------------------------------------------------------------------------
# Schema
# ---------------------------------------------------------------------------


def fetch_schemas_for_database(
    db_id: str,
    zone_ids: list[str] | None = None,
) -> dict[str, Any] | None:
    """Return schemas_count and a list of schema summaries for a database.

    Returns a dict with ``schemas_count`` and ``schemas`` — a list of
    ``{id, schema_name, tables_count}`` dicts.

    Returns ``None`` if no ``Database`` matches ``db_id``.

    When *zone_ids* is supplied only schemas (and their table counts) reachable
    through those zones are returned.
    """
    data_ids_by_zone = resolve_accessible_catalog_ids(zone_ids)
    if data_ids_by_zone is not None:
        schema_ids = list(data_ids_by_zone["schema_ids"])
        table_ids = list(data_ids_by_zone["table_ids"])
        where_clause = "WHERE s.id IN $schema_ids AND t.id IN $table_ids"
        params: dict[str, Any] = {
            "db_id": db_id,
            "schema_ids": schema_ids,
            "table_ids": table_ids,
        }
    else:
        where_clause = ""
        params = {"db_id": db_id}

    rows = get_neo4j_conn().query_read(
        f"""
        MATCH (db:{Labels.DB} {{id: $db_id}})-[:{Edges.CONTAINS}]->
              (s:{Labels.SCHEMA})-[:{Edges.CONTAINS}]->(t:{Labels.TABLE})
        {where_clause}
        WITH s.id AS id, s.name AS schema_name, s.description AS description,
             count(t) AS tables_count
        ORDER BY schema_name
        WITH collect({{id: id, schema_name: schema_name,
                      description: description,
                      tables_count: tables_count}}) AS schemas
        RETURN size(schemas) AS schemas_count, schemas
        """,
        params,
    )
    if not rows:
        return None
    record = rows[0]
    return {
        "schemas_count": record["schemas_count"],
        "schemas": [dict(s) for s in record["schemas"]],
    }


def fetch_all_schema_ids() -> list[str]:
    """Return all Schema node IDs."""
    return [
        r["schema_id"]
        for r in get_neo4j_conn().query_read(
            f"MATCH (s:{Labels.SCHEMA}) RETURN s.id AS schema_id",
        )
    ]


def fetch_schemas_by_ids(
    relevant_schemas_ids: list | None = None,
) -> list[dict[str, str]]:
    """Return column-level rows for the given schema IDs (all schemas when empty)."""
    schema_ids = relevant_schemas_ids or []
    result = get_neo4j_conn().query_read(
        f"""
        MATCH (db:{Labels.DB})-[:{Edges.CONTAINS}]->(schema:{Labels.SCHEMA})
              -[:{Edges.CONTAINS}]->(table:{Labels.TABLE})
              -[:{Edges.CONTAINS}]->(column:{Labels.COLUMN})
        WHERE size($schema_ids) = 0
           OR schema.id IN $schema_ids
        RETURN collect({{
            column_name:   column.name,
            column_id:     column.id,
            table_name:    table.name,
            table_id:      table.id,
            database_name: db.name,
            table_schema:  schema.name,
            data_type:     column.data_type
        }}) AS data
        """,
        {"schema_ids": schema_ids},
    )
    return result[0]["data"] if result else []


# ---------------------------------------------------------------------------
# Table
# ---------------------------------------------------------------------------

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

_FETCH_JOINS_QUERY = f"""
MATCH (t1:{Labels.TABLE})-[j:{Edges.JOIN}]->(t2:{Labels.TABLE})
RETURN t1.name AS source_table,
       t1.id AS source_table_id,
       t2.name AS target_table,
       t2.id AS target_table_id,
       j.join_columns AS join_columns
"""

_FETCH_TABLES_BY_IDS = f"""
UNWIND $table_ids AS tid
MATCH (tbl:{Labels.TABLE} {{id: tid}})
OPTIONAL MATCH (tbl)<-[:{Edges.CONTAINS}]-(sch:{Labels.SCHEMA})
OPTIONAL MATCH (tbl)-[:{Edges.CONTAINS}]->(col:{Labels.COLUMN})
WITH tbl, sch, collect({{name: col.name, data_type: col.data_type,
                         description: {column_description_expr("col")}}}) AS cols
RETURN tbl.id AS id, tbl.name AS name, tbl.description AS description,
       sch.name AS schema_name, cols
"""

_APPLY_TABLE_METADATA = f"""
UNWIND $rows AS row
MATCH (d:{Labels.DB} {{name: $database_name}})-[:{Edges.CONTAINS}]->
      (:{Labels.SCHEMA})-[:{Edges.CONTAINS}]->(t:{Labels.TABLE} {{name: row.table_name}})
SET t.description = coalesce(row.description, t.description)
"""

_APPLY_COLUMN_METADATA = f"""
UNWIND $rows AS row
MATCH (d:{Labels.DB} {{name: $database_name}})-[:{Edges.CONTAINS}]->
      (:{Labels.SCHEMA})-[:{Edges.CONTAINS}]->(t:{Labels.TABLE} {{name: row.table_name}})
      -[:{Edges.CONTAINS}]->(c:{Labels.COLUMN} {{name: row.column_name}})
SET c.description = coalesce(row.description, c.description),
    c.sample_values = coalesce(row.sample_values, c.sample_values)
"""


# Shared middle segment of the Table Cypher queries below: given `db, s, t`
# in scope, computes `columns_count`, `sql_count` and `unique_term_ids` (a
# Table's terms via both REPRESENTS and the ColumnAttribute/SEMANTIC_FK
# path, deduplicated). Interpolate between a query's initial MATCH/WHERE and
# its RETURN — used by both ``fetch_tables_for_schema`` (one schema) and
# ``fetch_data_exploration_graph`` (every visible table) so the two stay in
# sync instead of drifting as separately-maintained copies.
_TABLE_COUNTS_SUBQUERY = f"""
OPTIONAL MATCH (t)-[:{Edges.CONTAINS}]->(c:{Labels.COLUMN})
WITH db, s, t, count(DISTINCT c) AS columns_count
OPTIONAL MATCH (t)<-[:{Edges.SQL}]-(sql:{Labels.SQL})
WITH db, s, t, columns_count, count(DISTINCT sql) AS sql_count
OPTIONAL MATCH (t)-[:{REL_REPRESENTS}]->(represented:{LABEL_TERM})
WITH db, s, t, columns_count, sql_count,
     collect(DISTINCT represented.id) AS represented_term_ids
OPTIONAL MATCH (t)-[:{Edges.CONTAINS}]->(:{Labels.COLUMN})
      -[:{REL_HAS_ATTRIBUTE}|{REL_SEMANTIC_FK}]->
      (:{LABEL_COLUMN_ATTRIBUTE})-[:{REL_PROPERTY_OF}]->
      (attribute_term:{LABEL_TERM})
WITH db, s, t, columns_count, sql_count,
     represented_term_ids,
     collect(DISTINCT attribute_term.id) AS attribute_term_ids
WITH db, s, t, columns_count, sql_count,
     represented_term_ids + attribute_term_ids AS all_term_ids
WITH db, s, t, columns_count, sql_count,
     reduce(unique_ids = [], term_id IN all_term_ids |
         CASE
             WHEN term_id IS NULL OR term_id IN unique_ids THEN unique_ids
             ELSE unique_ids + term_id
         END
     ) AS unique_term_ids
"""


def fetch_tables_for_schema(
    schema_id: str,
    *,
    database_name: str
    | None = None,  # accepted for API compat; schema_id is globally unique
    zone_ids: list[str] | None = None,
) -> list[dict[str, Any]]:
    """Return Table payloads with column, SQL, and Term counts for a schema.

    When *zone_ids* is supplied only tables reachable through those zones are
    returned.
    """
    where_clause, params = resolve_table_filter(
        zone_ids, "t.id", extra_params={"schema_id": schema_id}
    )

    return get_neo4j_conn().query_read(
        f"""
        MATCH (db:{Labels.DB})-[:{Edges.CONTAINS}]->
              (s:{Labels.SCHEMA} {{id: $schema_id}})-[:{Edges.CONTAINS}]->
              (t:{Labels.TABLE})
        {where_clause}
        {_TABLE_COUNTS_SUBQUERY}
        RETURN t.id AS id,
               t.name AS name,
               t.table_type AS table_type,
               db.name AS database_name,
               s.name AS schema_name, t.description AS description,
               columns_count,
               sql_count,
               size(unique_term_ids) AS terms_count
        ORDER BY name
        """,
        params,
    )


def fetch_sorted_tables() -> list[dict[str, Any]]:
    """Return all tables ordered by query_count descending."""
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
    """Return a single Table row by id, or None if not found."""
    rows = get_neo4j_conn().query_read(_FETCH_TABLE_BY_ID, {"table_id": table_id})
    return rows[0] if rows else None


def fetch_table_by_name(name: str) -> dict[str, Any] | None:
    """Return the first Table row matching *name*, or None if not found."""
    rows = get_neo4j_conn().query_read(_FETCH_TABLE_BY_NAME, {"name": name})
    return rows[0] if rows else None


def fetch_tables_by_ids(table_ids: list[str]) -> list[dict[str, Any]]:
    """Return Table rows with nested column summaries for the given IDs."""
    if not table_ids:
        return []
    try:
        rows = get_neo4j_conn().query_read(
            _FETCH_TABLES_BY_IDS, {"table_ids": table_ids}
        )
    except Exception:
        logger.warning("fetch_tables_by_ids: Neo4j query failed", exc_info=True)
        return []
    tables = []
    for row in rows:
        tid = row.get("id")
        if not tid:
            continue
        cols = [c for c in (row.get("cols") or []) if c.get("name")]
        tables.append(
            {
                "id": tid,
                "name": row.get("name") or "",
                "description": row.get("description") or "",
                "schema_name": row.get("schema_name") or "",
                "label": "Table",
                "columns": cols,
            }
        )
    return tables


def fetch_all_tables_without_term() -> list[dict[str, Any]]:
    """Return Table nodes that have not yet been assigned a Term."""
    from nemo_retriever.tabular_data.ingestion.model.reserved_words import Edges

    return get_neo4j_conn().query_read(
        f"""
        MATCH (t:{Labels.TABLE})
        WHERE NOT (t)-[:{REL_REPRESENTS}]->()
        OPTIONAL MATCH (t)<-[:{Edges.CONTAINS}]-(sch:{Labels.SCHEMA})
        RETURN t.id AS id, t.name AS name, t.description AS description,
               sch.name AS schema_name
        ORDER BY t.name
        """
    )


def fetch_join_neighbors(table_id: str) -> list[dict[str, Any]]:
    """Return JOIN-adjacent tables (undirected), one row per neighbour."""
    return get_neo4j_conn().query_read(_FETCH_JOIN_NEIGHBORS, {"table_id": table_id})


def fetch_join_edges() -> list[dict[str, Any]]:
    """Return all JOIN edges between tables."""
    return get_neo4j_conn().query_read(_FETCH_JOINS_QUERY)


def fetch_data_exploration_edges(
    zone_ids: list[str] | None = None,
    data_ids_by_zone: dict[str, set[str]] | None = None,
) -> list[dict[str, Any]]:
    """Return table pairs connected by a shared SQL query or a foreign key.

    A query becomes an exploration edge when its ``Sql`` node references at
    least two visible tables — each such edge includes the SQL text shown
    when the user selects that connection. A foreign key between two
    tables' columns also becomes an edge (flagged ``via_foreign_key``), even
    when no stored SQL query ever referenced both tables together; that
    edge carries an empty ``queries`` list unless a shared SQL query also
    connects the same pair, in which case the two are merged into one edge.
    Pass a pre-resolved *data_ids_by_zone* (see
    ``resolve_accessible_catalog_ids``) when the caller already resolved
    *zone_ids* for this request, to skip a repeat Neo4j round trip.
    """
    data_ids_by_zone = resolve_accessible_catalog_ids(zone_ids, data_ids_by_zone)
    if data_ids_by_zone is not None:
        table_ids = list(data_ids_by_zone["table_ids"])
        if not table_ids:
            return []
        table_filter = "source.id IN $table_ids AND target.id IN $table_ids AND "
        params: dict[str, Any] = {"table_ids": table_ids}
    else:
        table_filter = ""
        params = {}

    conn = get_neo4j_conn()
    sql_rows = conn.query_read(
        f"""
        MATCH (source:{Labels.TABLE})<-[:{Edges.SQL}]-(sql:{Labels.SQL})
              -[:{Edges.SQL}]->(target:{Labels.TABLE})
        WHERE {table_filter}source.id < target.id
        WITH source, target,
             collect(DISTINCT sql.sql_full_query) AS raw_queries
        RETURN source.id AS source,
               target.id AS target,
               [query IN raw_queries
                WHERE query IS NOT NULL AND trim(toString(query)) <> ''] AS queries
        """,
        params,
    )
    fk_rows = conn.query_read(
        f"""
        MATCH (source:{Labels.TABLE})-[:{Edges.CONTAINS}]->(:{Labels.COLUMN})
              -[:{Edges.FOREIGN_KEY}]->(:{Labels.COLUMN})<-[:{Edges.CONTAINS}]-
              (target:{Labels.TABLE})
        WHERE {table_filter}source.id <> target.id
        RETURN DISTINCT source.id AS source, target.id AS target
        """,
        params,
    )

    edges: dict[tuple[str, str], dict[str, Any]] = {}
    for row in sql_rows:
        source, target = row.get("source"), row.get("target")
        if not source or not target:
            continue
        edges[(source, target)] = {
            "source": source,
            "target": target,
            "queries": [q for q in (row.get("queries") or []) if q],
            "via_foreign_key": False,
        }
    for row in fk_rows:
        a, b = row.get("source"), row.get("target")
        if not a or not b:
            continue
        key = (a, b) if a < b else (b, a)
        edge = edges.get(key)
        if edge is None:
            edges[key] = {
                "source": key[0],
                "target": key[1],
                "queries": [],
                "via_foreign_key": True,
            }
        else:
            edge["via_foreign_key"] = True

    return sorted(
        (edge for edge in edges.values() if edge["queries"] or edge["via_foreign_key"]),
        key=lambda edge: (edge["source"], edge["target"]),
    )


def fetch_table_exploration_details(
    table_id: str,
    zone_ids: list[str] | None = None,
) -> dict[str, list[dict[str, Any]]]:
    """Return SQL queries and Terms linked to one visible Table."""
    data_ids_by_zone = resolve_accessible_catalog_ids(zone_ids)
    if data_ids_by_zone is not None and table_id not in data_ids_by_zone["table_ids"]:
        return {"queries": [], "terms": []}

    conn = get_neo4j_conn()
    queries = conn.query_read(
        f"""
        MATCH (sql:{Labels.SQL})-[:{Edges.SQL}]->
              (t:{Labels.TABLE} {{id: $table_id}})
        RETURN DISTINCT sql.id AS id, sql.sql_full_query AS sql
        ORDER BY id
        """,
        {"table_id": table_id},
    )
    terms = conn.query_read(
        f"""
        MATCH (t:{Labels.TABLE} {{id: $table_id}})
              -[:{REL_REPRESENTS}]->(term:{LABEL_TERM})
        RETURN DISTINCT term.id AS id, term.name AS name,
                        term.description AS description
        UNION
        MATCH (t:{Labels.TABLE} {{id: $table_id}})
              -[:{Edges.CONTAINS}]->(:{Labels.COLUMN})
              -[:{REL_HAS_ATTRIBUTE}|{REL_SEMANTIC_FK}]->
              (:{LABEL_COLUMN_ATTRIBUTE})-[:{REL_PROPERTY_OF}]->
              (term:{LABEL_TERM})
        RETURN DISTINCT term.id AS id, term.name AS name,
                        term.description AS description
        """,
        {"table_id": table_id},
    )
    unique_terms = {row["id"]: row for row in terms if row.get("id")}
    return {
        "queries": [
            {"id": row.get("id") or "", "sql": row.get("sql") or ""}
            for row in queries
            if row.get("sql")
        ],
        "terms": list(unique_terms.values()),
    }


def fetch_table_zones_map(
    zone_ids: list[str] | None = None,
    data_ids_by_zone: dict[str, set[str]] | None = None,
) -> dict[str, list[dict[str, Any]]]:
    """Return ``{table_id: [zone, ...]}`` for every visible Table.

    Zones are resolved via ``Zone -[ZONE_OF]-> item`` where *item* is the
    table itself or one of its ancestors (Schema, Database), matching the
    resolution used by ``get_full_term_by_id`` / ``get_full_sql_attribute_by_id``.
    Used to render Zone chips in the Exploration graph without a per-node
    request. When *zone_ids* is supplied, both the visible tables and the
    returned zone names/colors are restricted to that set. Disabled zones
    are included (with ``enabled: False``) so admins can see and manage
    them; they never grant access since *zone_ids* itself is computed from
    enabled zones only. Pass ``None`` to return zones for every table
    (admin / internal callers). Pass a pre-resolved *data_ids_by_zone*
    (see ``resolve_accessible_catalog_ids``) when the caller already
    resolved *zone_ids* for this request, to skip a repeat Neo4j round trip.
    """
    data_ids_by_zone = resolve_accessible_catalog_ids(zone_ids, data_ids_by_zone)
    params: dict[str, Any] = {}
    table_filter = ""
    if data_ids_by_zone is not None:
        table_ids = list(data_ids_by_zone["table_ids"])
        if not table_ids:
            return {}
        table_filter = "WHERE t.id IN $table_ids"
        params["table_ids"] = table_ids

    # A viewer never sees a disabled zone's chip, even if its id ended up in
    # zone_ids (e.g. access granted before the zone was disabled) — admins
    # (zone_ids=None) still see disabled zones so they can manage them.
    zone_filter = (
        ""
        if zone_ids is None
        else f"AND z.id IN $zone_ids AND NOT z:{LABEL_ZONE_DISABLED}"
    )
    if zone_ids is not None:
        params["zone_ids"] = zone_ids

    rows = get_neo4j_conn().query_read(
        f"""
        MATCH (t:{Labels.TABLE})
        {table_filter}
        MATCH (z:{ZONE_LABEL_PATTERN})-[:{REL_ZONE_OF}]->(item)
        WHERE (item = t
           OR (item)-[:{Edges.CONTAINS}*1..2]->(t))
              {zone_filter}
        RETURN DISTINCT t.id   AS table_id,
                        z.id    AS id,
                        z.name  AS name,
                        z.color AS color,
                        NOT z:{LABEL_ZONE_DISABLED} AS enabled
        ORDER BY t.id, z.name
        """,
        params,
    )
    result: dict[str, list[dict[str, Any]]] = {}
    for row in rows:
        result.setdefault(row["table_id"], []).append(
            {
                "id": row["id"],
                "name": row["name"],
                "color": row["color"],
                "enabled": row["enabled"],
            }
        )
    return result


def fetch_data_exploration_graph(
    zone_ids: list[str] | None = None,
) -> dict[str, list[dict[str, Any]]]:
    """Return the whole data-layer Exploration graph in one payload.

    Builds ``{"nodes": [...], "links": [...]}`` server-side so the client
    renders the data graph from a single request instead of walking the
    catalog tree (databases → schemas → tables) with one request per level.

    Each node is a visible Table with its column / SQL / Term counts,
    owning Database and Schema ids and names, and resolved Zone chips.
    Links are the SQL-backed table connections from
    ``fetch_data_exploration_edges``. When *zone_ids* is supplied both nodes
    and links are restricted to tables reachable through those zones.

    *zone_ids* is resolved to accessible catalog ids exactly once (see
    ``resolve_accessible_catalog_ids``) and threaded through the node query,
    ``fetch_table_zones_map`` and ``fetch_data_exploration_edges`` — those
    three previously each re-resolved the same *zone_ids* independently,
    tripling the Neo4j round trips this endpoint made per request.
    """
    data_ids_by_zone = resolve_accessible_catalog_ids(zone_ids)
    where_clause, params = resolve_table_filter(
        zone_ids, "t.id", data_ids_by_zone=data_ids_by_zone
    )
    nodes = get_neo4j_conn().query_read(
        f"""
        MATCH (db:{Labels.DB})-[:{Edges.CONTAINS}]->
              (s:{Labels.SCHEMA})-[:{Edges.CONTAINS}]->
              (t:{Labels.TABLE})
        {where_clause}
        {_TABLE_COUNTS_SUBQUERY}
        RETURN t.id AS id,
               t.name AS name,
               t.table_type AS table_type,
               db.id AS database_id,
               db.name AS database_name,
               s.id AS schema_id,
               s.name AS schema_name,
               t.description AS description,
               columns_count,
               sql_count,
               size(unique_term_ids) AS terms_count
        ORDER BY name
        """,
        params,
    )
    zones_by_table = fetch_table_zones_map(zone_ids, data_ids_by_zone=data_ids_by_zone)
    return {
        "nodes": [
            {**dict(row), "zones": zones_by_table.get(row["id"], [])} for row in nodes
        ],
        "links": fetch_data_exploration_edges(
            zone_ids=zone_ids, data_ids_by_zone=data_ids_by_zone
        ),
    }


# ---------------------------------------------------------------------------
# Column
# ---------------------------------------------------------------------------

_FETCH_COLUMNS_QUERY = f"""
MATCH (t:{Labels.TABLE} {{id: $table_id}})
OPTIONAL MATCH (t)-[:{Edges.CONTAINS}]->(c:{Labels.COLUMN})
OPTIONAL MATCH (c)-[fk:{Edges.FOREIGN_KEY}]->(:{Labels.COLUMN})
RETURN c.id AS id,
       c.name AS name,
       c.data_type AS data_type,
       {column_description_expr("c")} AS description,
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

_FETCH_COL_TABLE_CONTEXTS = f"""
UNWIND $col_ids AS col_id
MATCH (col:{Labels.COLUMN} {{id: col_id}})<-[:{Edges.CONTAINS}]-(tbl:{Labels.TABLE})
      <-[:{Edges.CONTAINS}]-(sch:{Labels.SCHEMA})
RETURN col.id AS col_id, tbl.name AS table_name, sch.name AS schema_name
"""


def fetch_columns_for_table(table_id: str) -> dict[str, Any] | None:
    """Return a table dict with nested columns, or None if the table is missing."""
    rows = get_neo4j_conn().query_read(
        f"""
        MATCH (db:{Labels.DB})-[:{Edges.CONTAINS}]->(s:{Labels.SCHEMA})-[:{Edges.CONTAINS}]->
              (t:{Labels.TABLE} {{id: $table_id}})-[:{Edges.CONTAINS}]->(c:{Labels.COLUMN})
        WITH t, c, s, db ORDER BY c.ordinal_position
        WITH t, s, db, collect({{
                 id: c.id,
                 ordinal_position: c.ordinal_position,
                 column_name: c.name,
                 data_type: c.data_type,
                 description: {column_description_expr("c")},
                 sample_values: c.sample_values
             }}) AS columns
        RETURN t.name AS table_name,
               t.table_type AS table_type,
               s.name AS schema_name,
               db.name AS database_name,
               size(columns) AS columns_count,
               columns
        """,
        {"table_id": table_id},
    )
    if not rows:
        return None
    return rows[0]


def fetch_parent_table_id_for_column(column_id: str) -> str | None:
    """Return the id of the Table that contains this Column, or None."""
    rows = get_neo4j_conn().query_read(
        f"""
        MATCH (t:{Labels.TABLE})-[:{Edges.CONTAINS}]->(c:{Labels.COLUMN} {{id: $column_id}})
        RETURN t.id AS table_id
        LIMIT 1
        """,
        {"column_id": column_id},
    )
    return rows[0]["table_id"] if rows else None


def fetch_table_context(table_id: str) -> dict[str, Any]:
    """Return columns and FK edges for one table."""
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


def fetch_col_table_contexts(col_ids: list[str]) -> dict[str, dict[str, str]]:
    """Batch lookup: Column id → {table_name, schema_name}."""
    if not col_ids:
        return {}
    try:
        rows = get_neo4j_conn().query_read(
            _FETCH_COL_TABLE_CONTEXTS, {"col_ids": col_ids}
        )
    except Exception:
        logger.warning("fetch_col_table_contexts: Neo4j query failed", exc_info=True)
        return {}
    return {
        r["col_id"]: {
            "table_name": r.get("table_name") or "",
            "schema_name": r.get("schema_name") or "",
        }
        for r in rows
        if r.get("col_id")
    }


def store_column_sample_values(table_id: str, samples: dict[str, list]) -> None:
    """Write sample_values JSON onto Column nodes for a given table.

    Skips silently when *samples* is empty.
    """
    if not samples:
        return
    entries = [
        {"column_name": col, "sample_values": json.dumps(vals)}
        for col, vals in samples.items()
    ]
    get_neo4j_conn().query_write(
        f"""
        MATCH (t:{Labels.TABLE} {{id: $table_id}})-[:{Edges.CONTAINS}]->(col:{Labels.COLUMN})
        WHERE col.name IN [e IN $entries | e.column_name]
        WITH col,
             [e IN $entries WHERE e.column_name = col.name | e.sample_values][0]
             AS sv
        WHERE sv IS NOT NULL
        SET col.sample_values = sv
        """,
        {"table_id": table_id, "entries": entries},
    )


def store_column_uniqueness(table_id: str, uniqueness: dict[str, bool]) -> None:
    """Write is_unique flags onto Column nodes for a given table.

    Skips silently when *uniqueness* is empty.
    """
    if not uniqueness:
        return
    entries = [
        {"column_name": col, "is_unique": bool(is_unique)}
        for col, is_unique in uniqueness.items()
    ]
    get_neo4j_conn().query_write(
        f"""
        MATCH (t:{Labels.TABLE} {{id: $table_id}})-[:{Edges.CONTAINS}]->(col:{Labels.COLUMN})
        WHERE col.name IN [e IN $entries | e.column_name]
        WITH col,
             [e IN $entries WHERE e.column_name = col.name | e.is_unique][0]
             AS iu
        WHERE iu IS NOT NULL
        SET col.is_unique = iu
        """,
        {"table_id": table_id, "entries": entries},
    )


# ---------------------------------------------------------------------------
# Cross-entity (Table + Column batch operations)
# ---------------------------------------------------------------------------


def fetch_tables_and_columns_by_node_ids(
    node_ids: list[str],
) -> tuple[pd.DataFrame, pd.DataFrame, str]:
    """Load Table/Column rows from Neo4j as dataframes for TabularFetchEmbeddingsOp."""
    conn = get_neo4j_conn()
    columns_df = pd.DataFrame(
        conn.query_read(
            f"""
            UNWIND $ids AS id
            MATCH (db:{Labels.DB})-[:{Edges.CONTAINS}]->(s:{Labels.SCHEMA})
                  -[:{Edges.CONTAINS}]->(t:{Labels.TABLE})-[:{Edges.CONTAINS}]->(c:{Labels.COLUMN})
            WHERE t.id = id OR c.id = id
            RETURN DISTINCT
                   c.id AS id,
                   t.name AS table_name,
                   s.name AS table_schema,
                   c.name AS column_name,
                   c.data_type AS data_type,
                   {column_description_expr("c")} AS description,
                   c.sample_values AS sample_values,
                   db.name AS database_name
            """,
            {"ids": node_ids},
        ),
    )
    tables_df = pd.DataFrame(
        conn.query_read(
            f"""
            UNWIND $ids AS id
            MATCH (db:{Labels.DB})-[:{Edges.CONTAINS}]->(s:{Labels.SCHEMA})
                  -[:{Edges.CONTAINS}]->(t:{Labels.TABLE} {{id: id}})
            RETURN t.id AS id,
                   t.name AS table_name,
                   s.name AS table_schema,
                   t.table_type AS table_type,
                   t.description AS description,
                   db.name AS database_name
            """,
            {"ids": node_ids},
        ),
    )
    database_name = ""
    if not tables_df.empty:
        database_name = str(tables_df.iloc[0].get("database_name") or "")
    elif not columns_df.empty:
        database_name = str(columns_df.iloc[0].get("database_name") or "")
    return tables_df, columns_df, database_name


def apply_metadata_batch(
    database_name: str,
    table_rows: list[dict],
    column_rows: list[dict],
) -> None:
    """Batch-write description / sample_values onto Table and Column nodes.

    *table_rows* — list of ``{table_name, description}``.
    *column_rows* — list of ``{table_name, column_name, description, sample_values}``.
    Skips silently when either list is empty.
    """
    conn = get_neo4j_conn()
    if table_rows:
        conn.query_write(
            _APPLY_TABLE_METADATA,
            {"rows": table_rows, "database_name": database_name},
        )
    if column_rows:
        conn.query_write(
            _APPLY_COLUMN_METADATA,
            {"rows": column_rows, "database_name": database_name},
        )


# ---------------------------------------------------------------------------
# Any catalog node
# ---------------------------------------------------------------------------


def patch_catalog_node(
    node_id: str,
    properties: dict[str, Any],
) -> dict[str, Any] | None:
    """Write ``properties`` onto any catalog node matched by ``id``.

    Returns ``{id, label, props}`` or ``None`` when no node matches.
    This is the pure Cypher write; callers are responsible for triggering
    any downstream VDB re-embedding.
    """
    rows = get_neo4j_conn().query_write(
        f"""
        MATCH (n:{Labels.DB}|{Labels.SCHEMA}|{Labels.TABLE}|{Labels.COLUMN}
              {{id: $node_id}})
        SET n += $props
        RETURN n.id AS id, labels(n)[0] AS label, properties(n) AS props
        """,
        {"node_id": node_id, "props": properties},
    )
    if not rows:
        return None
    return {
        "id": rows[0]["id"],
        "label": rows[0]["label"],
        "props": dict(rows[0]["props"]),
    }


def fetch_node_properties_by_id(id: str, label: str | list[str]) -> dict | None:
    """Return all properties of the node with the given id and label, or None.

    Rejects unknown labels and returns None with a warning instead of raising.
    """
    labels_list = label if isinstance(label, list) else [label]
    for lbl in labels_list:
        if lbl not in _ALLOWED_NODE_LABELS:
            logger.warning(
                "Rejecting unknown label %r in fetch_node_properties_by_id", lbl
            )
            return None
    label_filter = "|".join(labels_list)
    props = get_neo4j_conn().query_read_only(
        f"""
        MATCH (n:{label_filter} {{id: $id}})
        RETURN apoc.map.setKey(properties(n), "label", labels(n)[0]) AS props
        """,
        parameters={"id": id},
    )
    return props[0]["props"] if props else None


def fetch_item_by_id(item_id: str, label: str | list[str]) -> dict | None:
    """Like ``fetch_node_properties_by_id`` but logs an error when the node is missing."""
    result = fetch_node_properties_by_id(item_id, label)
    if result is None:
        logger.error("Required item with id %r not found in graph.", item_id)
    return result
