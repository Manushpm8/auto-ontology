"""Neo4j write operations for the Rigor ontology.

Writes the constructed CoreOntology back to Neo4j as new nodes and edges:
  - BusinessTerm nodes
  - ColumnAttribute nodes: Column -[:HAS_ATTRIBUTE]-> ColumnAttribute -[:IS_PROPERTY_OF]-> BusinessTerm
  - ObjectProperty as RELATES_TO edges between BusinessTerms
  - SUBCLASS_OF edges for hierarchy
  - SqlAttribute nodes
"""

from __future__ import annotations

import logging
import uuid

from nemo_retriever.tabular_data.ingestion.model.reserved_words import (
    Edges,
    Labels,
)
from nemo_retriever.tabular_data.neo4j import get_neo4j_conn

from gsf.ontology.rigor.models import CoreOntology

logger = logging.getLogger(__name__)

RIGOR_SOURCE = "rigor"

# ---------------------------------------------------------------------------
# Cypher queries
# ---------------------------------------------------------------------------

_MERGE_BUSINESS_TERM = """
MERGE (bt:BusinessTerm {name: $name, source: $source})
SET bt.id = $id,
    bt.description = $description,
    bt.source_tables = $source_tables
RETURN bt.name AS name, bt.id AS id
"""

_MERGE_SUBCLASS = """
MATCH (child:BusinessTerm {name: $child_name, source: $source})
MATCH (parent:BusinessTerm {name: $parent_name, source: $source})
MERGE (child)-[:SUBCLASS_OF]->(parent)
RETURN child.name AS child, parent.name AS parent
"""

_MERGE_ATTRIBUTE = f"""
MERGE (a:ColumnAttribute {{name: $name, business_term: $term_name, source: $source}})
SET a.id = $id,
    a.datatype = $datatype,
    a.source_column = $source_column,
    a.description = $description,
    a.formula = $formula,
    a.usage_hint = $usage_hint,
    a.is_primary_key = $is_primary_key
WITH a
MATCH (bt:BusinessTerm {{name: $term_name, source: $source}})
MERGE (a)-[:IS_PROPERTY_OF]->(bt)
WITH a
MATCH (t:{Labels.TABLE} {{name: $source_table}})
      -[:{Edges.CONTAINS}]->(col:{Labels.COLUMN} {{name: $source_column}})
MERGE (col)-[:HAS_ATTRIBUTE]->(a)
RETURN a.name AS attr_name
"""

_MERGE_OBJECT_PROPERTY = """
MATCH (src:BusinessTerm {name: $source_term, source: $source})
MATCH (tgt:BusinessTerm {name: $target_term, source: $source})
MERGE (src)-[r:RELATES_TO {name: $name}]->(tgt)
SET r.derivation = $derivation,
    r.source_table = $source_table,
    r.source_column = $source_column,
    r.target_table = $target_table,
    r.target_column = $target_column
RETURN src.name AS src, r.name AS rel, tgt.name AS tgt
"""

_MERGE_SQL_ATTRIBUTE = """
MERGE (m:SqlAttribute {name: $name, source: $source})
SET m.id = $id,
    m.expression = $expression,
    m.aggregation_type = $aggregation_type,
    m.source_tables = $source_tables
RETURN m.name AS name, m.id AS id
"""

_LINK_SQL_ATTR_TO_COL_ATTR = f"""
MATCH (m:SqlAttribute {{name: $sql_attr_name, source: $source}})
MATCH (t:{Labels.TABLE})-[:{Edges.CONTAINS}]->(col:{Labels.COLUMN} {{name: $source_column}})
      -[:HAS_ATTRIBUTE]->(a:ColumnAttribute {{source: $source}})
WHERE t.name IN $source_tables
MERGE (m)-[:AGGREGATES]->(a)
RETURN m.name AS sql_attr, a.name AS col_attr
"""

