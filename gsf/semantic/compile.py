"""Unified entry point for semantic layer compilation."""

from __future__ import annotations

import logging
import os

from nemo_retriever.params import EmbedParams

from gsf.semantic.domain import DomainSummary, load_domain_summary
from gsf.semantic.embed import embed_semantic_layer
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

    count = compile_semantic_layer(
        database_name,
        resume=resume,
        domain_summary=summary,
    )

    if embed:
        embed_params = EmbedParams(
            embed_invoke_url=os.environ.get(
                "EMBED_ENDPOINT", "https://integrate.api.nvidia.com/v1"
            ),
            model_name=os.environ.get(
                "EMBED_MODEL", "nvidia/llama-nemotron-embed-1b-v2"
            ),
            api_key=os.environ.get("NVIDIA_API_KEY", ""),
            embed_modality="text",
        )
        embed_semantic_layer(database_name, embed_params=embed_params)

    logger.info("=" * 60)
    logger.info("Semantic compilation finished — %d table visits", count)
    logger.info("=" * 60)

    return count
