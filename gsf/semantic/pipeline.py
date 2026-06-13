"""Semantic compilation entry: flat table-by-table taxonomy pass."""

from __future__ import annotations

import logging

from gsf.semantic import neo4j_dal
from gsf.semantic.domain import DomainSummary, load_domain_summary
from gsf.semantic.loaders import fetch_table_context
from gsf.semantic.visit_enter import process_table

logger = logging.getLogger(__name__)


def compile_semantic_layer(
    database_name: str,
    *,
    domain_summary: DomainSummary | None = None,
) -> int:
    """Run full taxonomy compilation over every unreviewed table in Neo4j."""
    summary = domain_summary or load_domain_summary(database_name)

    count = 0

    while True:
        unreviewed = neo4j_dal.discover_unreviewed_tables()
        if not unreviewed:
            logger.info(
                "All tables reviewed — compilation complete (%d processed)", count
            )
            break

        table = unreviewed[0]
        table_id = table["id"]
        table_name = table["name"]

        ctx = fetch_table_context(table_id)

        if not ctx.get("columns"):
            logger.warning("Table %s has no columns — marking reviewed", table_name)
            neo4j_dal.mark_table_reviewed(table_id)
            continue

        logger.info(
            "[%d unreviewed] Processing table: %s",
            len(unreviewed),
            table_name,
        )

        try:
            process_table(table, ctx, domain_summary=summary)
        except Exception:
            logger.exception("Unexpected error processing table %s", table_name)

        neo4j_dal.mark_table_reviewed(table_id)
        count += 1

    return count
