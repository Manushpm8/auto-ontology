"""Finalize phase: ROLE / relates_to edges after BFS subtree traversal."""

from __future__ import annotations

import logging

from gsf.semantic import neo4j_dal
from gsf.semantic.models import BusinessQuestionItem
from gsf.semantic.paths import compute_data_layer_path, compute_semantic_layer_path

logger = logging.getLogger(__name__)


def visit_finalize(table_id: str) -> int:
    """Connect ROLE edges for FK-linked Term pairs after BFS expansion."""
    written = _write_fk_role_edges(table_id)
    if written:
        logger.info("Finalize table_id=%s: %d ROLE edge(s)", table_id, written)
    return written


def write_question_role_edges(
    table_id: str,
    src_term: str,
    src_table_name: str,
    role_intents: list[BusinessQuestionItem],
) -> int:
    """ROLE edges from anchor Term to entities named in business questions."""
    written = 0
    seen: set[tuple[str, str]] = set()
    for item in role_intents:
        tgt_term = item.entity
        role_name = item.role
        if not tgt_term or not role_name or tgt_term == src_term:
            continue
        key = (tgt_term, role_name)
        if key in seen:
            continue
        seen.add(key)

        tgt_table = neo4j_dal.get_table_for_term(tgt_term)
        if not tgt_table:
            logger.debug(
                "Skip question ROLE %s -[%s]-> %s: target Term not mapped",
                src_term,
                role_name,
                tgt_term,
            )
            continue

        data_path = compute_data_layer_path(table_id, tgt_table["id"])
        sem_path = compute_semantic_layer_path(src_term, tgt_term)
        neo4j_dal.merge_role_edge(
            source_term=src_term,
            target_term=tgt_term,
            role_name=role_name,
            path_data_layer=data_path,
            path_semantic_layer=sem_path,
            source_table=src_table_name,
            target_table=tgt_table["name"],
        )
        written += 1
    return written


def _write_fk_role_edges(table_id: str) -> int:
    pairs = neo4j_dal.fetch_fk_role_pairs(table_id)
    written = 0
    for pair in pairs:
        src_table = pair["source_table"]
        tgt_table = pair["target_table"]
        src_term = pair["source_term"]
        tgt_term = pair["target_term"]
        role_name = _role_name_from_column(pair.get("source_column", ""))

        data_path = compute_data_layer_path(
            pair["source_table_id"],
            pair["target_table_id"],
        )
        sem_path = compute_semantic_layer_path(src_term, tgt_term)

        neo4j_dal.merge_role_edge(
            source_term=src_term,
            target_term=tgt_term,
            role_name=role_name,
            path_data_layer=data_path,
            path_semantic_layer=sem_path,
            source_table=src_table,
            target_table=tgt_table,
        )
        written += 1
    return written


def _role_name_from_column(column_name: str) -> str:
    if column_name.endswith("_id"):
        base = column_name[:-3]
        parts = base.split("_")
        return parts[0].lower() + "".join(p.capitalize() for p in parts[1:])
    return "relatesTo"
