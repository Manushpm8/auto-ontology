# SPDX-FileCopyrightText: Copyright (c) 2024-2026, NVIDIA CORPORATION & AFFILIATES.
# All rights reserved.
# SPDX-License-Identifier: Apache-2.0

from __future__ import annotations

import logging
import os

from nemo_retriever.graph import Graph
from nemo_retriever.graph.tabular_schema_extract_operator import TabularSchemaExtractOp
from nemo_retriever.graph.tabular_fetch_embeddings_operator import (
    TabularFetchEmbeddingsOp,
)
from nemo_retriever.text_embed.operators import _BatchEmbedActor
from nemo_retriever.vdb import IngestVdbOperator
from nemo_retriever.params import EmbedParams, TabularExtractParams
from gsf.vdb import get_vdb
from gsf.connectors.postgres import PostgresDatabase
from gsf.ingestion_service.table_types import apply_table_types

logger = logging.getLogger("ingestion_service.ingest")

_NVIDIA_API_KEY = os.environ.get("NVIDIA_API_KEY", "")
_EMBED_ENDPOINT = os.environ.get(
    "EMBED_ENDPOINT", "https://integrate.api.nvidia.com/v1"
)
_EMBED_MODEL = os.environ.get("EMBED_MODEL", "nvidia/llama-nemotron-embed-1b-v2")

if not _NVIDIA_API_KEY:
    logger.warning("NVIDIA_API_KEY is not set. ")

EMBED_PARAMS = EmbedParams(
    embed_invoke_url=_EMBED_ENDPOINT,
    model_name=_EMBED_MODEL,
    api_key=_NVIDIA_API_KEY,
    embed_modality="text",
)


def run_ingest(connection_string: str) -> None:
    TABULAR_PARAMS = TabularExtractParams(
        connector=PostgresDatabase(connection_string),
    )

    extract_graph = Graph() >> TabularSchemaExtractOp(tabular_params=TABULAR_PARAMS)
    extract_graph.execute(None)
    apply_table_types(TABULAR_PARAMS.connector)

    embed_graph = (
        Graph()
        >> TabularFetchEmbeddingsOp(
            database_name=TABULAR_PARAMS.connector.database_name
        )
        >> _BatchEmbedActor(params=EMBED_PARAMS)
    )
    results = embed_graph.execute(None)
    result_df = results[0] if results else None

    if result_df is not None and not result_df.empty:
        ingest_op = IngestVdbOperator(
            vdb=get_vdb(database_name=TABULAR_PARAMS.connector.database_name)
        )
        ingest_op(result_df.to_dict(orient="records"))
        logger.info(
            f"Tabular ingest result: {len(result_df)} rows written to pgvector",
        )
    else:
        logger.info("Tabular ingest result: no rows produced")
