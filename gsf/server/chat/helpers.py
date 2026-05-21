# SPDX-FileCopyrightText: Copyright (c) 2024-2026, NVIDIA CORPORATION & AFFILIATES.
# All rights reserved.
# SPDX-License-Identifier: Apache-2.0

"""Chat-route helpers: request models, connector registry, retriever factory."""

from __future__ import annotations

import logging
import os

from pydantic import BaseModel, Field

from nemo_retriever.retriever import Retriever
from gsf.connectors.postgres import PostgresDatabase
from gsf.vdb import get_vdb

logger = logging.getLogger(__name__)


class ChatRequest(BaseModel):
    """Payload sent by the frontend to start a chat completion."""

    question: str = Field(..., min_length=1)
    connector_name: str | None = None
    acronyms: str | None = None
    custom_prompts: str | None = None


# Maps LangGraph node names from
# nemo_retriever.tabular_data.retrieval.text_to_sql.text_to_sql_graph
# to a single user-facing label per agent (1-to-1 with the agent classes
# instantiated inside ``create_graph``). Unknown nodes fall through to the
# raw node_name in the router so we never display a blank thinking step.
NODE_LABELS: dict[str, str] = {
    "entities_extraction": "Extracting entities",
    "retrieve_candidates": "Retrieving candidates",
    "prepare_candidates": "Preparing candidates",
    "construct_sql_from_candidates": "Constructing SQL from candidates",
    "construct_sql_not_from_snippets": "Constructing SQL from tables",
    "reconstruct_sql": "Reconstructing SQL",
    "validate_sql_query": "Validating SQL",
    "validate_intent": "Validating intent",
    "execute_sql_query": "Executing SQL",
    "format_and_respond": "Formatting response",
    "unconstructable_sql_response": "SQL could not be constructed",
}

# Remote NIM embedding endpoint — no local GPU required.
# MUST match the model used at ingest time (see EMBED_PARAMS in
# dev_tools/ingest_local_postgres.py); a mismatch produces garbage results
# or a dimension error from pgvector.
_EMBED_ENDPOINT = os.environ.get(
    "EMBED_ENDPOINT", "https://integrate.api.nvidia.com/v1"
)
_EMBED_MODEL = os.environ.get("EMBED_MODEL", "nvidia/llama-nemotron-embed-1b-v2")
_NVIDIA_API_KEY = os.environ.get("NVIDIA_API_KEY", "")

_retriever: Retriever | None = None
_connector: PostgresDatabase | None = None


def get_connector() -> PostgresDatabase:
    """Return the source-DB connector for the chat agent.

    Reads ``CONNECTION_STRINGS`` from the environment (set in ``.env``); the
    same URL is used by ``dev_tools/ingest_local_postgres.py`` so chat queries
    target the database whose schema/embeddings were ingested.
    """
    global _connector
    if _connector is None:
        connection_strings = os.environ.get("CONNECTION_STRINGS", "").split(",")
        if len(connection_strings) == 1:
            _connector = PostgresDatabase(connection_strings[0])
        elif len(connection_strings) == 0:
            logger.warning(
                "CONNECTION_STRINGS is not set. Add it to your .env, e.g.:\n\n    CONNECTION_STRINGS=postgresql://user:password@host:5432/dbname"
            )
        else:
            logger.warning("Multiple connection is not supported yet.")

    return _connector


def get_retriever() -> Retriever:
    global _retriever
    if _retriever is None:
        vdb = get_vdb()
        _retriever = Retriever(
            vdb_kwargs={"vdb": vdb},
            embed_kwargs={
                "model_name": _EMBED_MODEL,
                "embed_invoke_url": _EMBED_ENDPOINT,
                "api_key": _NVIDIA_API_KEY,
            },
        )
    return _retriever
