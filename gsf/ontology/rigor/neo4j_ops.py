"""Neo4j write operations for the semantic layer."""

from __future__ import annotations

import json
import logging
import uuid

from nemo_retriever.tabular_data.ingestion.model.reserved_words import (
    Edges,
    Labels,
)
from nemo_retriever.tabular_data.neo4j import get_neo4j_conn

from gsf.ontology.rigor.models import (
    AttributeType,
    CoreOntology,
    attribute_neo4j_label,
)

logger = logging.getLogger(__name__)

RIGOR_SOURCE = "rigor"

# ---------------------------------------------------------------------------
# Cypher queries
# ---------------------------------------------------------------------------

_MERGE_TERM = """
MERGE (t:Term {name: $name, source: $source})
SET t:BusinessTerm,
    t.id = $id,
    t.description = $description,
    t.source_tables = $source_tables
RETURN t.name AS name, t.id AS id
"""

_MERGE_IS_A = """
MATCH (child:Term {name: $child_name, source: $source})
MATCH (parent:Term {name: $parent_name, source: $source})
MERGE (child)-[:IS_A]->(parent)
RETURN child.name AS child, parent.name AS parent
"""

_MERGE_PART_OF = """
MATCH (child:Term {name: $child_name, source: $source})
MATCH (parent:Term {name: $parent_name, source: $source})
MERGE (child)-[:PART_OF]->(parent)
RETURN child.name AS child, parent.name AS parent
"""

_MERGE_RELATES_TO = """
MATCH (src:Term {name: $source_term, source: $source})
MATCH (tgt:Term {name: $target_term, source: $source})
MERGE (src)-[r:RELATES_TO {name: $name}]->(tgt)
SET r.derivation = $derivation,
    r.relation_kind = $relation_kind,
    r.source_table = $source_table,
    r.source_column = $source_column,
    r.target_table = $target_table,
    r.target_column = $target_column,
    r.path_data_layer = $path_data_layer,
    r.path_semantic_layer = $path_semantic_layer
RETURN src.name AS src, r.name AS rel, tgt.name AS tgt
"""

_MERGE_ROLE = """
MATCH (src:Term {name: $source_term, source: $source})
MATCH (tgt:Term {name: $target_term, source: $source})
MERGE (src)-[r:ROLE {role_name: $name}]->(tgt)
SET r.derivation = $derivation,
    r.path_data_layer = $path_data_layer,
    r.path_semantic_layer = $path_semantic_layer
RETURN src.name AS src, r.role_name AS rel, tgt.name AS tgt
"""

_MERGE_METRIC = """
MERGE (m:Metric {name: $name, source: $source})
SET m.id = $id,
    m.expression = $expression,
    m.aggregation_type = $aggregation_type,
    m.source_tables = $source_tables
RETURN m.name AS name, m.id AS id
"""

_LINK_METRIC_TO_ATTRIBUTE = f"""
MATCH (m:Metric {{name: $metric_name, source: $source}})
MATCH (t:{Labels.TABLE})-[:{Edges.CONTAINS}]->(col:{Labels.COLUMN} {{name: $source_column}})
      -[:HAS_ATTRIBUTE]->(a {{source: $source}})
WHERE (a:ColumnAttribute OR a:SqlAttribute OR a:TextAttribute)
  AND t.name IN $source_tables
MERGE (m)-[:AGGREGATES]->(a)
RETURN m.name AS metric, a.name AS attr
"""

_LINK_METRIC_TO_TERM = f"""
MATCH (m:Metric {{name: $metric_name, source: $source}})
MATCH (t:{Labels.TABLE} {{name: $source_table}})
      -[:{Edges.CONTAINS}]->(:{Labels.COLUMN})
      -[:HAS_ATTRIBUTE]->(:ColumnAttribute|SqlAttribute|TextAttribute {{source: $source}})
      -[:PROPERTY_OF]->(bt:Term {{source: $source}})
WITH m, bt LIMIT 1
MERGE (m)-[:DERIVED_FROM]->(bt)
RETURN m.name AS metric, bt.name AS term
"""

