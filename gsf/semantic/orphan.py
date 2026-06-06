"""Phase 4: deterministic orphan stitch — no LLM."""

from __future__ import annotations

import logging

from gsf.semantic import neo4j_dal
from gsf.semantic.deterministic import column_attribute_specs, to_term_name
from gsf.semantic.loaders import fetch_join_edges, fetch_table_context

logger = logging.getLogger(__name__)


def orphan_stitch() -> int:
    """Bind tables without a Term to the nearest mapped FK/JOIN neighbor."""
    orphans = neo4j_dal.list_orphan_tables()
    if not orphans:
        return 0

    join_edges = fetch_join_edges()
    bound = 0

    for orphan in orphans:
        orphan_id = orphan["id"]
        orphan_name = orphan["name"]
        ctx = fetch_table_context(orphan_id)

        neighbor_table: str | None = None
        neighbor_term: str | None = None

        for fk in ctx.get("fks", []):
            tgt_id = fk.get("target_table_id")
            if not tgt_id:
                continue
            term = neo4j_dal.get_term_for_table(tgt_id)
            if term:
                neighbor_table = fk.get("target_table")
                neighbor_term = term
                break

        if not neighbor_term:
            for edge in join_edges:
                if edge["source_table_id"] == orphan_id:
                    other_id = edge["target_table_id"]
                    other_name = edge["target_table"]
                elif edge["target_table_id"] == orphan_id:
                    other_id = edge["source_table_id"]
                    other_name = edge["source_table"]
                else:
                    continue
                term = neo4j_dal.get_term_for_table(other_id)
                if term:
                    neighbor_table = other_name
                    neighbor_term = term
                    break

        orphan_term = to_term_name(orphan_name)
        neo4j_dal.merge_term(
            orphan_term,
            f"Orphan fallback from table {orphan_name}",
            orphan_id,
        )

        if neighbor_term:
            neo4j_dal.merge_part_of(orphan_term, neighbor_term)

        specs = column_attribute_specs(ctx.get("columns", []), ctx.get("fks", []))
        for spec in specs:
            neo4j_dal.merge_column_attribute(
                term_name=orphan_term,
                table_id=orphan_id,
                source_column=spec.source_column,
                attr_name=spec.name,
                datatype=spec.datatype,
                description=spec.description,
            )

        neo4j_dal.mark_table_reviewed(orphan_id)
        bound += 1
        logger.info(
            "Orphan %s → Term %r (neighbor=%s)",
            orphan_name,
            orphan_term,
            neighbor_table,
        )

    logger.info("Orphan stitch bound %d table(s)", bound)
    return bound
