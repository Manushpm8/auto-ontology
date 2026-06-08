"""Phases 1–5 orchestration: seed → BFS expand → finalize → orphan → coverage sweep."""

from __future__ import annotations

import logging
from typing import Any

from gsf.semantic import neo4j_dal
from gsf.semantic.domain import DomainSummary, load_domain_summary
from gsf.semantic.loaders import (
    build_tables_index,
    fetch_join_edges,
    fetch_table_context,
)
from gsf.semantic.orphan import orphan_stitch
from gsf.semantic.queue import TablesQueue
from gsf.semantic.seed import select_seed_table
from gsf.semantic.visit_enter import build_data_retriever, visit_enter
from gsf.semantic.visit_finalize import visit_finalize

logger = logging.getLogger(__name__)


def run_bfs_tree(
    seed: dict[str, Any],
    *,
    tables_by_name: dict[str, dict[str, Any]],
    tables_by_id: dict[str, dict[str, Any]],
    join_edges: list[dict[str, Any]],
    domain_summary: DomainSummary | None,
    retriever: Any,
    tree_index: int,
) -> list[str]:
    """Expand queue from seed, then finalize in reverse visit order."""
    queue = TablesQueue(tables_by_name, join_edges)
    queue.push_seed(seed["name"])

    visit_order: list[str] = []

    while True:
        entry = queue.pop()
        if entry is None:
            break

        table = tables_by_id.get(entry.table_id) or tables_by_name.get(entry.table_name)
        if not table:
            continue

        ctx = fetch_table_context(table["id"])
        if ctx.get("reviewed"):
            logger.debug("Skip %s — already reviewed", table["name"])
            continue
        if not ctx.get("columns"):
            logger.warning("Table %s has no columns — skipping", table["name"])
            neo4j_dal.mark_table_reviewed(table["id"])
            continue

        visit_enter(
            table,
            ctx,
            queue=queue,
            hop=entry.hop,
            retriever=retriever,
            domain_summary=domain_summary,
        )
        visit_order.append(table["id"])

    logger.info(
        "BFS tree %d complete — %d tables, starting finalize pass",
        tree_index + 1,
        len(visit_order),
    )

    for table_id in reversed(visit_order):
        visit_finalize(table_id)

    orphan_stitch()

    return visit_order


def compile_semantic_layer(
    database_name: str,
    *,
    resume: bool = True,
    domain_summary: DomainSummary | None = None,
) -> int:
    """Run full semantic compilation over the entire Neo4j graph."""
    summary = domain_summary or load_domain_summary(database_name)
    tables, tables_by_name = build_tables_index()
    tables_by_id = {t["id"]: t for t in tables}
    if not tables:
        logger.warning("No tables in Neo4j graph")
        return 0

    logger.info("Catalog: %d table(s) across Neo4j graph", len(tables))

    if not resume:
        neo4j_dal.clear_reviewed_flags()
        logger.info("Cleared reviewed flags for fresh run")

    join_edges = fetch_join_edges()
    retriever = build_data_retriever(database_name)

    tree_index = 0
    total_processed = 0
    first_tree = True

    while True:
        unreviewed = neo4j_dal.discover_unreviewed_tables()
        if not unreviewed:
            logger.info("All tables reviewed — compilation complete")
            break

        if first_tree and summary:
            seed = select_seed_table(
                [tables_by_id[t["id"]] for t in unreviewed if t["id"] in tables_by_id]
                or tables,
                summary,
            )
            first_tree = False
        else:
            seed = tables_by_id.get(unreviewed[0]["id"])
            if not seed:
                logger.error("Unreviewed table %r not in catalog", unreviewed[0]["id"])
                break

        logger.info("=" * 40)
        logger.info(
            "BFS tree %d seed=%s (%d unreviewed)",
            tree_index + 1,
            seed["name"],
            len(unreviewed),
        )
        logger.info("=" * 40)

        visit_order = run_bfs_tree(
            seed,
            tables_by_name=tables_by_name,
            tables_by_id=tables_by_id,
            join_edges=join_edges,
            domain_summary=summary,
            retriever=retriever,
            tree_index=tree_index,
        )
        total_processed += len(visit_order)
        tree_index += 1

        if not visit_order:
            logger.error("BFS tree produced no progress — stopping")
            break

    return total_processed
