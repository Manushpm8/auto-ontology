# SPDX-FileCopyrightText: Copyright (c) 2024-2026, NVIDIA CORPORATION & AFFILIATES.
# All rights reserved.
# SPDX-License-Identifier: Apache-2.0

"""Ontology construction runner.

Executes all phases of ontology construction in sequence:
  Phase 0: Domain Pre-Reading (map-reduce summarization)
  Phase 1: (future) Attribute extraction per table
  ...

Usage::

    uv run python -m gsf.ontology

Environment:
    CONNECTION_STRINGS  — comma-separated Postgres URIs (first used)
    NVIDIA_API_KEY      — required for LLM calls
    BASE_URL            — NIM endpoint (default: https://integrate.api.nvidia.com/v1)
    MODEL_NAME          — chat model (default: nvidia/nemotron-3-nano-30b-a3b)
"""

from __future__ import annotations

import json
import logging
import os
import sys
from pathlib import Path

from gsf.connectors.postgres import PostgresDatabase
from gsf.ontology.domain_prereading import DomainSummary, run_domain_prereading


def _load_env() -> None:
    """Load .env files the same way the server does."""
    from dotenv import load_dotenv

    repo_root = Path(__file__).resolve().parent.parent.parent
    load_dotenv(repo_root / ".env", override=True)
    gsf_dir = repo_root / "gsf"
    load_dotenv(gsf_dir / ".env", override=True)


def _get_connection_string() -> str:
    raw = os.environ.get("CONNECTION_STRINGS", "")
    if not raw:
        print(
            "ERROR: CONNECTION_STRINGS is not set. "
            "Add it to your .env, e.g.:\n"
            "  CONNECTION_STRINGS=postgresql://user:pass@host:5432/dbname",
            file=sys.stderr,
        )
        sys.exit(1)
    return raw.split(",")[0]


def _save_summary(summary: DomainSummary, output_path: Path) -> None:
    output_path.parent.mkdir(parents=True, exist_ok=True)
    output_path.write_text(
        json.dumps(summary.model_dump(), indent=2, ensure_ascii=False) + "\n"
    )


def main() -> None:
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s %(levelname)s %(name)s: %(message)s",
    )
    logger = logging.getLogger("gsf.ontology")

    _load_env()
    connection_string = _get_connection_string()

    api_key = os.environ.get("NVIDIA_API_KEY", "")
    if not api_key:
        logger.error("NVIDIA_API_KEY is not set — LLM calls will fail")
        sys.exit(1)

    # --- Phase 0: Domain Pre-Reading ---
    logger.info("=" * 60)
    logger.info("PHASE 0 — Domain Pre-Reading")
    logger.info("=" * 60)

    connector = PostgresDatabase(connection_string)
    try:
        summary = run_domain_prereading(connector)
    finally:
        connector.close()

    output_dir = Path(__file__).resolve().parent / "output"
    output_path = output_dir / "domain_summary.json"
    _save_summary(summary, output_path)

    logger.info("Domain Summary saved to %s", output_path)
    logger.info("  domain: %s", summary.domain)
    logger.info("  entities: %s", summary.core_entities)
    logger.info("  metrics: %s", summary.core_metrics)
    logger.info("  rules: %d", len(summary.business_rules))
    logger.info("  glossary hints: %d", len(summary.glossary_hints))
    logger.info("  join patterns: %d", len(summary.dominant_join_patterns))


if __name__ == "__main__":
    main()
