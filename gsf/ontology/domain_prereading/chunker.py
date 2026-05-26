# SPDX-FileCopyrightText: Copyright (c) 2024-2026, NVIDIA CORPORATION & AFFILIATES.
# All rights reserved.
# SPDX-License-Identifier: Apache-2.0

"""CHUNK: split schema + SQL corpus into overlapping token chunks."""

from __future__ import annotations

import logging
from dataclasses import dataclass, field

import pandas as pd

logger = logging.getLogger(__name__)

TARGET_CHUNK_TOKENS = 2000
OVERLAP_TOKENS = 200
APPROX_CHARS_PER_TOKEN = 4


@dataclass
class Chunk:
    """A single chunk of schema/SQL text ready for the MAP phase."""

    text: str
    source_type: str  # "schema" or "sql"
    tables: list[str] = field(default_factory=list)


def _estimate_tokens(text: str) -> int:
    return len(text) // APPROX_CHARS_PER_TOKEN


def _format_table_block(
    table_schema: str,
    table_name: str,
    columns: pd.DataFrame,
) -> str:
    """Render a single table's schema as a compact text block."""
    fqn = f"{table_schema}.{table_name}"
    lines = [f"TABLE: {fqn}"]
    for _, col in columns.iterrows():
        nullable = "NULL" if col.get("is_nullable") == "YES" else "NOT NULL"
        lines.append(f"  {col['column_name']} {col['data_type']} {nullable}")
    return "\n".join(lines)


def chunk_schema(
    tables_df: pd.DataFrame,
    columns_df: pd.DataFrame,
) -> list[Chunk]:
    """Group tables into chunks of ~TARGET_CHUNK_TOKENS each.

    Adjacent tables (by schema/name sort order) are grouped together so each
    chunk contains semantically related tables where possible.
    """
    chunks: list[Chunk] = []
    current_lines: list[str] = []
    current_tables: list[str] = []
    current_tokens = 0

    for _, tbl in tables_df.iterrows():
        schema = tbl["table_schema"]
        name = tbl["table_name"]
        tbl_cols = columns_df[
            (columns_df["table_schema"] == schema)
            & (columns_df["table_name"] == name)
        ]
        block = _format_table_block(schema, name, tbl_cols)
        block_tokens = _estimate_tokens(block)

        if (
            current_tokens + block_tokens > TARGET_CHUNK_TOKENS
            and current_lines
        ):
            chunks.append(
                Chunk(
                    text="\n\n".join(current_lines),
                    source_type="schema",
                    tables=current_tables.copy(),
                )
            )
            overlap_text = current_lines[-1] if current_lines else ""
            current_lines = [overlap_text] if overlap_text else []
            current_tables = (
                [current_tables[-1]] if current_tables else []
            )
            current_tokens = _estimate_tokens(overlap_text)

        current_lines.append(block)
        current_tables.append(f"{schema}.{name}")
        current_tokens += block_tokens

    if current_lines:
        chunks.append(
            Chunk(
                text="\n\n".join(current_lines),
                source_type="schema",
                tables=current_tables,
            )
        )

    logger.info("Schema chunked into %d chunks", len(chunks))
    return chunks


def chunk_sql_corpus(
    queries_df: pd.DataFrame,
    *,
    max_queries: int = 500,
) -> list[Chunk]:
    """Split SQL queries into chunks of ~TARGET_CHUNK_TOKENS each.

    Takes the top ``max_queries`` queries (assumed pre-sorted by frequency or
    coverage). Each chunk packs consecutive queries until the token budget.
    """
    queries = queries_df.head(max_queries)["query_text"].dropna().tolist()
    if not queries:
        return []

    chunks: list[Chunk] = []
    current_lines: list[str] = []
    current_tokens = 0

    for i, sql in enumerate(queries):
        sql_block = f"-- Query {i + 1}\n{sql.strip()}"
        block_tokens = _estimate_tokens(sql_block)

        if current_tokens + block_tokens > TARGET_CHUNK_TOKENS and current_lines:
            chunks.append(
                Chunk(
                    text="\n\n".join(current_lines),
                    source_type="sql",
                )
            )
            overlap_line = current_lines[-1] if current_lines else ""
            current_lines = [overlap_line] if overlap_line else []
            current_tokens = _estimate_tokens(overlap_line)

        current_lines.append(sql_block)
        current_tokens += block_tokens

    if current_lines:
        chunks.append(
            Chunk(
                text="\n\n".join(current_lines),
                source_type="sql",
            )
        )

    logger.info("SQL corpus chunked into %d chunks", len(chunks))
    return chunks
