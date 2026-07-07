# SPDX-FileCopyrightText: Copyright (c) 2024-2026, NVIDIA CORPORATION & AFFILIATES.
# All rights reserved.
# SPDX-License-Identifier: Apache-2.0

"""Neo4j data access for SqlAttribute / Sql subgraph.

Contains only functions that call ``get_neo4j_conn()`` directly.

Orchestration (SQL validation, connector resolution, VDB lifecycle)
lives in ``gsf/server/sql_attributes/service.py``.
"""

from __future__ import annotations

import logging
from typing import Any

from nemo_retriever.tabular_data.ingestion.model.reserved_words import Edges, Labels
from nemo_retriever.tabular_data.neo4j import get_neo4j_conn

from gsf.dal.users import get_accessible_catalog_ids_for_zones
from gsf.semantic.constants import (
    LABEL_COLUMN_ATTRIBUTE,
    LABEL_SQL_ATTRIBUTE,
    LABEL_TERM,
    REL_HAS_ATTRIBUTE,
    REL_PROPERTY_OF,
    REL_REPRESENTS,
)
from gsf.server.sql_utils import SqlParseError
from gsf.server.zones.constants import LABEL_ZONE, REL_ZONE_OF

logger = logging.getLogger(__name__)

# Source values stored on SqlAttribute nodes.
SQL_ATTR_SOURCE_MANUAL = "manual"
SQL_ATTR_SOURCE_SQL = "sql"


# ---------------------------------------------------------------------------
# Domain errors
# ---------------------------------------------------------------------------


class SqlAttributeNameConflict(Exception):
    """Raised when a write would collide with another SqlAttribute name."""


class SqlAttributeExpressionConflict(Exception):
    """Raised when a Term already has another SqlAttribute with this SQL."""


SqlAttributeSqlError = SqlParseError


# Shared RETURN projection — keep list/get/fetch-by-term queries in sync.
_SQL_ATTRIBUTE_FIELDS = """attr.id            AS id,
               attr.name          AS name,
               attr.description   AS description,
               attr.expression    AS expression,
               attr.source        AS source,
               sql.sql_full_query AS sql"""


def _term_zone_filter(
    zone_ids: list[str] | None,
    extra_params: dict[str, Any] | None = None,
) -> tuple[str, dict[str, Any]]:
    """Return a Cypher ``WHERE`` clause and params for term zone scoping."""
    params = dict(extra_params or {})
    if zone_ids is None:
        return "", params
    table_ids = list(get_accessible_catalog_ids_for_zones(zone_ids)["table_ids"])
    term_filter = (
        f"WHERE NOT EXISTS {{"
        f" (other:{Labels.TABLE})-[:{REL_REPRESENTS}]->(term)"
        f" WHERE NOT other.id IN $table_ids"
        f" }}"
    )
    params["table_ids"] = table_ids
    return term_filter, params


# ---------------------------------------------------------------------------
# Reads
# ---------------------------------------------------------------------------


def list_sql_attributes() -> list[dict[str, Any]]:
    """Return every SqlAttribute with its connected Term and SQL text."""
    return get_neo4j_conn().query_read(
        f"""
        MATCH (attr:{LABEL_SQL_ATTRIBUTE})
        OPTIONAL MATCH (attr)-[:{REL_PROPERTY_OF}]->(term:{LABEL_TERM})
        OPTIONAL MATCH (attr)-[:{Edges.HAS_SQL}]->(sql:{Labels.SQL})
        RETURN {_SQL_ATTRIBUTE_FIELDS},
               term.id          AS term_id,
               term.name        AS term_name
        ORDER BY attr.name
        """
    )


def get_sql_attribute(attr_id: str) -> dict[str, Any] | None:
    """Return a single SqlAttribute by id, or None."""
    rows = get_neo4j_conn().query_read(
        f"""
        MATCH (attr:{LABEL_SQL_ATTRIBUTE} {{id: $id}})
        OPTIONAL MATCH (attr)-[:{REL_PROPERTY_OF}]->(term:{LABEL_TERM})
        OPTIONAL MATCH (attr)-[:{Edges.HAS_SQL}]->(sql:{Labels.SQL})
        RETURN {_SQL_ATTRIBUTE_FIELDS},
               term.id          AS term_id,
               term.name        AS term_name
        """,
        {"id": attr_id},
    )
    return rows[0] if rows else None


