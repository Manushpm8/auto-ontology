"""Finalize phase: ROLE / relates_to edges after BFS subtree traversal."""

from __future__ import annotations

import logging
from collections import defaultdict
from typing import TYPE_CHECKING, Any

from gsf.semantic import neo4j_dal
from gsf.semantic.loaders import fetch_table_by_name
from gsf.semantic.models import BusinessQuestionItem
from gsf.semantic.paths import compute_join_path
from gsf.semantic.role_resolver import find_semantic_role_path, resolve_single_hop_join

if TYPE_CHECKING:
    from gsf.semantic.visit_enter import VisitContext

logger = logging.getLogger(__name__)


def _dedup_intents(
    role_intents: list[tuple[str, BusinessQuestionItem]],
) -> list[tuple[str, BusinessQuestionItem]]:
    """Return intents with invalid / duplicate (src, entity, role) triples removed."""
    seen: set[tuple[str, str, str]] = set()
    unique: list[tuple[str, BusinessQuestionItem]] = []
    for src_term, item in role_intents:
        if not item.entity or not item.role or item.entity == src_term:
            continue
        key = (src_term, item.entity, item.role)
        if key not in seen:
            seen.add(key)
            unique.append((src_term, item))
    return unique


def resolve_single_hop_role_intents(
    table_id: str,
    src_table_name: str,
    role_intents: list[tuple[str, BusinessQuestionItem]],
) -> tuple[int, list[tuple[str, BusinessQuestionItem]]]:
    """Attempt a single-hop FK/LLM join for each intent.

    Tries a deterministic FK/JOIN graph path first; falls back to LLM inference
    using column metadata fetched directly from Neo4j.  Returns
    (written_count, unresolved_intents) where unresolved intents are those for
    which no direct join path could be found.
    """
    tgt_cache: dict[str, dict[str, Any] | None] = {}

    def _tgt(term: str) -> dict[str, Any] | None:
        if term not in tgt_cache:
            tgt_cache[term] = neo4j_dal.get_table_for_term(term)
        return tgt_cache[term]

    unresolved: list[tuple[str, BusinessQuestionItem]] = []
    written = 0

    for src_term, item in _dedup_intents(role_intents):
        tgt_table = _tgt(item.entity)
        if not tgt_table:
            logger.debug(
                "Single-hop skip %s -[%s]-> %s: target Term not in graph yet",
                src_term,
                item.role,
                item.entity,
            )
            unresolved.append((src_term, item))
            continue

        join_path = resolve_single_hop_join(
            table_id,
            src_table_name,
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

    return written, unresolved


def resolve_question_roles_two_phase(
    table_id: str,
    src_table_name: str,
    role_intents: list[tuple[str, BusinessQuestionItem]],
) -> int:
    """Create ROLE edges for a set of business-question intents.

    First attempts single-hop FK/LLM join resolution for each intent.  For
    intents where no direct join is found, searches the shortest path through
    already-created ROLE edges to derive a multi-hop join.

    Returns the total number of ROLE edges written.
    """
    written, unresolved = resolve_single_hop_role_intents(
        table_id,
        src_table_name,
        role_intents,
    )

    tgt_cache: dict[str, dict[str, Any] | None] = {}

    def _tgt(term: str) -> dict[str, Any] | None:
        if term not in tgt_cache:
            tgt_cache[term] = neo4j_dal.get_table_for_term(term)
        return tgt_cache[term]

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
                "No ROLE path found for %s -[%s]-> %s",
                src_term,
                item.role,
                item.entity,
            )

    return written


def finalize_all_roles(vctx: VisitContext) -> int:
    """Create all deferred ROLE edges after the full DFS has completed.

    Single-hop resolution runs first for every table that has known FK columns,
    ensuring direct join paths are in place before semantic path search begins.
    Semantic path search then runs for all accumulated business-question intents,
    traversing the ROLE graph to derive multi-hop connections.

    Returns total number of ROLE edges written.
    """
    # Group FK column names by table_name.
    fk_names_by_table: dict[str, set[str]] = defaultdict(set)
    for table_name, fk in vctx.pending_fk_entries:
        fk_names_by_table[table_name].add(fk.column_name)

    # For each table with known FK columns, resolve a ROLE edge for every
    # FK-linked term pair whose join column is among the accumulated FK names.
    written = 0
    for table_name, fk_col_names in fk_names_by_table.items():
        table = fetch_table_by_name(table_name)
        if not table:
            continue
        table_id = table["id"]

        for pair in neo4j_dal.fetch_fk_role_pairs(table_id):
            if pair.get("source_column") not in fk_col_names:
                continue
            tgt_table = {"id": pair["target_table_id"], "name": pair["target_table"]}
            join_path = resolve_single_hop_join(
                table_id,
                table_name,
                tgt_table,
            )
            if join_path is not None:
                neo4j_dal.merge_role_edge(
                    source_term=pair["source_term"],
                    target_term=pair["target_term"],
                    role_name=_role_name_from_column(pair.get("source_column", "")),
                    join_path=join_path,
                    source_table=table_name,
                    target_table=pair["target_table"],
                )
                written += 1
            else:
                logger.debug(
                    "No join path for FK %s.%s → %s",
                    table_name,
                    pair.get("source_column"),
                    pair["target_table"],
                )

    logger.info("finalize_all_roles single-hop: %d ROLE edge(s)", written)

    # Group pending question-role intents by (table_id, table_name) so each
    # source table goes through the full two-phase resolution: single-hop
    # FK/LLM first (which also handles multi-hop FK graph paths via
    # compute_join_path), then semantic ROLE-graph search for the remainder.
    intents_by_table: dict[
        tuple[str, str], list[tuple[str, BusinessQuestionItem]]
    ] = defaultdict(list)
    for table_id, table_name, anchor_term, item in vctx.pending_question_roles:
        intents_by_table[(table_id, table_name)].append((anchor_term, item))

    semantic_written = 0
    for (tbl_id, tbl_name), intents in intents_by_table.items():
        q_written = resolve_question_roles_two_phase(tbl_id, tbl_name, intents)
        semantic_written += q_written

    logger.info("finalize_all_roles semantic: %d ROLE edge(s)", semantic_written)
    written += semantic_written
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
