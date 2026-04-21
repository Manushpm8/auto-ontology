"""Ingest the local docker-compose Postgres into Neo4j via NeMo Retriever.

Run after ``docker compose up -d`` and ``scripts.seed_local_postgres``.

Usage::

    PYTHONPATH=gsf uv run --no-sync python -m scripts.ingest_local_postgres
"""

from __future__ import annotations

import logging
import os

from gsf.server.connectors.postgres import PostgresDatabase
from gsf.server.env import load_server_env
from nemo_retriever.graph import Graph
from nemo_retriever.graph.tabular_schema_extract_operator import TabularSchemaExtractOp
from nemo_retriever.graph.tabular_fetch_embeddings_operator import TabularFetchEmbeddingsOp
from nemo_retriever.text_embed.operators import _BatchEmbedActor
from nemo_retriever.tabular_data.retrieval import generate_sql
from nemo_retriever.vector_store.lancedb_store import handle_lancedb
from nemo_retriever.params import (
    EmbedParams,
    TabularExtractParams,
    VdbUploadParams,
)

logger = logging.getLogger("scripts.ingest_local_postgres")

_NVIDIA_API_KEY = os.environ.get("NVIDIA_API_KEY", "")
if not _NVIDIA_API_KEY:
    raise EnvironmentError(
        "NVIDIA_API_KEY is not set. "
        "Export it before running:\n\n"
        "    export NVIDIA_API_KEY='nvapi-...'\n\n"
        "Get your key at https://build.nvidia.com"
    )

# Remote NIM embedding endpoint — no local GPU required.
# Model hosted on build.nvidia.com; billed against your NVIDIA API key.
EMBED_PARAMS = EmbedParams(
    embed_invoke_url="https://integrate.api.nvidia.com/v1",
    model_name="nvidia/llama-nemotron-embed-1b-v2",
    api_key=_NVIDIA_API_KEY,
    embed_modality="text",
)

VDB_PARAMS = VdbUploadParams(
    lancedb={
        "lancedb_uri": "lancedb",
        "table_name": "nv-ingest-tabular",
        "overwrite": True,
        "create_index": True,
    }
)

DATABASE: str = "testdb"


def _conn_string(db: str) -> str:
    host = "localhost"
    port = 5432
    user = os.environ["POSTGRES_USER"]
    password = os.environ["POSTGRES_PASSWORD"]
    return f"postgresql://{user}:{password}@{host}:{port}/{db}"


def ingest(database: str = DATABASE) -> None:
    """Ingest each Postgres database in ``databases`` into Neo4j."""
    load_server_env()

    TABULAR_PARAMS = TabularExtractParams(
        connector=PostgresDatabase(_conn_string(database)),
    )
    graph = (
        Graph()
        >> TabularSchemaExtractOp(tabular_params=TABULAR_PARAMS)
        >> TabularFetchEmbeddingsOp()
        >> _BatchEmbedActor(params=EMBED_PARAMS)
    )

    results = graph.execute(None)
    result_df = results[0] if results else None

    if result_df is not None and not result_df.empty:
        lancedb_params = VDB_PARAMS.lancedb
        handle_lancedb(
            result_df.to_dict(orient="records"),
            uri=lancedb_params.lancedb_uri,
            table_name=lancedb_params.table_name,
        )
        logger.info("Tabular ingest result:", len(result_df), "rows written to LanceDB")
    else:
        logger.info("Tabular ingest result: no rows produced")

    sql_result = generate_sql("How many customers exists?")
    logger.info("generate_sql result:", sql_result)


if __name__ == "__main__":
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s %(levelname)s %(name)s: %(message)s",
    )
    ingest()