# SPDX-FileCopyrightText: Copyright (c) 2024-2026, NVIDIA CORPORATION & AFFILIATES.
# All rights reserved.
# SPDX-License-Identifier: Apache-2.0

"""Phase 0 — Domain Pre-Reading: top-level pipeline entry point.

Orchestrates the full map-reduce summarization pass:
  1. Gather schema metadata + SQL corpus from the Neo4j graph.
  2. CHUNK into overlapping ~2K-token segments.
  3. MAP each chunk (parallel LLM calls) to extract domain signals.
  4. REDUCE all signals into a single DomainSummary.
"""

from __future__ import annotations

import logging

import pandas as pd

from gsf.ontology.domain_prereading.chunker import Chunk, chunk_schema, chunk_sql_corpus
from gsf.ontology.domain_prereading.map_reduce import iterative_build, summarize_graph
from gsf.ontology.domain_prereading.models import DomainSummary

logger = logging.getLogger(__name__)


def _gather_corpus() -> list[Chunk]:
    """Collect schema + SQL chunks from the Neo4j graph."""
    from nemo_retriever.tabular_data.ingestion.dal.queries_dal import (
        load_sqls_to_tables,
    )
    from nemo_retriever.tabular_data.ingestion.model.reserved_words import Edges, Labels
    from nemo_retriever.tabular_data.neo4j import get_neo4j_conn

    logger.info("Gathering schema metadata from Neo4j graph...")
    query = f"""
    MATCH (db:{Labels.DB})-[:{Edges.CONTAINS}]->(schema:{Labels.SCHEMA})
          -[:{Edges.CONTAINS}]->(table:{Labels.TABLE})
          -[:{Edges.CONTAINS}]->(column:{Labels.COLUMN})
    RETURN schema.name   AS table_schema,
           table.name    AS table_name,
           column.name   AS column_name,
           column.data_type AS data_type,
           'YES'         AS is_nullable,
           column.ordinal_position AS ordinal_position
    ORDER BY schema.name, table.name, column.ordinal_position
    """
    rows = get_neo4j_conn().query_read(query)
    if not rows:
        logger.warning("No schema data found in Neo4j graph")
        return []

    columns_df = pd.DataFrame(rows)
    tables_df = (
        columns_df[["table_schema", "table_name"]]
        .drop_duplicates()
        .sort_values(["table_schema", "table_name"])
        .reset_index(drop=True)
    )
    schema_chunks = chunk_schema(tables_df, columns_df)

    logger.info("Gathering SQL query corpus from Neo4j graph...")
    sqls_df = load_sqls_to_tables()
    if sqls_df.empty or "sql_full_query" not in sqls_df.columns:
        sql_chunks: list[Chunk] = []
    else:
        queries_df = sqls_df[["sql_full_query"]].rename(
            columns={"sql_full_query": "query_text"}
        )
        queries_df = queries_df.dropna(subset=["query_text"])
        sql_chunks = chunk_sql_corpus(queries_df, max_queries=500)

    all_chunks = schema_chunks + sql_chunks
    logger.info(
        "Corpus: %d schema chunks + %d SQL chunks = %d total",
        len(schema_chunks),
        len(sql_chunks),
        len(all_chunks),
    )
    return all_chunks


def run_domain_prereading() -> DomainSummary:
    """Execute the full Phase 0 pipeline and return a DomainSummary.

    Reads schema and SQL data from the Neo4j graph (populated by a prior
    ingest run).

    Returns
    -------
    DomainSummary
        A compact (~500-1500 token) summary of the business domain.
    """
    chunks = _gather_corpus()

    if not chunks:
        logger.warning("No chunks produced — returning empty DomainSummary")
        return DomainSummary(domains=["unknown"])

    logger.info("Starting iterative knowledge graph build (%d chunks)...", len(chunks))
    knowledge_graph = iterative_build(chunks)

    logger.info("Summarizing knowledge graph into DomainSummary...")
    summary = summarize_graph(knowledge_graph)

    logger.info("Domain Pre-Reading complete: %s", summary.domains)
    return summary