_LOOKUP_TERM = """
MATCH (t:Term {name: $name, source: $source})
RETURN t.id AS id, t.description AS description LIMIT 1
"""

_LOOKUP_ATTRIBUTE = """
MATCH (a {name: $name, business_term: $term_name, source: $source})
WHERE a:ColumnAttribute OR a:SqlAttribute OR a:TextAttribute
RETURN a.id AS id LIMIT 1
"""


def _merge_attribute_cypher(label: str) -> str:
    return f"""
MERGE (a:{label} {{name: $name, business_term: $term_name, source: $source}})
SET a.id = $id,
    a.datatype = $datatype,
    a.attribute_type = $attribute_type,
    a.source_column = $source_column,
    a.description = $description,
    a.definition = $definition,
    a.formula = $formula,
    a.usage_hint = $usage_hint
WITH a
MATCH (bt:Term {{name: $term_name, source: $source}})
MERGE (a)-[:PROPERTY_OF]->(bt)
WITH a
OPTIONAL MATCH (t:{Labels.TABLE} {{name: $source_table}})
      -[:{Edges.CONTAINS}]->(col:{Labels.COLUMN} {{name: $source_column}})
WHERE $source_column <> ''
WITH a, col
FOREACH (_ IN CASE WHEN col IS NULL THEN [] ELSE [1] END |
    MERGE (col)-[:HAS_ATTRIBUTE]->(a)
)
RETURN a.name AS attr_name
"""


def lookup_existing_term(name: str) -> dict | None:
    """Return existing Term node metadata from Neo4j, if any."""
    rows = get_neo4j_conn().query_read(
        _LOOKUP_TERM, {"name": name, "source": RIGOR_SOURCE}
    )
    return rows[0] if rows else None


def lookup_existing_attribute(name: str, term_name: str) -> dict | None:
    """Return existing attribute node metadata from Neo4j, if any."""
    rows = get_neo4j_conn().query_read(
        _LOOKUP_ATTRIBUTE,
        {"name": name, "term_name": term_name, "source": RIGOR_SOURCE},
    )
    return rows[0] if rows else None


def _path_json(path: list[dict] | None) -> str:
    return json.dumps(path or [])


def _attribute_definition(attr) -> str | None:
    if attr.definition:
        return attr.definition
    if attr.attribute_type == "sql" and attr.formula:
        return attr.formula
    if attr.attribute_type == "text" and attr.usage_hint:
        return attr.usage_hint
    return None


