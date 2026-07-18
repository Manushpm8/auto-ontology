# SPDX-FileCopyrightText: Copyright (c) 2026, NVIDIA CORPORATION & AFFILIATES.
# All rights reserved.
# SPDX-License-Identifier: Apache-2.0

"""Neo4j read/write for ColumnAttribute and SemanticFK entities.

Also contains find_join_path, which traverses SEMANTIC_FK / HAS_ATTRIBUTE /
CONTAINS edges to resolve multi-hop join routes at retrieval time.
"""

from __future__ import annotations

import logging
from typing import Any

from nemo_retriever.tabular_data.ingestion.model.reserved_words import Edges, Labels
from nemo_retriever.tabular_data.neo4j import get_neo4j_conn

from gsf.semantic.constants import (
    LABEL_COLUMN_ATTRIBUTE,
    LABEL_TERM,
    REL_HAS_ATTRIBUTE,
    REL_PROPERTY_OF,
    REL_SEMANTIC_FK,
    SEMANTIC_SOURCE,
)
from gsf.dal.datasources import fetch_col_table_contexts

logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# ColumnAttribute CRUD
# ---------------------------------------------------------------------------


def merge_column_attribute(
    *,
    term_name: str,
    table_id: str,
    source_column: str,
    attr_name: str,
    datatype: str,
    description: str | None,
) -> str | None:
    """Merge the ColumnAttribute node and return its persistent ``id`` (UUID)."""
    rows = get_neo4j_conn().query_write(
        f"""
        MATCH (t:{Labels.TABLE} {{id: $table_id}})-[:{Edges.CONTAINS}]->
              (col:{Labels.COLUMN} {{name: $source_column}})
        MATCH (term:{LABEL_TERM} {{name: $term_name, source: $source}})
        MERGE (attr:{LABEL_COLUMN_ATTRIBUTE} {{
            name: $attr_name,
            source_column: $source_column,
            term_name: $term_name,
            table_id: $table_id,
            source: $source
        }})
        ON CREATE SET attr.id = randomUUID()
        SET attr.datatype = $datatype,
            attr.description = coalesce($description, attr.description)
        MERGE (col)-[:{REL_HAS_ATTRIBUTE}]->(attr)
        MERGE (attr)-[:{REL_PROPERTY_OF}]->(term)
        RETURN attr.id AS id
        """,
        {
            "table_id": table_id,
            "source_column": source_column,
            "term_name": term_name,
            "attr_name": attr_name,
            "datatype": datatype,
            "description": description,
            "source": SEMANTIC_SOURCE,
        },
    )
    return rows[0]["id"] if rows else None


def find_column_attribute_by_column_id(column_id: str) -> str | None:
    """Return the id of the ColumnAttribute connected to a given Column, or None."""
    rows = get_neo4j_conn().query_read(
        f"""
        MATCH (col:{Labels.COLUMN} {{id: $col_id}})-[:{REL_HAS_ATTRIBUTE}]->
              (attr:{LABEL_COLUMN_ATTRIBUTE} {{source: $source}})
        RETURN attr.id AS id LIMIT 1
        """,
        {"col_id": column_id, "source": SEMANTIC_SOURCE},
    )
    return rows[0]["id"] if rows else None


def fetch_attr_column_contexts(attr_ids: list[str]) -> dict[str, dict]:
    """Fetch Column + Table + Schema context for ColumnAttribute IDs.

    Returns a mapping of attr_id -> {attr_name, attr_description, col_id,
    col_name, table_id, table_name, schema_name}.
    """
    if not attr_ids:
        return {}
    query = """
    UNWIND $attr_ids AS attr_id
    MATCH (attr:ColumnAttribute {id: attr_id})
    OPTIONAL MATCH (col:Column)-[:SEMANTIC_FK|HAS_ATTRIBUTE]->(attr)
    OPTIONAL MATCH (col)<-[:CONTAINS]-(tbl:Table)<-[:CONTAINS]-(sch:Schema)
    RETURN attr.id AS attr_id, attr.name AS attr_name,
           attr.description AS attr_description,
           col.id AS col_id, col.name AS col_name,
           tbl.id AS table_id, tbl.name AS table_name, sch.name AS schema_name
    """
    try:
        rows = get_neo4j_conn().query_read(query, {"attr_ids": attr_ids})
    except Exception:
        logger.warning("fetch_attr_column_contexts: Neo4j query failed", exc_info=True)
        return {}
    result: dict[str, dict] = {}
    for row in rows:
        aid = row.get("attr_id")
        if not aid:
            continue
        result[aid] = {
            "attr_name": row.get("attr_name") or "",
            "attr_description": row.get("attr_description") or "",
            "col_id": row.get("col_id"),
            "col_name": row.get("col_name") or "",
            "table_id": row.get("table_id"),
            "table_name": row.get("table_name") or "",
            "schema_name": row.get("schema_name") or "",
        }
    return result


