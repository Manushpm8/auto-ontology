"""Ingest the local docker-compose Postgres into Neo4j via NeMo Retriever.

Run after ``docker compose up -d`` and ``scripts.seed_local_postgres``.

Usage::

    PYTHONPATH=gsf uv run --no-sync python -m scripts.ingest_local_postgres
"""

from __future__ import annotations

import logging
import os

from gsf.connectors.postgres import PostgresDatabase
from gsf.server.env import load_server_env
from nemo_retriever.graph import Graph
from nemo_retriever.graph.tabular_schema_extract_operator import TabularSchemaExtractOp
from nemo_retriever.graph.tabular_fetch_embeddings_operator import (
    TabularFetchEmbeddingsOp,
)
from nemo_retriever.text_embed.operators import _BatchEmbedActor
from nemo_retriever.tabular_data.retrieval import generate_sql
from nemo_retriever.vdb import IngestVdbOperator
from nemo_retriever.params import (
    EmbedParams,
    TabularExtractParams,
    VdbUploadParams,
)

logger = logging.getLogger("scripts.ingest_local_postgres")

# Load .env files before reading any env vars at module level so that
# NVIDIA_API_KEY / LANCEDB_URI / LANCEDB_TABLE picked up below match what
# the FastAPI server sees at runtime.
load_server_env()

_NVIDIA_API_KEY = os.environ.get("NVIDIA_API_KEY", "")
_EMBED_ENDPOINT = os.environ.get(
    "EMBED_ENDPOINT", "https://integrate.api.nvidia.com/v1"
)
_EMBED_MODEL = os.environ.get("EMBED_MODEL", "nvidia/llama-nemotron-embed-1b-v2")

_LANCEDB_URI = os.environ.get("LANCEDB_URI", "lancedb")
_LANCEDB_TABLE = os.environ.get("LANCEDB_TABLE", "nv-ingest-tabular")


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

# vdb_kwargs are forwarded straight to ``nemo_retriever.vdb.lancedb.LanceDB``.
VDB_PARAMS = VdbUploadParams(
    vdb_op="lancedb",
    vdb_kwargs={
        "uri": _LANCEDB_URI,
        "table_name": _LANCEDB_TABLE,
        "overwrite": True,
    },
)


def _conn_string(db: str) -> str:
    host = "localhost"
    port = 5432
    user = os.environ["POSTGRES_USER"]
    password = os.environ["POSTGRES_PASSWORD"]
    return f"postgresql://{user}:{password}@{host}:{port}/{db}"


DATABASE: str = "testdb"
TABULAR_PARAMS = TabularExtractParams(
    connector=PostgresDatabase(_conn_string(DATABASE)),
)


def ingest(database: str = DATABASE) -> None:
    """Ingest each Postgres database in ``databases`` into Neo4j."""
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
            vdb_op=VDB_PARAMS.vdb_op,
            vdb_kwargs=VDB_PARAMS.vdb_kwargs,
        )
        ingest_op(result_df.to_dict(orient="records"))
        logger.info("Tabular ingest result: %d rows written to LanceDB", len(result_df))
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
