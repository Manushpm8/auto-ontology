# SPDX-FileCopyrightText: Copyright (c) 2024-2026, NVIDIA CORPORATION & AFFILIATES.
# All rights reserved.
# SPDX-License-Identifier: Apache-2.0

"""Phase 0 — Domain Pre-Reading: top-level pipeline entry point.

Orchestrates the full map-reduce summarization pass:
  1. Gather schema metadata + SQL corpus from the source database.
  2. CHUNK into overlapping ~2K-token segments.
  3. MAP each chunk (parallel LLM calls) to extract domain signals.
  4. REDUCE all signals into a single DomainSummary.
"""

from __future__ import annotations

import logging

from gsf.connectors.postgres import PostgresDatabase
from gsf.ontology.domain_prereading.chunker import Chunk, chunk_schema, chunk_sql_corpus
from gsf.ontology.domain_prereading.map_reduce import map_phase, reduce_phase
from gsf.ontology.domain_prereading.models import DomainSummary

logger = logging.getLogger(__name__)


def _gather_corpus(connector: PostgresDatabase) -> list[Chunk]:
    """Collect schema + SQL chunks from the database connector."""
    logger.info("Gathering schema metadata...")
    tables_df = connector.get_tables()
    columns_df = connector.get_columns()
    schema_chunks = chunk_schema(tables_df, columns_df)

    logger.info("Gathering SQL query corpus...")
    queries_df = connector.get_queries(hours=720)  # ~30 days of history
    sql_chunks = chunk_sql_corpus(queries_df, max_queries=500)

    all_chunks = schema_chunks + sql_chunks
    logger.info(
        "Corpus: %d schema chunks + %d SQL chunks = %d total",
        len(schema_chunks),
        len(sql_chunks),
        len(all_chunks),
    )
    return all_chunks


def run_domain_prereading(connector: PostgresDatabase) -> DomainSummary:
    """Execute the full Phase 0 pipeline and return a DomainSummary.

    Parameters
    ----------
    connector:
        An active database connector to extract schema and query metadata from.

    Returns
    -------
    DomainSummary
        A compact (~500-1500 token) summary of the business domain.
    """
    chunks = _gather_corpus(connector)

    if not chunks:
        logger.warning("No chunks produced — returning empty DomainSummary")
        return DomainSummary(domain="unknown")

    logger.info("Starting MAP phase (%d chunks)...", len(chunks))
    signals = map_phase(chunks)

    non_empty = [s for s in signals if s.key_entities or s.domain_areas]
    if not non_empty:
        logger.warning("MAP phase produced no signals — returning empty summary")
        return DomainSummary(domain="unknown")

    logger.info("Starting REDUCE phase (%d signal sets)...", len(non_empty))
    summary = reduce_phase(non_empty)

    logger.info("Domain Pre-Reading complete: %s", summary.domain)
    return summary
