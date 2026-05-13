"""Ingest the local docker-compose Postgres into the pgvector embeddings store.

Run after ``docker compose up -d`` and ``scripts.seed_local_postgres``.

Usage::

    PYTHONPATH=gsf uv run --no-sync python -m scripts.ingest_local_postgres
"""

from __future__ import annotations

import logging
import os

from langchain_nvidia_ai_endpoints import NVIDIAEmbeddings
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

from gsf.connectors.postgres import PostgresDatabase
from gsf.vdb.postgres import PostgresVDB

logger = logging.getLogger("scripts.ingest_local_postgres")

_NVIDIA_API_KEY = os.environ.get("NVIDIA_API_KEY", "")
if not _NVIDIA_API_KEY:
    raise EnvironmentError(
        "NVIDIA_API_KEY is not set. "
        "Export it before running:\n\n"
        "    export NVIDIA_API_KEY='nvapi-...'\n\n"
        "Get your key at https://build.nvidia.com"
    )


EMBED_MODEL = "nvidia/llama-nemotron-embed-1b-v2"
EMBED_INVOKE_URL = "https://integrate.api.nvidia.com/v1"

# Remote NIM embedding endpoint — no local GPU required.
# Model hosted on build.nvidia.com; billed against your NVIDIA API key.
EMBED_PARAMS = EmbedParams(
    embed_invoke_url=EMBED_INVOKE_URL,
    model_name=EMBED_MODEL,
    api_key=_NVIDIA_API_KEY,
    embed_modality="text",
)

# Source database to ingest.
SOURCE_DATABASE: str = "pagila"

# Postgres database that hosts the pgvector embeddings table.
VDB_DATABASE: str = os.environ.get("POSTGRES_DATABASE", "gsf")
VDB_COLLECTION: str = "nv_ingest_tabular"


def _conn_string(db: str) -> str:
    host = os.environ.get("POSTGRES_HOST", "localhost")
    port = os.environ.get("POSTGRES_PORT", "5432")
    user = os.environ["POSTGRES_USER"]
    password = os.environ["POSTGRES_PASSWORD"]
    return f"postgresql://{user}:{password}@{host}:{port}/{db}"


TABULAR_PARAMS = TabularExtractParams(
    connector=PostgresDatabase(_conn_string(SOURCE_DATABASE)),
)


def _build_vdb(
    *, with_query_embedder: bool, include_database_name: bool = True
) -> PostgresVDB:
    """Build a PostgresVDB pointed at the local pgvector-enabled Postgres.

    ``with_query_embedder=True`` wires up an NVIDIA embedder for the read path
    (similarity search). On the ingest path we don't need it because vectors
    are precomputed upstream by the NeMo Retriever pipeline.
    """
    kwargs: dict = {
        "connection_string": _conn_string(VDB_DATABASE),
        "collection_name": VDB_COLLECTION,
    }
    if include_database_name:
        kwargs["database_name"] = TABULAR_PARAMS.connector.database_name
    if with_query_embedder:
        kwargs["embeddings"] = NVIDIAEmbeddings(
            api_key=_NVIDIA_API_KEY,
            model=EMBED_MODEL,
            base_url=EMBED_INVOKE_URL,
        )
    return PostgresVDB(**kwargs)


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
            vdb=_build_vdb(with_query_embedder=False, include_database_name=True)
        )
        ingest_op(result_df.to_dict(orient="records"))
        print(
            "Tabular ingest result:",
            len(result_df),
            f"rows written to pgvector ({VDB_DATABASE}.{VDB_COLLECTION})",
        )
    else:
        print("Tabular ingest result: no rows produced")


def run_retrieve() -> None:
    """Run the text-to-SQL agent against the previously ingested pgvector store."""
    retriever = Retriever(
        # vdb=,_build_vdb(with_query_embedder=True, include_database_name=False)
        top_k=15,
        vdb_kwargs={
            "vdb": _build_vdb(with_query_embedder=False, include_database_name=False)
        },
        # embedding_api_key=_NVIDIA_API_KEY,
        # embedding_http_endpoint=EMBED_PARAMS.embed_invoke_url,
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
