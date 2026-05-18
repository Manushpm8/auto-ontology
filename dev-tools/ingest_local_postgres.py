# SPDX-FileCopyrightText: Copyright (c) 2024-2026, NVIDIA CORPORATION & AFFILIATES.
# All rights reserved.
# SPDX-License-Identifier: Apache-2.0

"""Ingest the local docker-compose Postgres into the pgvector embeddings store.

Run after ``docker compose up -d`` and ``scripts.seed_local_postgres``.

Usage::

    PYTHONPATH=gsf uv run --no-sync python -m scripts.ingest_local_postgres
"""

from __future__ import annotations

import logging
import os

from nemo_retriever.graph import Graph
from nemo_retriever.graph.tabular_schema_extract_operator import TabularSchemaExtractOp
from nemo_retriever.graph.tabular_fetch_embeddings_operator import (
    TabularFetchEmbeddingsOp,
)
from nemo_retriever.text_embed.operators import _BatchEmbedActor
from nemo_retriever.retriever import Retriever
from nemo_retriever.tabular_data.retrieval.text_to_sql.main import get_agent_response
from nemo_retriever.tabular_data.retrieval.text_to_sql.state import AgentPayload
from nemo_retriever.vdb import IngestVdbOperator
from nemo_retriever.params import EmbedParams, TabularExtractParams
from gsf.vdb import get_vdb
from gsf.connectors.postgres import PostgresDatabase

logger = logging.getLogger("scripts.ingest_local_postgres")

_NVIDIA_API_KEY = os.environ.get("NVIDIA_API_KEY", "")
_EMBED_ENDPOINT = os.environ.get(
    "EMBED_ENDPOINT", "https://integrate.api.nvidia.com/v1"
)
_EMBED_MODEL = os.environ.get("EMBED_MODEL", "nvidia/llama-nemotron-embed-1b-v2")

if not _NVIDIA_API_KEY:
    raise EnvironmentError(
        "NVIDIA_API_KEY is not set. "
        "Export it before running:\n\n"
        "    export NVIDIA_API_KEY='nvapi-...'\n\n"
        "Get your key at https://build.nvidia.com"
    )


# Remote NIM embedding endpoint — no local GPU required.
# Model hosted on build.nvidia.com; billed against your NVIDIA API key.
# Configured via EMBED_ENDPOINT / EMBED_MODEL env vars so the API server
# (gsf/server/chat/router.py) and this script always agree.
EMBED_PARAMS = EmbedParams(
    embed_invoke_url=_EMBED_ENDPOINT,
    model_name=_EMBED_MODEL,
    api_key=_NVIDIA_API_KEY,
    embed_modality="text",
)

# Remote source DB to extract tabular schema/embeddings from. Kept separate
# from the local POSTGRES_* vars (which point at the pgvector store).
_CONNECTION_STRINGS = os.environ.get("CONNECTION_STRINGS", "")
if not _CONNECTION_STRINGS:
    raise EnvironmentError(
        "CONNECTION_STRINGS is not set. Add it to your .env, e.g.:\n\n"
        "    CONNECTION_STRINGS=postgresql://user:password@host:5432/dbname"
    )

TABULAR_PARAMS = TabularExtractParams(
    connector=PostgresDatabase(_CONNECTION_STRINGS),
)


def run_ingest() -> None:
    """Build the tabular ingest graph, run it, and write embeddings to pgvector."""
    graph = (
        Graph()
        >> TabularSchemaExtractOp(tabular_params=TABULAR_PARAMS)
        >> TabularFetchEmbeddingsOp(
            database_name=TABULAR_PARAMS.connector.database_name
        )
        >> _BatchEmbedActor(params=EMBED_PARAMS)
    )

    results = graph.execute(None)
    result_df = results[0] if results else None

    if result_df is not None and not result_df.empty:
        ingest_op = IngestVdbOperator(
            vdb=get_vdb(database_name=TABULAR_PARAMS.connector.database_name)
        )
        ingest_op(result_df.to_dict(orient="records"))
        print(
            "Tabular ingest result:",
            len(result_df),
            "rows written to pgvector)",
        )
    else:
        print("Tabular ingest result: no rows produced")


def run_retrieve() -> None:
    """Run the text-to-SQL agent against the previously ingested pgvector store."""
    retriever = Retriever(
        top_k=15,
        vdb_kwargs={"vdb": get_vdb()},
        embed_kwargs={
            "model_name": _EMBED_MODEL,
            "embed_invoke_url": _EMBED_ENDPOINT,
            "api_key": _NVIDIA_API_KEY,
        },
    )

    question = "List actors"

    payload: AgentPayload = {
        "question": question,
        "retriever": retriever,
        "connector": TABULAR_PARAMS.connector,
        "path_state": {},
        "custom_prompts": "",
        "acronyms": "",
    }

    agent_result = get_agent_response(payload)
    print("get_agent_response result:", agent_result)


if __name__ == "__main__":
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s %(levelname)s %(name)s: %(message)s",
    )
    run_ingest()
    run_retrieve()