_LINK_SQL_ATTR_TO_TERM = f"""
MATCH (m:SqlAttribute {{name: $sql_attr_name, source: $source}})
MATCH (t:{Labels.TABLE} {{name: $source_table}})
      -[:{Edges.CONTAINS}]->(:{Labels.COLUMN})
      -[:HAS_ATTRIBUTE]->(:ColumnAttribute {{source: $source}})
      -[:IS_PROPERTY_OF]->(bt:BusinessTerm {{source: $source}})
WITH m, bt LIMIT 1
MERGE (m)-[:DERIVED_FROM]->(bt)
RETURN m.name AS sql_attr, bt.name AS term
"""


# ---------------------------------------------------------------------------
# Write functions
# ---------------------------------------------------------------------------


def write_ontology_to_neo4j(ontology: CoreOntology) -> dict[str, int]:
    """Write the entire CoreOntology to Neo4j.

    Returns a summary of how many elements were written.
    """
    conn = get_neo4j_conn()
    stats = {
        "business_terms": 0,
        "subclass_edges": 0,
        "attributes": 0,
        "object_properties": 0,
        "metrics": 0,
        "metric_edges": 0,
    }

    # 1. Write BusinessTerm nodes
    for term in ontology.business_terms:
        node_id = str(uuid.uuid4())
        source_tables = sorted({p.source_table for p in term.provenance})
        rows = conn.query_write(
            _MERGE_BUSINESS_TERM,
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
            stats["business_terms"] += 1
            logger.info("  [neo4j] BusinessTerm: %s (%s)", term.name, node_id)

        if term.parent:
            rows = conn.query_write(
                _MERGE_SUBCLASS,
                {
                    "child_name": term.name,
                    "parent_name": term.parent,
                    "source": RIGOR_SOURCE,
                },
            )
            if rows:
                stats["subclass_edges"] += 1

    # 2. Write ColumnAttribute nodes: Column -[:HAS_ATTRIBUTE]-> ColumnAttribute -[:IS_PROPERTY_OF]-> BusinessTerm
    for attr in ontology.attributes:
        node_id = str(uuid.uuid4())
        rows = conn.query_write(
            _MERGE_ATTRIBUTE,
            {
                "id": node_id,
                "name": attr.name,
                "term_name": attr.term_name,
                "datatype": attr.datatype,
                "source_table": attr.provenance.source_table,
                "source_column": attr.source_column,
                "source": RIGOR_SOURCE,
                "description": attr.description,
                "formula": attr.formula,
                "usage_hint": attr.usage_hint,
                "is_primary_key": attr.is_primary_key,
            },
        )
        if rows:
            attr.id = node_id
            stats["attributes"] += 1

    # 3. Write ObjectProperty edges
    for op in ontology.object_properties:
        rows = conn.query_write(
            _MERGE_OBJECT_PROPERTY,
            {
                "source_term": op.source_term,
                "target_term": op.target_term,
                "name": op.name,
                "derivation": op.provenance.derivation,
                "source_table": op.provenance.source_table,
                "source_column": op.provenance.source_column or "",
                "target_table": op.provenance.target_table or "",
                "target_column": op.provenance.target_column or "",
                "source": RIGOR_SOURCE,
            },
        )
        if rows:
            stats["object_properties"] += 1

    # 4. Write SqlAttribute nodes + link to ColumnAttributes or BusinessTerms
    for metric in ontology.metrics:
        node_id = str(uuid.uuid4())
        rows = conn.query_write(
            _MERGE_SQL_ATTRIBUTE,
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

        # Try linking to the ColumnAttribute via the Column graph path
        linked = conn.query_write(
            _LINK_SQL_ATTR_TO_COL_ATTR,
            {
                "sql_attr_name": metric.name,
                "source_column": metric.source_column,
                "source_tables": metric.source_tables,
                "source": RIGOR_SOURCE,
            },
        )
        if linked:
            stats["metric_edges"] += len(linked)
            logger.info(
                "  [neo4j] SqlAttribute %s -[:AGGREGATES]-> %s",
                metric.name,
                [r["col_attr"] for r in linked],
            )
            continue

        # Fallback: link to BusinessTerm via DERIVED_FROM
        for src_table in metric.source_tables:
            fallback = conn.query_write(
                _LINK_SQL_ATTR_TO_TERM,
                {
                    "sql_attr_name": metric.name,
                    "source_table": src_table,
                    "source": RIGOR_SOURCE,
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
