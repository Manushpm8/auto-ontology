"""Semantic compilation entry: build VisitContext, pick seeds, drive recursion."""

from __future__ import annotations

import logging

from gsf.semantic import neo4j_dal
from gsf.semantic.domain import DomainSummary, load_domain_summary
from gsf.semantic.embed import SemanticEmbedder
from gsf.semantic.loaders import (
    fetch_sorted_tables,
    fetch_table_by_id,
    fetch_table_context,
)
from gsf.semantic.orphan import pick_orphan_seed
from gsf.semantic.seed import select_seed_table
from gsf.semantic.visit_enter import VisitContext, build_data_retriever, visit_enter
from gsf.semantic.visit_finalize import finalize_all_roles

logger = logging.getLogger(__name__)


def compile_semantic_layer(
    database_name: str,
    *,
    resume: bool = True,
    domain_summary: DomainSummary | None = None,
    embedder: SemanticEmbedder | None = None,
) -> int:
    """Run full semantic compilation over the entire Neo4j graph."""
    summary = domain_summary or load_domain_summary(database_name)

    if not resume:
        neo4j_dal.clear_reviewed_flags()
        logger.info("Cleared reviewed flags for fresh run")

    vctx = VisitContext(
        retriever=build_data_retriever(database_name),
        embedder=embedder,
        domain_summary=summary,
    )

    count = 0
    tree_index = 0
    first_tree = True

    while True:
        unreviewed = neo4j_dal.discover_unreviewed_tables()
        if not unreviewed:
            logger.info("All tables reviewed — compilation complete")
            break

        seed = _select_next_seed(unreviewed, summary=summary, first_tree=first_tree)
        if seed is None:
            logger.error("No seed candidate among %d unreviewed", len(unreviewed))
            break
        first_tree = False

        seed_ctx = fetch_table_context(seed["id"])
        if seed_ctx.get("reviewed"):
            logger.debug(
                "Seed %s already reviewed — marking and skipping", seed["name"]
            )
            neo4j_dal.mark_table_reviewed(seed["id"])
            continue
        if not seed_ctx.get("columns"):
            logger.warning("Seed %s has no columns — marking reviewed", seed["name"])
            neo4j_dal.mark_table_reviewed(seed["id"])
            continue

        logger.info("=" * 40)
        logger.info(
            "Tree %d seed=%s (%d unreviewed)",
            tree_index + 1,
            seed["name"],
            len(unreviewed),
        )
        logger.info("=" * 40)

        visit_enter(seed, seed_ctx, vctx=vctx, hop=0)
        count += 1
        tree_index += 1

    role_count = finalize_all_roles(vctx)
    logger.info("Post-DFS: %d total ROLE edge(s) written", role_count)

    return count


def _select_next_seed(
    unreviewed: list[dict[str, object]],
    *,
    summary: DomainSummary | None,
    first_tree: bool,
):
    """LLM-pick the first seed; for subsequent trees, prefer orphan tables."""
    if first_tree and summary:
        catalog = fetch_sorted_tables()
        unreviewed_ids = {t["id"] for t in unreviewed}
        candidates = [t for t in catalog if t["id"] in unreviewed_ids]
        if not candidates:
            return None
        return select_seed_table(candidates, summary)

    orphan = pick_orphan_seed()
    if orphan is not None:
        return orphan
    return fetch_table_by_id(unreviewed[0]["id"])
