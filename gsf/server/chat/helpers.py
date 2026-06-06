# SPDX-FileCopyrightText: Copyright (c) 2024-2026, NVIDIA CORPORATION & AFFILIATES.
# All rights reserved.
# SPDX-License-Identifier: Apache-2.0

"""Chat-route helpers: request models, node labels, retriever factory."""

from __future__ import annotations

import os

from pydantic import BaseModel, Field

from nemo_retriever.retriever import Retriever

from gsf.vdb import get_data_vdb, get_semantic_vdb

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

_EMBED_ENDPOINT = os.environ.get(
    "EMBED_ENDPOINT", "https://integrate.api.nvidia.com/v1"
)
_EMBED_MODEL = os.environ.get("EMBED_MODEL", "nvidia/llama-nemotron-embed-1b-v2")
_NVIDIA_API_KEY = os.environ.get("NVIDIA_API_KEY", "")

_retriever: Retriever | None = None
_semantic_retriever: Retriever | None = None


class ChatRequest(BaseModel):
    """Payload sent by the frontend to start a chat completion."""

    question: str = Field(..., min_length=1)


def get_retriever() -> Retriever:
    global _retriever
    if _retriever is None:
        vdb = get_data_vdb()
        _retriever = Retriever(
            vdb_kwargs={"vdb": vdb},
            embed_kwargs={
                "model_name": _EMBED_MODEL,
                "embed_invoke_url": _EMBED_ENDPOINT,
                "api_key": _NVIDIA_API_KEY,
            },
        )
    return _retriever


def get_semantic_retriever() -> Retriever:
    """Retriever backed by the semantic_layer pgvector collection."""
    global _semantic_retriever
    if _semantic_retriever is None:
        vdb = get_semantic_vdb()
        _semantic_retriever = Retriever(
            vdb_kwargs={"vdb": vdb},
            embed_kwargs={
                "model_name": _EMBED_MODEL,
                "embed_invoke_url": _EMBED_ENDPOINT,
                "api_key": _NVIDIA_API_KEY,
            },
        )
    return _semantic_retriever
