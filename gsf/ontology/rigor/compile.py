"""Unified entry point for semantic layer compilation."""

from __future__ import annotations

import logging
from pathlib import Path

from gsf.ontology.domain_prereading.models import DomainSummary
from gsf.ontology.rigor.embed import embed_ontology
from gsf.ontology.rigor.models import CoreOntology
from gsf.ontology.rigor.pipeline import build_ontology

logger = logging.getLogger(__name__)

_DOMAIN_SUMMARY_DIR = Path(".semantic_summaries")


def _load_domain_summary(database_name: str) -> DomainSummary | None:
    path = _DOMAIN_SUMMARY_DIR / f"{database_name}.json"
    if not path.exists():
        return None
    try:
        return DomainSummary.model_validate_json(path.read_text())
    except Exception:
        logger.warning("Failed to load domain summary from %s", path)
        return None


def _save_domain_summary(database_name: str, summary: DomainSummary) -> None:
    _DOMAIN_SUMMARY_DIR.mkdir(parents=True, exist_ok=True)
    path = _DOMAIN_SUMMARY_DIR / f"{database_name}.json"
    path.write_text(summary.model_dump_json(indent=2) + "\n")


def run_semantic_compilation(
    database_name: str,
    *,
    skip_threshold: int = 0,
    schema_name: str | None = None,
    write_to_neo4j: bool = True,
    embed: bool = True,
    resume: bool = True,
    domain_summary: DomainSummary | None = None,
    run_domain_prereading: bool = True,
) -> CoreOntology:
    """Run the full semantic compilation pipeline for a database."""
    summary = domain_summary or _load_domain_summary(database_name)
    if summary is None and run_domain_prereading:
        from gsf.ontology.domain_prereading import run_domain_prereading

        logger.info("Running domain pre-reading for %r", database_name)
        summary = run_domain_prereading()
        _save_domain_summary(database_name, summary)

    ontology = build_ontology(
        database_name=database_name,
        skip_threshold=skip_threshold,
        write_to_neo4j=write_to_neo4j,
        resume=resume,
        schema_name=schema_name,
        domain_summary=summary,
    )

    if embed and write_to_neo4j:
        embed_ontology(
            ontology,
            database_name=database_name,
            schema_name=schema_name or database_name,
        )

    return ontology