# ---------------------------------------------------------------------------
# SemanticFK
# ---------------------------------------------------------------------------


def find_unlinked_fk_columns() -> list[dict[str, Any]]:
    """Return Column nodes with no SEMANTIC_FK and no HAS_ATTRIBUTE edge.

    These are FK columns that have not yet been linked to a ColumnAttribute.
    """
    return get_neo4j_conn().query_read(
        f"""
        MATCH (t:{Labels.TABLE})-[:{Edges.CONTAINS}]->(col:{Labels.COLUMN})
        WHERE NOT (col)-[:{REL_SEMANTIC_FK}]->()
          AND NOT (col)-[:{REL_HAS_ATTRIBUTE}]->()
        OPTIONAL MATCH (col)-[:{Edges.FOREIGN_KEY}]->(tgt:{Labels.COLUMN})
        RETURN col.id          AS id,
               col.name        AS name,
               col.description AS description,
               col.sample_values AS sample_values,
               t.name          AS table_name,
               tgt.id          AS fk_target_col_id
        """
    )


def fetch_semantic_fk_related_tables(table_ids: list[str]) -> list[dict[str, Any]]:
    """Return tables reachable via a SEMANTIC_FK edge from the given tables.

    Traverses ``Table -[CONTAINS]-> Column -[SEMANTIC_FK]-> ColumnAttribute
    <-[HAS_ATTRIBUTE]- Column <-[CONTAINS]- Table``: i.e. a foreign-key column
    of one of the input tables points at a ColumnAttribute owned by a column of
    another table. The input tables themselves are excluded.

    Returns one dict per related table::

        {
            "id": <related table id>,
            "database_name": <db name or None>,
            "join_paths": [
                {
                    "source_table_id", "source_table", "source_column",
                    "source_col_id", "target_table_id", "target_table",
                    "target_column", "target_col_id",
                },
                ...
            ],
        }

    Each join path holds the two columns of the join — ``source_*`` on the input
    (resolved) table and ``target_*`` on the related table.
    """
    if not table_ids:
        return []
    try:
        rows = get_neo4j_conn().query_read(
            f"""
            UNWIND $table_ids AS tid
            MATCH (t:{Labels.TABLE} {{id: tid}})-[:{Edges.CONTAINS}]->
                  (src:{Labels.COLUMN})-[:{REL_SEMANTIC_FK}]->
                  (attr:{LABEL_COLUMN_ATTRIBUTE})<-[:{REL_HAS_ATTRIBUTE}]-
                  (tgt:{Labels.COLUMN})<-[:{Edges.CONTAINS}]-(rel:{Labels.TABLE})
            WHERE NOT rel.id IN $table_ids
            OPTIONAL MATCH (db:{Labels.DB})-[:{Edges.CONTAINS}]->
                  (:{Labels.SCHEMA})-[:{Edges.CONTAINS}]->(rel)
            RETURN DISTINCT rel.id AS id, db.name AS database_name,
                   t.id AS source_table_id, t.name AS source_table,
                   src.id AS source_col_id, src.name AS source_column,
                   tgt.id AS target_col_id, tgt.name AS target_column,
                   rel.name AS target_table
            """,
            {"table_ids": table_ids},
        )
    except Exception:
        logger.warning(
            "fetch_semantic_fk_related_tables: Neo4j query failed", exc_info=True
        )
        return []

    by_id: dict[str, dict[str, Any]] = {}
    for r in rows:
        rid = r.get("id")
        if not rid:
            continue
        entry = by_id.setdefault(
            rid,
            {
                "id": rid,
                "database_name": r.get("database_name"),
                "join_paths": [],
            },
        )
        entry["join_paths"].append(
            {
                "source_table_id": r.get("source_table_id"),
                "source_table": r.get("source_table") or "",
                "source_column": r.get("source_column") or "",
                "source_col_id": r.get("source_col_id"),
                "target_table_id": rid,
                "target_table": r.get("target_table") or "",
                "target_column": r.get("target_column") or "",
                "target_col_id": r.get("target_col_id"),
            }
        )
    return list(by_id.values())


