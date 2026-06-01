"""Neo4j write operations for the Rigor ontology.

Writes the constructed CoreOntology back to Neo4j as new nodes and edges:
  - BusinessTerm nodes
  - Attribute nodes: Column -[:HAS_ATTRIBUTE]-> Attribute -[:IS_PROPERTY_OF]-> BusinessTerm
  - ObjectProperty as RELATES_TO edges between BusinessTerms
  - SUBCLASS_OF edges for hierarchy
  - REPRESENTS edges from Column -> BusinessTerm (provenance)
  - Metric nodes
"""

from __future__ import annotations

import logging

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
SET bt.description = $description
RETURN bt.name AS name
"""

_MERGE_SUBCLASS = """
MATCH (child:BusinessTerm {name: $child_name, source: $source})
MATCH (parent:BusinessTerm {name: $parent_name, source: $source})
MERGE (child)-[:SUBCLASS_OF]->(parent)
RETURN child.name AS child, parent.name AS parent
"""

_MERGE_ATTRIBUTE = f"""
MERGE (a:Attribute {{name: $name, business_term: $term_name, source: $source}})
SET a.datatype = $datatype,
    a.source_column = $source_column,
    a.description = $description,
    a.formula = $formula,
    a.usage_hint = $usage_hint
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
    r.source_column = $source_column
RETURN src.name AS src, r.name AS rel, tgt.name AS tgt
"""

_MERGE_REPRESENTS = f"""
MATCH (t:{Labels.TABLE} {{name: $table_name}})
      -[:{Edges.CONTAINS}]->(c:{Labels.COLUMN})
MATCH (bt:BusinessTerm {{name: $term_name, source: $source}})
MERGE (c)-[:REPRESENTS]->(bt)
RETURN count(c) AS linked
"""

_MERGE_METRIC = """
MERGE (m:Metric {name: $name, source: $source})
SET m.expression = $expression,
    m.aggregation_type = $aggregation_type,
    m.source_tables = $source_tables
RETURN m.name AS name
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
        "represents_links": 0,
        "metrics": 0,
    }

    # 1. Write BusinessTerm nodes
    for term in ontology.business_terms:
        rows = conn.query_write(
            _MERGE_BUSINESS_TERM,
            {
                "name": term.name,
                "description": term.description,
                "source": RIGOR_SOURCE,
            },
        )
        if rows:
            stats["business_terms"] += 1
            logger.info("  [neo4j] BusinessTerm: %s", term.name)

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

        for prov in term.provenance:
            conn.query_write(
                _MERGE_REPRESENTS,
                {
                    "table_name": prov.source_table,
                    "term_name": term.name,
                    "source": RIGOR_SOURCE,
                },
            )
            stats["represents_links"] += 1

    # 2. Write Attribute nodes: Column -[:HAS_ATTRIBUTE]-> Attribute -[:IS_PROPERTY_OF]-> BusinessTerm
    for attr in ontology.attributes:
        rows = conn.query_write(
            _MERGE_ATTRIBUTE,
            {
                "name": attr.name,
                "term_name": attr.term_name,
                "datatype": attr.datatype,
                "source_table": attr.provenance.source_table,
                "source_column": attr.source_column,
                "source": RIGOR_SOURCE,
                "description": attr.description,
                "formula": attr.formula,
                "usage_hint": attr.usage_hint,
            },
        )
        if rows:
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
                "source": RIGOR_SOURCE,
            },
        )
        if rows:
            stats["object_properties"] += 1

    # 4. Write Metric nodes
    for metric in ontology.metrics:
        rows = conn.query_write(
            _MERGE_METRIC,
            {
                "name": metric.name,
                "expression": metric.expression,
                "aggregation_type": metric.aggregation_type.value,
                "source_tables": metric.source_tables,
                "source": RIGOR_SOURCE,
            },
        )
        if rows:
            stats["metrics"] += 1

    logger.info(
        "[neo4j] Write complete: %d business_terms, %d attributes, %d OPs, "
        "%d subclass, %d represents, %d metrics",
        stats["business_terms"],
        stats["attributes"],
        stats["object_properties"],
        stats["subclass_edges"],
        stats["represents_links"],
        stats["metrics"],
    )
    return stats
