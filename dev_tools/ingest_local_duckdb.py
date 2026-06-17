# SPDX-FileCopyrightText: Copyright (c) 2024-2026, NVIDIA CORPORATION & AFFILIATES.
# All rights reserved.
# SPDX-License-Identifier: Apache-2.0

"""Ingest a local DuckDB database into Neo4j graph + pgvector embeddings store.

Uses the DuckDB connector from nemo-retriever (same as debug_ingest.py).
The connector resolves sample_queries.csv from its package-internal
benchmarks/<database_name>/ directory — a symlink from the workspace's
dev_tools/benchmarks/bird/sample_queries.csv must exist there.

Usage::

    uv run python -m dev_tools.ingest_local_duckdb

Environment:
    DUCKDB_PATH     — path to the .duckdb file
    NVIDIA_API_KEY  — required for embedding calls
    EMBED_ENDPOINT  — NIM embedding endpoint (default: build.nvidia.com)
    EMBED_MODEL     — embedding model name
"""

from __future__ import annotations

import logging
import os
from pathlib import Path

from gsf.connectors.duckdb import DuckDBDatabase
from nemo_retriever.graph import Graph
from nemo_retriever.graph.tabular_schema_extract_operator import TabularSchemaExtractOp
from nemo_retriever.graph.tabular_fetch_embeddings_operator import (
    TabularFetchEmbeddingsOp,
)
from nemo_retriever.text_embed.operators import _BatchEmbedActor
from nemo_retriever.vdb import IngestVdbOperator
from nemo_retriever.params import EmbedParams, TabularExtractParams
from gsf.vdb import get_vdb

logger = logging.getLogger("dev_tools.ingest_local_duckdb")

_BENCHMARKS_DIR = Path(__file__).resolve().parent / "benchmarks" / "bird"
_NVIDIA_API_KEY = os.environ.get("NVIDIA_API_KEY", "")
_EMBED_API_KEY = os.environ.get("EMBED_API_KEY", _NVIDIA_API_KEY)
_EMBED_ENDPOINT = os.environ.get(
    "EMBED_ENDPOINT", "https://integrate.api.nvidia.com/v1"
)
_EMBED_MODEL = os.environ.get("EMBED_MODEL", "nvidia/llama-nemotron-embed-1b-v2")
_DUCKDB_PATH = os.environ.get("DUCKDB_PATH", str(_BENCHMARKS_DIR / "bird.duckdb"))

if not _EMBED_API_KEY:
    raise EnvironmentError(
        "Neither EMBED_API_KEY nor NVIDIA_API_KEY is set. "
        "Export one before running:\n\n"
        "    export EMBED_API_KEY='nvapi-...'\n\n"
        "Get your key at https://build.nvidia.com"
    )

connector = DuckDBDatabase(_DUCKDB_PATH)

EMBED_PARAMS = EmbedParams(
    embed_invoke_url=_EMBED_ENDPOINT,
    model_name=_EMBED_MODEL,
    api_key=_EMBED_API_KEY,
    embed_modality="text",
)

TABULAR_PARAMS = TabularExtractParams(
    connector=connector,
)


def run_ingest() -> None:
    """Build the tabular ingest graph, run it, and write embeddings to pgvector."""
    database_name = connector.database_name

    graph = (
        Graph()
        >> TabularSchemaExtractOp(tabular_params=TABULAR_PARAMS)
        >> TabularFetchEmbeddingsOp(database_name=database_name)
        >> _BatchEmbedActor(params=EMBED_PARAMS)
    )

    results = graph.execute(None)
    result_df = results[0] if results else None

    vdb = get_vdb(database_name=database_name)

    if result_df is not None and not result_df.empty:
        ingest_op = IngestVdbOperator(vdb=vdb)
        ingest_op(result_df.to_dict(orient="records"))
        print(f"Tabular ingest result: {len(result_df)} rows written to pgvector")
    else:
        print("Tabular ingest result: no rows produced")


if __name__ == "__main__":
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s %(levelname)s %(name)s: %(message)s",
    )
    run_ingest()
