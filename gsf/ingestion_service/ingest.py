# SPDX-FileCopyrightText: Copyright (c) 2024-2026, NVIDIA CORPORATION & AFFILIATES.
# All rights reserved.
# SPDX-License-Identifier: Apache-2.0

from __future__ import annotations

import logging
import threading
from typing import Any

from gsf.connectors.connection_string_factory import build_connection_string
from gsf.utils import get_embed_params
from nemo_retriever.graph import Graph
from nemo_retriever.tabular_data.operators.tabular_schema_extract_operator import (
    TabularSchemaExtractOp,
)
from nemo_retriever.tabular_data.operators.tabular_fetch_embeddings_operator import (
    TabularFetchEmbeddingsOp,
)
from nemo_retriever.operators.embed.operators import _BatchEmbedActor
from nemo_retriever.operators.vdb import IngestVdbOperator
from nemo_retriever.common.params.models import TabularExtractParams
from gsf.vdb import get_data_vdb
from gsf.connectors.registry import create_connector
from gsf.dal.reset import delete_database, delete_semantic

logger = logging.getLogger("ingestion_service.ingest")


def run_ingest(connection_string: str) -> None:
    TABULAR_PARAMS = TabularExtractParams(
        connector=create_connector(connection_string),
    )

    if not TABULAR_PARAMS.connector:
        raise ValueError("Connector is not set")

    try:
        database_name = TABULAR_PARAMS.connector.database_name
        embed_params = get_embed_params()

        graph = (
            Graph()
            >> TabularSchemaExtractOp(tabular_params=TABULAR_PARAMS)
            >> TabularFetchEmbeddingsOp(database_name=database_name)
            >> _BatchEmbedActor(params=embed_params)
        )

        results = graph.execute(None)
        result_df = results[0] if results else None

        if result_df is not None and not result_df.empty:
            data_vdb = get_data_vdb(database_name=database_name, reset=True)
            ingest_op = IngestVdbOperator(vdb=data_vdb)
            ingest_op(result_df.to_dict(orient="records"))
            logger.info(
                f"Tabular ingest result: {len(result_df)} rows written to pgvector",
            )

    finally:
        TABULAR_PARAMS.connector.close()


def trigger_ingest(connection: dict[str, Any]) -> None:
    """Run ingest for a new connection without blocking the caller."""
    connection_string = build_connection_string(connection)
    database_name = str(connection.get("database") or "")

    def _run() -> None:
        try:
            run_ingest(connection_string)
        except Exception:
            logger.exception(
                "Background ingest failed for database %s",
                database_name,
            )

    threading.Thread(
        target=_run,
        daemon=True,
        name=f"ingest-{database_name}",
    ).start()


def trigger_delete_ingest(database_name: str) -> None:
    """Delete a database's ingested graph and embeddings without blocking."""

    def _run() -> None:
        try:
            delete_database(database_name)
        except Exception:
            logger.exception(
                "Background delete-ingest failed for database %s",
                database_name,
            )

    threading.Thread(
        target=_run,
        daemon=True,
        name=f"delete-ingest-{database_name}",
    ).start()


def trigger_reset_semantic(database_name: str) -> None:
    """Delete a database's semantic layer then recompile it, non-blocking."""

    def _run() -> None:
        try:
            delete_semantic(database_name)
            from gsf.semantic.compile import run_semantic_compilation

            run_semantic_compilation(database_name)
        except Exception:
            logger.exception(
                "Background reset-semantic failed for database %s",
                database_name,
            )

    threading.Thread(
        target=_run,
        daemon=True,
        name=f"reset-semantic-{database_name}",
    ).start()
