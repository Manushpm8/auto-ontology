"""Unified entry point for semantic layer compilation."""

from __future__ import annotations

import logging

from gsf.semantic.domain import DomainSummary, load_domain_summary
from gsf.semantic.pipeline import compile_semantic_layer

logger = logging.getLogger(__name__)


def run_semantic_compilation(
    database_name: str,
    *,
    embed: bool = True,
    resume: bool = True,
    domain_summary: DomainSummary | None = None,
) -> int:
    """Compile semantic taxonomy then embed all nodes into the VDB.

    Returns the number of tables processed.
    """
    summary = domain_summary or load_domain_summary(database_name)

    logger.info("=" * 60)
    logger.info(
        "Semantic compilation — full Neo4j graph (database=%r)",
        database_name,
    )
    logger.info("=" * 60)

    count = compile_semantic_layer(
        database_name,
        resume=resume,
        domain_summary=summary,
    )

    logger.info("=" * 60)
    logger.info("Semantic compilation finished — %d table visits", count)
    logger.info("=" * 60)

    if embed:
        from gsf.semantic.embed import build_semantic_embedder, embed_all_semantic_nodes

        embedder = build_semantic_embedder(database_name, reset=not resume)
        if embedder is not None:
            logger.info("=" * 60)
            logger.info("Embedding semantic nodes into VDB…")
            logger.info("=" * 60)
            vdb_rows = embed_all_semantic_nodes(embedder)
            logger.info("Embedding complete — %d VDB row(s) written", vdb_rows)

    return count