def merge_semantic_fk(src_column_id: str, tgt_attr_id: str) -> None:
    """Create a SEMANTIC_FK edge from a source Column to a target ColumnAttribute."""
    get_neo4j_conn().query_write(
        f"""
        MATCH (src:{Labels.COLUMN} {{id: $src_id}})
        MATCH (tgt:{LABEL_COLUMN_ATTRIBUTE} {{id: $tgt_id}})
        MERGE (src)-[:{REL_SEMANTIC_FK}]->(tgt)
        """,
        {"src_id": src_column_id, "tgt_id": tgt_attr_id},
    )


# ---------------------------------------------------------------------------
# Join path traversal
# ---------------------------------------------------------------------------


def find_join_path(anchor_col_id: str, dest_col_id: str) -> list[dict]:
    """Find the shortest semantic join path between two Column nodes.

    SEMANTIC_FK is directional (Column -> ColumnAttribute) and is followed
    only in that outgoing direction: an FK column points at the attribute it
    references. Traversing it undirected would hop from one FK column up to a
    shared target attribute and back down a *different* FK column, fabricating
    a join between two unrelated columns that merely reference the same target
    (e.g. two person-id columns). HAS_ATTRIBUTE and CONTAINS stay undirected.

    Returns a list of hop dicts:
        [{source_schema, source_table, source_column,
          target_schema, target_table, target_column}, ...]
    Returns [] when anchor == dest or no path exists.
    """
    if anchor_col_id == dest_col_id:
        return []

    # apoc.path.expandConfig is used instead of shortestPath because Cypher's
    # variable-length patterns apply a single direction to every relationship
    # type, whereas we need SEMANTIC_FK outgoing-only (">") while keeping
    # HAS_ATTRIBUTE and CONTAINS bidirectional. bfs + limit:1 yields the
    # shortest path; labelFilter "-Schema" keeps Schema nodes out of the path.
    path_query = """
    MATCH (col_anchor:Column {id: $anchor_col_id})
    MATCH (col_dest:Column {id: $dest_col_id})
    CALL apoc.path.expandConfig(col_anchor, {
        relationshipFilter: 'SEMANTIC_FK>|HAS_ATTRIBUTE|CONTAINS',
        labelFilter: '-Schema',
        terminatorNodes: [col_dest],
        bfs: true,
        uniqueness: 'NODE_GLOBAL',
        minLevel: 1,
        maxLevel: 30,
        limit: 1
    }) YIELD path
    RETURN [n IN nodes(path) | {
        id: n.id,
        name: n.name,
        label: labels(n)[0]
    }] AS path_nodes
    """
    try:
        rows = get_neo4j_conn().query_read(
            path_query,
            {"anchor_col_id": anchor_col_id, "dest_col_id": dest_col_id},
        )
    except Exception:
        logger.warning(
            "find_join_path: Neo4j query failed for %s -> %s",
            anchor_col_id,
            dest_col_id,
            exc_info=True,
        )
        return []

    if not rows:
        return []

    path_nodes: list[dict] = rows[0].get("path_nodes") or []
    col_nodes = [n for n in path_nodes if n.get("label") == "Column"]
    if len(col_nodes) < 2:
        return []

    col_ids = [n["id"] for n in col_nodes if n.get("id")]
    col_ctx = fetch_col_table_contexts(col_ids)

    hops: list[dict] = []
    for i in range(0, len(col_nodes) - 1, 2):
        src = col_nodes[i]
        tgt = col_nodes[i + 1]
        src_ctx = col_ctx.get(src.get("id") or "", {})
        tgt_ctx = col_ctx.get(tgt.get("id") or "", {})
        hops.append(
            {
                "source_schema": src_ctx.get("schema_name", ""),
                "source_table": src_ctx.get("table_name", ""),
                "source_column": src.get("name", ""),
                "target_schema": tgt_ctx.get("schema_name", ""),
                "target_table": tgt_ctx.get("table_name", ""),
                "target_column": tgt.get("name", ""),
            }
        )
    return hops
