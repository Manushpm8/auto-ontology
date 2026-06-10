"""Finalize phase: ROLE / relates_to edges after BFS subtree traversal."""

from __future__ import annotations

import logging
from collections import defaultdict
from typing import TYPE_CHECKING, Any

from gsf.semantic import neo4j_dal
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
    *,
    src_table: dict[str, Any],
    src_ctx: dict[str, Any],
    suggested_fk_names: set[str],
) -> tuple[int, list[tuple[str, BusinessQuestionItem]]]:
    """Attempt a single-hop FK/LLM join for each intent.

    Tries a deterministic FK/JOIN graph path first; falls back to LLM inference
    from column annotations and suggested foreign keys.  Returns
    (written_count, unresolved_intents) where unresolved intents are those for
    which no direct join path could be found.
    """
    src_pk = src_table.get("pk")
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

    return written, unresolved


def resolve_question_roles_two_phase(
    table_id: str,
    src_table_name: str,
    role_intents: list[tuple[str, BusinessQuestionItem]],
    *,
    src_table: dict[str, Any],
    src_ctx: dict[str, Any],
    suggested_fk_names: set[str],
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
        src_table=src_table,
        src_ctx=src_ctx,
        suggested_fk_names=suggested_fk_names,
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
    # Group FK entries by table_name → reconstruct suggested_fk_names per table.
    fk_names_by_table: dict[str, set[str]] = defaultdict(set)
    for table_name, fk in vctx.pending_fk_entries:
        fk_names_by_table[table_name].add(fk.column_name)

    # Group question roles by table_name for fast lookup.
    intents_by_table: dict[
        str,
        list[
            tuple[
                str,
                str,
                str,
                BusinessQuestionItem,
                dict[str, Any],
                dict[str, Any],
                set[str],
            ]
        ],
    ] = defaultdict(list)
    for entry in vctx.pending_question_roles:
        intents_by_table[entry[1]].append(entry)

    written = 0
    for table_name, suggested_fk_names in fk_names_by_table.items():
        table_entries = intents_by_table.get(table_name, [])
        if not table_entries:
            continue
        # All entries for the same table share the same table_id / src_table / src_ctx.
        table_id, _, _, _, src_table, src_ctx, _ = table_entries[0]
        role_intents: list[tuple[str, BusinessQuestionItem]] = [
            (anchor_term, item) for _, _, anchor_term, item, _, _, _ in table_entries
        ]
        phase1_written, _ = resolve_single_hop_role_intents(
            table_id,
            table_name,
            role_intents,
            src_table=src_table,
            src_ctx=src_ctx,
            suggested_fk_names=suggested_fk_names,
        )
        written += phase1_written

    logger.info("finalize_all_roles single-hop: %d ROLE edge(s)", written)

    semantic_written = 0
    tgt_cache: dict[str, dict[str, Any] | None] = {}

    def _tgt(term: str) -> dict[str, Any] | None:
        if term not in tgt_cache:
            tgt_cache[term] = neo4j_dal.get_table_for_term(term)
        return tgt_cache[term]

    seen: set[tuple[str, str, str]] = set()
    for table_id, table_name, anchor_term, item, _, _, _ in vctx.pending_question_roles:
        if not item.entity or not item.role or item.entity == anchor_term:
            continue
        key = (anchor_term, item.entity, item.role)
        if key in seen:
            continue
        seen.add(key)

        tgt_table = _tgt(item.entity)
        join_path = find_semantic_role_path(anchor_term, item.entity)
        if join_path is not None and tgt_table:
            neo4j_dal.merge_role_edge(
                source_term=anchor_term,
                target_term=item.entity,
                role_name=item.role,
                join_path=join_path,
                source_table=table_name,
                target_table=tgt_table["name"],
            )
            semantic_written += 1
        else:
            logger.debug(
                "No semantic path: %s -[%s]-> %s",
                anchor_term,
                item.role,
                item.entity,
            )

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