def write_ontology_to_neo4j(ontology: CoreOntology) -> dict[str, int]:
    """Write the entire CoreOntology to Neo4j.

    Returns a summary of how many elements were written.
    """
    conn = get_neo4j_conn()
    stats = {
        "terms": 0,
        "is_a_edges": 0,
        "part_of_edges": 0,
        "attributes": 0,
        "object_properties": 0,
        "role_edges": 0,
        "metrics": 0,
        "metric_edges": 0,
    }

    for term in ontology.business_terms:
        node_id = str(uuid.uuid4())
        source_tables = sorted({p.source_table for p in term.provenance})
        rows = conn.query_write(
            _MERGE_TERM,
            {
                "id": node_id,
                "name": term.name,
                "description": term.description,
                "source_tables": source_tables,
                "source": RIGOR_SOURCE,
            },
        )
        if rows:
            term.id = node_id
            stats["terms"] += 1
            logger.info("  [neo4j] Term: %s (%s)", term.name, node_id)

        if term.parent:
            rows = conn.query_write(
                _MERGE_IS_A,
                {
                    "child_name": term.name,
                    "parent_name": term.parent,
                    "source": RIGOR_SOURCE,
                },
            )
            if rows:
                stats["is_a_edges"] += 1

        if term.part_of:
            rows = conn.query_write(
                _MERGE_PART_OF,
                {
                    "child_name": term.name,
                    "parent_name": term.part_of,
                    "source": RIGOR_SOURCE,
                },
            )
            if rows:
                stats["part_of_edges"] += 1

    for attr in ontology.attributes:
        if lookup_existing_attribute(attr.name, attr.term_name):
            logger.debug(
                "  [neo4j] Attribute %s.%s already exists — skip",
                attr.term_name,
                attr.name,
            )
            continue
        node_id = str(uuid.uuid4())
        attr_type: AttributeType = attr.attribute_type
        neo4j_label = attribute_neo4j_label(attr_type)
        rows = conn.query_write(
            _merge_attribute_cypher(neo4j_label),
            {
                "id": node_id,
                "name": attr.name,
                "term_name": attr.term_name,
                "datatype": attr.datatype,
                "attribute_type": attr_type,
                "source_table": attr.provenance.source_table,
                "source_column": attr.source_column,
                "source": RIGOR_SOURCE,
                "description": attr.description,
                "definition": _attribute_definition(attr),
                "formula": attr.formula,
                "usage_hint": attr.usage_hint,
            },
        )
        if rows:
            attr.id = node_id
            stats["attributes"] += 1
            logger.info(
                "  [neo4j] %s: %s.%s",
                neo4j_label,
                attr.term_name,
                attr.name,
            )

    for op in ontology.object_properties:
        params = {
            "source_term": op.source_term,
            "target_term": op.target_term,
            "name": op.name,
            "derivation": op.provenance.derivation,
            "source_table": op.provenance.source_table,
            "source_column": op.provenance.source_column or "",
            "target_table": op.provenance.target_table or "",
            "target_column": op.provenance.target_column or "",
            "relation_kind": op.relation_kind,
            "path_data_layer": _path_json(op.path_data_layer),
            "path_semantic_layer": _path_json(op.path_semantic_layer),
            "source": RIGOR_SOURCE,
        }
        if op.relation_kind == "part_of":
            rows = conn.query_write(
                _MERGE_PART_OF,
                {
                    "child_name": op.source_term,
                    "parent_name": op.target_term,
                    "source": RIGOR_SOURCE,
                },
            )
            if rows:
                stats["part_of_edges"] += 1
            continue
        if op.relation_kind == "is_a":
            rows = conn.query_write(
                _MERGE_IS_A,
                {
                    "child_name": op.source_term,
                    "parent_name": op.target_term,
                    "source": RIGOR_SOURCE,
                },
            )
            if rows:
                stats["is_a_edges"] += 1
            continue
        if op.relation_kind == "role":
            rows = conn.query_write(_MERGE_ROLE, params)
            if rows:
                stats["role_edges"] += 1
            continue
        rows = conn.query_write(_MERGE_RELATES_TO, params)
        if rows:
            stats["object_properties"] += 1

    for metric in ontology.metrics:
        node_id = str(uuid.uuid4())
        rows = conn.query_write(
            _MERGE_METRIC,
            {
                "id": node_id,
                "name": metric.name,
                "expression": metric.expression,
                "aggregation_type": metric.aggregation_type.value,
                "source_tables": metric.source_tables,
                "source": RIGOR_SOURCE,
            },
        )
        if rows:
            metric.id = node_id
            stats["metrics"] += 1

        if not metric.source_column or not metric.source_tables:
            continue

        linked = conn.query_write(
            _LINK_METRIC_TO_ATTRIBUTE,
            {
                "metric_name": metric.name,
                "source_column": metric.source_column,
                "source_tables": metric.source_tables,
                "source": RIGOR_SOURCE,
            },
        )
        if linked:
            stats["metric_edges"] += len(linked)
            continue

        for src_table in metric.source_tables:
            fallback = conn.query_write(
                _LINK_METRIC_TO_TERM,
                {
                    "metric_name": metric.name,
                    "source_table": src_table,
                    "source": RIGOR_SOURCE,
                },
            )
            if fallback:
                stats["metric_edges"] += 1
                break

    logger.info("[neo4j] Write complete: %s", stats)
    return stats
