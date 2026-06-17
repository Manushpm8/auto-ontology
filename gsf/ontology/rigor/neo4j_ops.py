"""Neo4j write operations for the Rigor ontology.

Writes the constructed CoreOntology back to Neo4j as new nodes and edges:
  - Term nodes
  - ColumnAttribute nodes: Column -[:HAS_ATTRIBUTE]-> ColumnAttribute -[:PROPERTY_OF]-> Term
  - ObjectProperty as ROLE edges between Terms
  - IS_A edges for hierarchy
  - SqlAttribute nodes
"""

from __future__ import annotations

import json
import logging
import uuid

from nemo_retriever.tabular_data.ingestion.model.reserved_words import (
    Edges,
    Labels,
)
from nemo_retriever.tabular_data.neo4j import get_neo4j_conn

from gsf.ontology.rigor.models import CoreOntology

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Cypher queries
# ---------------------------------------------------------------------------

_MERGE_TERM = """
MERGE (bt:Term {name: $name, database: $database_name})
ON CREATE SET bt.id = $id
WITH bt
CALL {
  WITH bt
  WITH bt, coalesce(bt.description, '') AS old, coalesce($description, '') AS new
  WITH bt, old, new,
       CASE
         WHEN new = '' THEN old
         WHEN old = '' THEN new
         WHEN old CONTAINS new THEN old
         WHEN new CONTAINS old THEN new
         ELSE old + ' | ' + new
       END AS merged
  SET bt.description = merged
}
RETURN bt.name AS name, bt.id AS id
"""

_MERGE_REPRESENTS = f"""
MATCH (t:{Labels.TABLE} {{id: $table_id}})
MATCH (bt:Term {{name: $term_name, database: $database_name}})
MERGE (t)-[:REPRESENTS]->(bt)
RETURN t.name AS table_name, bt.name AS term_name
"""

_MERGE_SUBCLASS = """
MATCH (child:Term {name: $child_name, database: $database_name})
MATCH (parent:Term {name: $parent_name, database: $database_name})
MERGE (child)-[:IS_A]->(parent)
RETURN child.name AS child, parent.name AS parent
"""

_MERGE_ATTRIBUTE = f"""
MERGE (a:ColumnAttribute {{name: $name, business_term: $term_name}})
SET a.id = $id,
    a.datatype = $datatype,
    a.source_column = $source_column,
    a.formula = $formula,
    a.usage_hint = $usage_hint,
    a.is_primary_key = $is_primary_key
WITH a
CALL {{
  WITH a
  WITH a, coalesce(a.description, '') AS old, coalesce($description, '') AS new
  WITH a, old, new,
       CASE
         WHEN new = '' THEN old
         WHEN old = '' THEN new
         WHEN old CONTAINS new THEN old
         WHEN new CONTAINS old THEN new
         ELSE old + ' | ' + new
       END AS merged
  SET a.description = merged
}}
WITH a
MATCH (bt:Term {{name: $term_name, database: $database_name}})
MERGE (a)-[:PROPERTY_OF]->(bt)
WITH a
MATCH (t:{Labels.TABLE} {{id: $table_id}})
      -[:{Edges.CONTAINS}]->(col:{Labels.COLUMN} {{name: $source_column}})
MERGE (col)-[:HAS_ATTRIBUTE]->(a)
RETURN a.name AS attr_name
"""

_MERGE_OBJECT_PROPERTY = """
MATCH (src:Term {name: $source_term, database: $database_name})
MATCH (tgt:Term {name: $target_term, database: $database_name})
MERGE (src)-[r:ROLE {name: $name}]->(tgt)
SET r.derivation = $derivation,
    r.join_path = $join_path
RETURN src.name AS src, r.name AS rel, tgt.name AS tgt
"""

_MERGE_SQL_ATTRIBUTE = """
MERGE (m:SqlAttribute {name: $name})
SET m.id = $id,
    m.expression = $expression,
    m.aggregation_type = $aggregation_type,
    m.source_tables = $source_tables
RETURN m.name AS name, m.id AS id
"""

_LINK_SQL_ATTR_TO_COL_ATTR = f"""
MATCH (m:SqlAttribute {{name: $sql_attr_name}})
MATCH (t:{Labels.TABLE} {{id: $table_id}})
      -[:{Edges.CONTAINS}]->(col:{Labels.COLUMN} {{name: $source_column}})
      -[:HAS_ATTRIBUTE]->(a:ColumnAttribute)
MERGE (m)-[:AGGREGATES]->(a)
RETURN m.name AS sql_attr, a.name AS col_attr
"""

