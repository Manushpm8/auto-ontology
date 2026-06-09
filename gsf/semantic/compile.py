"""Unified entry point for semantic layer compilation."""

from __future__ import annotations

import logging

from gsf.semantic.domain import DomainSummary, load_domain_summary
from gsf.semantic.embed import build_semantic_embedder
from gsf.semantic.pipeline import compile_semantic_layer

logger = logging.getLogger(__name__)


def run_semantic_compilation(
    database_name: str,
    *,
    embed: bool = True,
    resume: bool = True,
    domain_summary: DomainSummary | None = None,
) -> int:
    """Compile semantic layer over the full Neo4j graph. Returns tables processed."""
    summary = domain_summary or load_domain_summary(database_name)

    logger.info("=" * 60)
    logger.info(
        "Semantic compilation — full Neo4j graph (VDB namespace=%r)",
        database_name,
    )
    logger.info("=" * 60)

    embedder = (
        build_semantic_embedder(database_name, reset=not resume) if embed else None
    )

    count = compile_semantic_layer(
        database_name,
        resume=resume,
        domain_summary=summary,
        embedder=embedder,
    )

    logger.info("=" * 60)
    logger.info("Semantic compilation finished — %d table visits", count)
    logger.info("=" * 60)

    return count
