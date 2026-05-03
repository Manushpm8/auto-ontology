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
from nemo_retriever.vector_store.lancedb_store import (
    LanceDBConfig,
    _build_lancedb_rows_from_df,
    _write_rows_to_lancedb,
)
from nemo_retriever.params import (
    EmbedParams,
    TabularExtractParams,
)

logger = logging.getLogger("scripts.ingest_local_postgres")

# Load .env files before reading any env vars at module level so that
# NVIDIA_API_KEY / LANCEDB_URI / LANCEDB_TABLE picked up below match what
# the FastAPI server sees at runtime.
load_server_env()

_NVIDIA_API_KEY = os.environ.get("NVIDIA_API_KEY", "")
_EMBED_ENDPOINT = os.environ.get("EMBED_ENDPOINT", "https://integrate.api.nvidia.com/v1")
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

LANCEDB_CONFIG = LanceDBConfig(
    uri=_LANCEDB_URI,
    table_name=_LANCEDB_TABLE,
    overwrite=True,
    create_index=False,  # local dev dataset is too small for IVF-PQ index
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
        rows = _build_lancedb_rows_from_df(result_df.to_dict(orient="records"))
        if rows:
            _write_rows_to_lancedb(rows, cfg=LANCEDB_CONFIG)
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