_LINK_SQL_ATTR_TO_TERM = f"""
MATCH (m:SqlAttribute {{name: $sql_attr_name}})
MATCH (t:{Labels.TABLE} {{id: $table_id}})
      -[:{Edges.CONTAINS}]->(:{Labels.COLUMN})
      -[:HAS_ATTRIBUTE]->(:ColumnAttribute)
      -[:PROPERTY_OF]->(bt:Term)
WITH m, bt LIMIT 1
MERGE (m)-[:DERIVED_FROM]->(bt)
RETURN m.name AS sql_attr, bt.name AS term
"""


# ---------------------------------------------------------------------------
# Write functions
# ---------------------------------------------------------------------------


def write_ontology_to_neo4j(
    ontology: CoreOntology,
    schema_name: str = "",
    database_name: str = "",
) -> dict[str, int]:
    """Write the entire CoreOntology to Neo4j.

    *schema_name* scopes the ``table_name → table_id`` lookup so that
    REPRESENTS, HAS_ATTRIBUTE, and SqlAttribute link queries match
    Table nodes by their unique UUID (like the semantic-layer branch).

    *database_name* enables cross-schema REPRESENTS edges: when a
    table isn't found in the current schema, we search all schemas
    under this database.

    Returns a summary of how many elements were written.
    """
    conn = get_neo4j_conn()

    # Build table_name → table_id mapping for this schema
    table_rows = conn.query_read(
        f"MATCH (:{Labels.SCHEMA} {{name: $schema_name}})"
        f"-[:{Edges.CONTAINS}]->(t:{Labels.TABLE}) "
        "RETURN t.id AS id, t.name AS name",
        {"schema_name": schema_name},
    )
    table_id_map: dict[str, str] = {r["name"]: r["id"] for r in table_rows}

    # Cross-schema fallback: (schema, table_name) → table_id across ALL
    # schemas in the database (used only for REPRESENTS edges on FK targets).
    # Keyed by (schema, table_name) to avoid collisions when two schemas
    # have tables with the same name.
    cross_schema_id_map: dict[tuple[str, str], str] = {}
    cross_schema_any: dict[str, str] = {}
    if database_name:
        all_rows = conn.query_read(
            f"MATCH (:{Labels.DB} {{name: $db_name}})-[:{Edges.CONTAINS}]->"
            f"(s:{Labels.SCHEMA})-[:{Edges.CONTAINS}]->(t:{Labels.TABLE}) "
            "RETURN t.id AS id, t.name AS name, s.name AS schema",
            {"db_name": database_name},
        )
        for r in all_rows:
            cross_schema_id_map[(r["schema"], r["name"])] = r["id"]
            cross_schema_any.setdefault(r["name"], r["id"])

    stats = {
        "business_terms": 0,
        "subclass_edges": 0,
        "attributes": 0,
        "object_properties": 0,
        "metrics": 0,
        "metric_edges": 0,
    }

    # 1. Write Term nodes + REPRESENTS edges
    for term in ontology.business_terms:
        node_id = str(uuid.uuid4())
        source_tables = sorted({p.source_table for p in term.provenance})
        rows = conn.query_write(
            _MERGE_TERM,
            {
                "id": node_id,
                "name": term.name,
                "description": term.description,
                "database_name": database_name,
            },
        )
        if rows:
            term.id = rows[0]["id"]
            stats["business_terms"] += 1
            logger.info("  [neo4j] Term: %s (%s)", term.name, term.id)

        for tbl in source_tables:
            tid = table_id_map.get(tbl)
            if not tid:
                prov_schemas = [
                    p.source_schema
                    for p in term.provenance
                    if p.source_table == tbl and p.source_schema
                ]
                for ps in prov_schemas:
                    tid = cross_schema_id_map.get((ps, tbl))
                    if tid:
                        break
                if not tid:
                    tid = cross_schema_any.get(tbl)
            if not tid:
                logger.warning(
                    "  Table %r not found in any schema — skipping REPRESENTS",
                    tbl,
                )
                continue
            conn.query_write(
                _MERGE_REPRESENTS,
                {
                    "table_id": tid,
                    "term_name": term.name,
                    "database_name": database_name,
                },
            )

        if term.parent:
            rows = conn.query_write(
                _MERGE_SUBCLASS,
                {
                    "child_name": term.name,
                    "parent_name": term.parent,
                    "database_name": database_name,
                },
            )
            if rows:
                stats["subclass_edges"] += 1

    # 2. Write ColumnAttribute nodes: Column -[:HAS_ATTRIBUTE]-> ColumnAttribute -[:PROPERTY_OF]-> Term
    for attr in ontology.attributes:
        tid = table_id_map.get(attr.provenance.source_table)
        if not tid:
            logger.warning(
                "  Table %r not found — skipping attribute %r",
                attr.provenance.source_table,
                attr.name,
            )
            continue
        node_id = str(uuid.uuid4())
        rows = conn.query_write(
            _MERGE_ATTRIBUTE,
            {
                "id": node_id,
                "name": attr.name,
                "term_name": attr.term_name,
                "datatype": attr.datatype,
                "table_id": tid,
                "source_column": attr.source_column,
                "description": attr.description,
                "formula": attr.formula,
                "usage_hint": attr.usage_hint,
                "is_primary_key": attr.is_primary_key,
                "database_name": database_name,
            },
        )
        if rows:
            attr.id = node_id
            stats["attributes"] += 1

    # 3. Write ROLE edges with join_path
    for op in ontology.object_properties:
        join_path_data = [hop.model_dump() for hop in op.join_path]
        if not join_path_data and op.provenance.source_column:
            join_path_data = [
                {
                    "source_table": op.provenance.source_table,
                    "source_schema": op.provenance.source_schema,
                    "source_column": op.provenance.source_column,
                    "target_table": op.provenance.target_table or "",
                    "target_schema": op.provenance.target_schema,
                    "target_column": op.provenance.target_column or "",
                }
            ]
        rows = conn.query_write(
            _MERGE_OBJECT_PROPERTY,
            {
                "source_term": op.source_term,
                "target_term": op.target_term,
                "name": op.name,
                "derivation": op.provenance.derivation,
                "join_path": json.dumps(join_path_data),
                "database_name": database_name,
            },
        )
        if rows:
            stats["object_properties"] += 1

    # 4. Write SqlAttribute nodes + link to ColumnAttributes or Terms
    for metric in ontology.metrics:
        node_id = str(uuid.uuid4())
        rows = conn.query_write(
            _MERGE_SQL_ATTRIBUTE,
            {
                "id": node_id,
                "name": metric.name,
                "expression": metric.expression,
                "aggregation_type": metric.aggregation_type.value,
                "source_tables": sorted(metric.source_tables),
            },
        )
        if rows:
            metric.id = node_id
            stats["metrics"] += 1

        if not metric.source_column or not metric.source_tables:
            continue

        linked: list = []
        for src_table in metric.source_tables:
            tid = table_id_map.get(src_table)
            if not tid:
                continue
            linked = conn.query_write(
                _LINK_SQL_ATTR_TO_COL_ATTR,
                {
                    "sql_attr_name": metric.name,
                    "table_id": tid,
                    "source_column": metric.source_column,
                },
            )
            if linked:
                stats["metric_edges"] += len(linked)
                logger.info(
                    "  [neo4j] SqlAttribute %s -[:AGGREGATES]-> %s",
                    metric.name,
                    [r["col_attr"] for r in linked],
                )
                break

        if not linked:
            for src_table in metric.source_tables:
                tid = table_id_map.get(src_table)
                if not tid:
                    continue
                fallback = conn.query_write(
                    _LINK_SQL_ATTR_TO_TERM,
                    {
                        "sql_attr_name": metric.name,
                        "table_id": tid,
                    },
                )
                if fallback:
                    stats["metric_edges"] += 1
                    logger.info(
                        "  [neo4j] SqlAttribute %s -[:DERIVED_FROM]-> %s",
                        metric.name,
                        fallback[0]["term"],
                    )
                    break

    logger.info(
        "[neo4j] Write complete: %d business_terms, %d attributes, %d OPs, "
        "%d subclass, %d metrics (%d linked to attrs)",
        stats["business_terms"],
        stats["attributes"],
        stats["object_properties"],
        stats["subclass_edges"],
        stats["metrics"],
        stats["metric_edges"],
    )
    return stats