def get_full_sql_attribute_by_id(
    attr_id: str,
    zone_ids: list[str] | None = None,
) -> dict[str, Any] | None:
    """Return a single SqlAttribute with its term, SQL text and resolved zones.

    Zones are resolved through the parent Term's tables (the same
    ColumnAttribute → Column → Table → Zone path used by
    ``get_full_term_by_id``), so a SQL attribute always shows the same zones
    as the term it belongs to.  When *zone_ids* is supplied the parent term
    must pass the all-or-nothing zone check, otherwise ``None`` is returned
    so viewers cannot read attributes of out-of-zone terms.
    """
    conn = get_neo4j_conn()
    term_filter, params = _term_zone_filter(zone_ids, extra_params={"id": attr_id})
    rows = conn.query_read(
        f"""
        MATCH (attr:{LABEL_SQL_ATTRIBUTE} {{id: $id}})
        MATCH (attr)-[:{REL_PROPERTY_OF}]->(term:{LABEL_TERM})
        {term_filter}
        OPTIONAL MATCH (attr)-[:{Edges.HAS_SQL}]->(sql:{Labels.SQL})
        RETURN {_SQL_ATTRIBUTE_FIELDS},
               term.id          AS term_id,
               term.name        AS term_name
        """,
        params,
    )
    if not rows:
        return None
    result = dict(rows[0])

    term_id = result.get("term_id")
    if not term_id:
        result["zones"] = []
        return result

    zone_filter = "" if zone_ids is None else "AND z.id IN $zone_ids"
    zone_params: dict[str, Any] = {"term_id": term_id}
    if zone_ids is not None:
        zone_params["zone_ids"] = zone_ids

    zone_rows = conn.query_read(
        f"""
        MATCH (term:{LABEL_TERM} {{id: $term_id}})
        MATCH (term)<-[:{REL_PROPERTY_OF}]-(:{LABEL_COLUMN_ATTRIBUTE})
              <-[:{REL_HAS_ATTRIBUTE}]-(:{Labels.COLUMN})
              <-[:{Edges.CONTAINS}]-(t:{Labels.TABLE})
        MATCH (z:{LABEL_ZONE})-[:{REL_ZONE_OF}]->(item)
        WHERE (item = t
           OR (item)-[:{Edges.CONTAINS}*1..2]->(t))
              {zone_filter}
        RETURN DISTINCT z.id    AS id,
                        z.name  AS name,
                        z.color AS color
        ORDER BY z.name
        """,
        zone_params,
    )
    result["zones"] = [dict(r) for r in zone_rows]
    return result


def fetch_sql_attributes(
    zone_ids: list[str] | None = None,
) -> list[dict[str, Any]]:
    """Return SqlAttribute nodes, optionally restricted to zone-visible terms."""
    term_filter, params = _term_zone_filter(zone_ids)
    return get_neo4j_conn().query_read(
        f"""
        MATCH (attr:{LABEL_SQL_ATTRIBUTE})-[:{REL_PROPERTY_OF}]->(term:{LABEL_TERM})
        {term_filter}
        OPTIONAL MATCH (attr)-[:{Edges.HAS_SQL}]->(sql:{Labels.SQL})
        RETURN {_SQL_ATTRIBUTE_FIELDS},
               term.id          AS term_id,
               term.name        AS term_name
        ORDER BY term.name, attr.name
        """,
        params,
    )


def fetch_sql_attributes_by_term_id(
    term_id: str,
    zone_ids: list[str] | None = None,
) -> list[dict[str, Any]]:
    """Return SqlAttribute nodes linked to a single Term via PROPERTY_OF.

    When *zone_ids* is supplied, the term must pass the same all-or-nothing
    zone check used by ``get_full_term_by_id``; otherwise an empty list is
    returned so viewers cannot read SQL attributes for out-of-zone terms.
    """
    term_filter, params = _term_zone_filter(zone_ids, extra_params={"term_id": term_id})

    return get_neo4j_conn().query_read(
        f"""
        MATCH (term:{LABEL_TERM} {{id: $term_id}})
        {term_filter}
        MATCH (attr:{LABEL_SQL_ATTRIBUTE})-[:{REL_PROPERTY_OF}]->(term)
        OPTIONAL MATCH (attr)-[:{Edges.HAS_SQL}]->(sql:{Labels.SQL})
        RETURN {_SQL_ATTRIBUTE_FIELDS}
        ORDER BY attr.name
        """,
        params,
    )


def find_attr_by_name(name: str, exclude_id: str | None) -> dict[str, str] | None:
    """Return ``{id, name}`` of a SqlAttribute using *name*, or None."""
    rows = get_neo4j_conn().query_read(
        f"""
        MATCH (a:{LABEL_SQL_ATTRIBUTE} {{name: $name}})
        WHERE $exclude_id IS NULL OR a.id <> $exclude_id
        RETURN a.id AS id, a.name AS name
        LIMIT 1
        """,
        {"name": name, "exclude_id": exclude_id},
    )
    return {"id": rows[0]["id"], "name": rows[0]["name"]} if rows else None


