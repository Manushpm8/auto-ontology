"""Shortest-path computation and Phase 3 role synthesis."""

from __future__ import annotations

import logging
from typing import Any

from nemo_retriever.tabular_data.ingestion.model.reserved_words import (
    Edges,
    Labels,
)
from nemo_retriever.tabular_data.neo4j import get_neo4j_conn

from gsf.ontology.rigor.models import CoreOntology, ObjectProperty, Provenance

logger = logging.getLogger(__name__)

RIGOR_SOURCE = "rigor"

_DATA_LAYER_PATH = f"""
MATCH (db:{Labels.DB} {{name: $db_name}})-[:{Edges.CONTAINS}]->
      (s:{Labels.SCHEMA} {{name: $schema_name}})-[:{Edges.CONTAINS}]->
      (src:{Labels.TABLE} {{name: $src_table}}),
      (db)-[:{Edges.CONTAINS}]->(s)-[:{Edges.CONTAINS}]->
      (tgt:{Labels.TABLE} {{name: $tgt_table}})
MATCH p = shortestPath(
    (src)-[:{Edges.FOREIGN_KEY}|{Edges.JOIN}*..6]-(tgt)
)
RETURN [n IN nodes(p) | n.name] AS path_nodes,
       [r IN relationships(p) | type(r)] AS path_rels
LIMIT 1
"""

_SEMANTIC_LAYER_PATH = """
MATCH (src:Term {name: $src_term, source: $source}),
      (tgt:Term {name: $tgt_term, source: $source})
MATCH p = shortestPath(
    (src)-[:IS_A|PART_OF|ROLE|PROPERTY_OF|RELATES_TO*..6]-(tgt)
)
RETURN [n IN nodes(p) | n.name] AS path_nodes,
       [r IN relationships(p) | type(r)] AS path_rels
LIMIT 1
"""


def compute_data_layer_path(
    database_name: str,
    schema_name: str,
    src_table: str,
    tgt_table: str,
) -> list[dict[str, Any]] | None:
    """Shortest path between physical tables over FK and JOIN edges."""
    if src_table == tgt_table:
        return [{"node": src_table, "type": "Table"}]
    rows = get_neo4j_conn().query_read(
        _DATA_LAYER_PATH,
        {
            "db_name": database_name,
            "schema_name": schema_name,
            "src_table": src_table,
            "tgt_table": tgt_table,
        },
    )
    if not rows or not rows[0].get("path_nodes"):
        return None
    nodes = rows[0]["path_nodes"]
    rels = rows[0].get("path_rels") or []
    steps: list[dict[str, Any]] = []
    for i, node in enumerate(nodes):
        step: dict[str, Any] = {"node": node, "type": "Table"}
        if i < len(rels):
            step["via"] = rels[i]
        steps.append(step)
    return steps


def compute_semantic_layer_path(
    src_term: str,
    tgt_term: str,
) -> list[dict[str, Any]] | None:
    """Shortest path between Terms over semantic edges."""
    if src_term == tgt_term:
        return [{"node": src_term, "type": "Term"}]
    rows = get_neo4j_conn().query_read(
        _SEMANTIC_LAYER_PATH,
        {"src_term": src_term, "tgt_term": tgt_term, "source": RIGOR_SOURCE},
    )
    if not rows or not rows[0].get("path_nodes"):
        return None
    nodes = rows[0]["path_nodes"]
    rels = rows[0].get("path_rels") or []
    steps: list[dict[str, Any]] = []
    for i, node in enumerate(nodes):
        step: dict[str, Any] = {"node": node, "type": "Term"}
        if i < len(rels):
            step["via"] = rels[i]
        steps.append(step)
    return steps


def _table_pair_key(a: str, b: str) -> tuple[str, str]:
    return (a, b) if a <= b else (b, a)


def synthesize_role_edges(
    ontology: CoreOntology,
    database_name: str,
    schema_name: str,
) -> int:
    """Phase 3: attach paths to role/relationship edges between Terms.

    Returns count of edges enriched with path metadata.
    """
    enriched = 0
    seen_pairs: set[tuple[str, str]] = set()

    for op in ontology.object_properties:
        pair = _table_pair_key(op.source_term, op.target_term)
        if pair in seen_pairs:
            continue
        seen_pairs.add(pair)

        src_table = op.provenance.source_table
        tgt_table = op.provenance.target_table or src_table
        if not src_table or not tgt_table:
            src_tables = {
                ontology.table_to_term.get(k, k)
                for k in ontology.table_to_term
                if ontology.table_to_term[k] == op.source_term
            }
            tgt_tables = {
                ontology.table_to_term.get(k, k)
                for k in ontology.table_to_term
                if ontology.table_to_term[k] == op.target_term
            }
            for st in ontology.table_to_term:
                if ontology.table_to_term[st] == op.source_term:
                    src_table = st
                    break
            for tt in ontology.table_to_term:
                if ontology.table_to_term[tt] == op.target_term:
                    tgt_table = tt
                    break
            _ = src_tables, tgt_tables

        data_path = compute_data_layer_path(
            database_name, schema_name, src_table, tgt_table
        )
        sem_path = compute_semantic_layer_path(op.source_term, op.target_term)

        if data_path:
            op.path_data_layer = data_path
            enriched += 1
        if sem_path:
            op.path_semantic_layer = sem_path
            enriched += 1

        if op.relation_kind == "relates_to" and op.provenance.derivation in (
            "declared_fk",
            "implicit_id_pattern",
            "sql_join_inferred",
        ):
            op.relation_kind = "role"

    logger.info("Phase 3: enriched %d relationship paths", enriched)
    return enriched


def create_role_edge(
    source_term: str,
    target_term: str,
    role_name: str,
    source_table: str,
    target_table: str,
    derivation: str,
    database_name: str,
    schema_name: str,
) -> ObjectProperty:
    """Build a ROLE edge with both path layers attached."""
    op = ObjectProperty(
        name=role_name,
        source_term=source_term,
        target_term=target_term,
        relation_kind="role",
        provenance=Provenance(
            source_table=source_table,
            target_table=target_table,
            derivation=derivation,  # type: ignore[arg-type]
        ),
    )
    op.path_data_layer = compute_data_layer_path(
        database_name, schema_name, source_table, target_table
    )
    op.path_semantic_layer = compute_semantic_layer_path(source_term, target_term)
    return op
