"""Finalize phase: ROLE / relates_to edges after BFS subtree traversal."""

from __future__ import annotations

import logging
from typing import Any

from gsf.semantic import neo4j_dal
from gsf.semantic.models import BusinessQuestionItem
from gsf.semantic.paths import compute_join_path
from gsf.semantic.role_resolver import find_semantic_role_path, resolve_single_hop_join

logger = logging.getLogger(__name__)


def visit_finalize(table_id: str) -> int:
    """Connect ROLE edges for FK-linked Term pairs after BFS expansion."""
    written = _write_fk_role_edges(table_id)
    if written:
        logger.info("Finalize table_id=%s: %d ROLE edge(s)", table_id, written)
    return written


def resolve_question_roles_two_phase(
    table_id: str,
    src_table_name: str,
    role_intents: list[tuple[str, BusinessQuestionItem]],
    *,
    src_table: dict[str, Any],
    src_ctx: dict[str, Any],
    suggested_fk_names: set[str],
) -> int:
    """Two-phase ROLE edge creation from business-question intents.

    Phase 1 — single-hop: for each (anchor_term, item) intent, try a
    deterministic FK/JOIN graph path first; if absent, ask the LLM to infer
    a join from column annotations and suggested foreign keys.

    Phase 2 — multi-hop semantic: for any intents still unresolved after
    Phase 1, search for the shortest path through already-created ROLE edges
    and concatenate their join_paths.
    """
    seen: set[tuple[str, str, str]] = set()
    unique: list[tuple[str, BusinessQuestionItem]] = []
    for src_term, item in role_intents:
        if not item.entity or not item.role or item.entity == src_term:
            continue
        key = (src_term, item.entity, item.role)
        if key not in seen:
            seen.add(key)
            unique.append((src_term, item))

    tgt_cache: dict[str, dict[str, Any] | None] = {}

    def _tgt(term: str) -> dict[str, Any] | None:
        if term not in tgt_cache:
            tgt_cache[term] = neo4j_dal.get_table_for_term(term)
        return tgt_cache[term]

    src_pk = src_table.get("pk")
    unresolved: list[tuple[str, BusinessQuestionItem]] = []
    written = 0

    # Phase 1: single-hop FK / LLM join
    for src_term, item in unique:
        tgt_table = _tgt(item.entity)
        if not tgt_table:
            logger.debug(
                "Phase-1 skip %s -[%s]-> %s: target Term not in graph yet",
                src_term,
                item.role,
                item.entity,
            )
            unresolved.append((src_term, item))
            continue

        join_path = resolve_single_hop_join(
            table_id,
            src_table_name,
            src_ctx,
            src_pk,
            suggested_fk_names,
            tgt_table,
        )
        if join_path is not None:
            neo4j_dal.merge_role_edge(
                source_term=src_term,
                target_term=item.entity,
                role_name=item.role,
                join_path=join_path,
                source_table=src_table_name,
                target_table=tgt_table["name"],
            )
            written += 1
        else:
            unresolved.append((src_term, item))

    # Phase 2: multi-hop via existing ROLE edges
    for src_term, item in unresolved:
        tgt_table = _tgt(item.entity)
        join_path = find_semantic_role_path(src_term, item.entity)
        if join_path is not None and tgt_table:
            neo4j_dal.merge_role_edge(
                source_term=src_term,
                target_term=item.entity,
                role_name=item.role,
                join_path=join_path,
                source_table=src_table_name,
                target_table=tgt_table["name"],
            )
            written += 1
        else:
            logger.debug(
                "No join path found for %s -[%s]-> %s",
                src_term,
                item.role,
                item.entity,
            )

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

        join_path = compute_join_path(
            pair["source_table_id"],
            pair["target_table_id"],
        )

        neo4j_dal.merge_role_edge(
            source_term=src_term,
            target_term=tgt_term,
            role_name=role_name,
            join_path=join_path,
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