def find_attr_by_expression(
    *,
    term_id: str,
    expression: str,
    exclude_id: str | None,
) -> dict[str, str] | None:
    """Return a same-term SqlAttribute with equivalent SQL, or None.

    Mirrors the legacy snippet validation behavior by ignoring case and
    collapsing whitespace before comparison.
    """
    normalized_expression = " ".join(expression.split()).lower()
    rows = get_neo4j_conn().query_read(
        f"""
        MATCH (attr:{LABEL_SQL_ATTRIBUTE})-[:{REL_PROPERTY_OF}]->(:{LABEL_TERM} {{id: $term_id}})
        WHERE $exclude_id IS NULL OR attr.id <> $exclude_id
        RETURN attr.id AS id,
               attr.name AS name,
               attr.expression AS expression
        """,
        {"term_id": term_id, "exclude_id": exclude_id},
    )
    for row in rows:
        row_expression = row.get("expression")
        if not isinstance(row_expression, str):
            continue
        if " ".join(row_expression.split()).lower() == normalized_expression:
            return {"id": row["id"], "name": row["name"]}
    return None


def get_sql_attribute_by_id(attr_id: str) -> str | None:
    """Return the id of the SqlAttribute, or None if it doesn't exist."""
    rows = get_neo4j_conn().query_read(
        f"""
        MATCH (a:{LABEL_SQL_ATTRIBUTE} {{id: $id}})
        RETURN a.id AS id
        LIMIT 1
        """,
        {"id": attr_id},
    )
    return rows[0]["id"] if rows else None


# ---------------------------------------------------------------------------
# Write helpers
# ---------------------------------------------------------------------------


def detach_existing_sql_edges(attr_id: str) -> None:
    """Drop every HAS_SQL edge leaving the SqlAttribute."""
    get_neo4j_conn().query_write(
        f"""
        MATCH (a:{LABEL_SQL_ATTRIBUTE} {{id: $id}})
              -[r:{Edges.HAS_SQL}]->(:{Labels.SQL})
        DELETE r
        """,
        {"id": attr_id},
    )


def link_to_term(attr_id: str, term_id: str) -> None:
    """Set the PROPERTY_OF edge from SqlAttribute to Term, replacing any prior link."""
    get_neo4j_conn().query_write(
        f"""
        MATCH (attr:{LABEL_SQL_ATTRIBUTE} {{id: $attr_id}})
        OPTIONAL MATCH (attr)-[old:{REL_PROPERTY_OF}]->(existing)
        WHERE existing.id <> $term_id
        DELETE old
        WITH attr
        MATCH (term:{LABEL_TERM} {{id: $term_id}})
        MERGE (attr)-[:{REL_PROPERTY_OF}]->(term)
        """,
        {"attr_id": attr_id, "term_id": term_id},
    )


def update_sql_attribute_props(
    attr_id: str,
    *,
    name: str,
    description: str,
    expression: str,
    source: str,
) -> None:
    """SET properties on an existing SqlAttribute node."""
    get_neo4j_conn().query_write(
        f"""
        MATCH (attr:{LABEL_SQL_ATTRIBUTE} {{id: $id}})
        SET attr.name        = $name,
            attr.description = $description,
            attr.expression  = $expression,
            attr.source      = $source
        """,
        {
            "id": attr_id,
            "name": name,
            "description": description,
            "expression": expression,
            "source": source,
        },
    )


def delete_sql_attribute_node(attr_id: str) -> None:
    """DETACH DELETE the SqlAttribute node."""
    get_neo4j_conn().query_write(
        f"""
        MATCH (attr:{LABEL_SQL_ATTRIBUTE} {{id: $id}})
        DETACH DELETE attr
        """,
        {"id": attr_id},
    )


# ---------------------------------------------------------------------------
# Embedding data fetch
# ---------------------------------------------------------------------------


def fetch_sql_attribute_docs(attr_id: str) -> list[dict[str, Any]]:
    """Fetch one SqlAttribute from Neo4j as embedding-ready docs.

    Returns a list of dicts with keys: text, name, label, id.
    Empty list when the attribute or its Sql node is missing.
    """
    result = get_neo4j_conn().query_read(
        f"""
        MATCH (attr:{LABEL_SQL_ATTRIBUTE} {{id: $attr_id}})
              -[:{Edges.HAS_SQL}]->(sql:{Labels.SQL})
        OPTIONAL MATCH (attr)-[:{REL_PROPERTY_OF}]->(term:{LABEL_TERM})
        RETURN collect({{
            text: 'sql_attribute: ' + attr.name +
                  CASE WHEN attr.description IS NOT NULL
                       AND trim(toString(attr.description)) <> ''
                       THEN ', description: ' + attr.description
                       ELSE '' END +
                  CASE WHEN term.name IS NOT NULL
                       THEN ', term: ' + term.name
                       ELSE '' END +
                  ', sql: ' + sql.sql_full_query,
            name: attr.name,
            label: labels(attr)[0],
            id: attr.id
        }}) AS docs
        """,
        {"attr_id": attr_id},
    )
    docs = result[0].get("docs") if result else None
    return docs or []
