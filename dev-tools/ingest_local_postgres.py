"""Ingest the local docker-compose Postgres into Neo4j via NeMo Retriever.

Run after ``docker compose up -d`` and ``scripts.seed_local_postgres``.

Usage::

    PYTHONPATH=gsf uv run --no-sync python -m scripts.ingest_local_postgres
"""

from __future__ import annotations

import logging
import os

from nemo_retriever.graph import Graph
from nemo_retriever.graph.tabular_schema_extract_operator import TabularSchemaExtractOp
from nemo_retriever.graph.tabular_fetch_embeddings_operator import TabularFetchEmbeddingsOp
from nemo_retriever.text_embed.operators import _BatchEmbedActor
from nemo_retriever.retriever import Retriever
from nemo_retriever.tabular_data.retrieval.text_to_sql.main import get_agent_response
from nemo_retriever.tabular_data.retrieval.text_to_sql.state import AgentPayload
from nemo_retriever.vdb import IngestVdbOperator
from nemo_retriever.params import (
    EmbedParams,
    TabularExtractParams,
    VdbUploadParams,
)
from gsf.connectors.postgres import PostgresDatabase

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
    vdb_op="lancedb",
    vdb_kwargs={
        "uri": "lancedb",
        "table_name": "nv-ingest-tabular",
        "overwrite": True,
    },
)

DATABASE: str = "testdb"

TABULAR_PARAMS = TabularExtractParams(
    connector=PostgresDatabase(_conn_string(DATABASE)),
)

def _conn_string(db: str) -> str:
    host = "localhost"
    port = 5432
    user = os.environ["POSTGRES_USER"]
    password = os.environ["POSTGRES_PASSWORD"]
    return f"postgresql://{user}:{password}@{host}:{port}/{db}"


def run_ingest() -> None:
    """Build the tabular ingest graph, run it, and write embeddings to LanceDB."""
    graph = (
        Graph()
        >> TabularSchemaExtractOp(tabular_params=TABULAR_PARAMS)
        >> TabularFetchEmbeddingsOp(database_name=TABULAR_PARAMS.connector.database_name)
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
        print("Tabular ingest result:", len(result_df), "rows written to LanceDB")
    else:
        print("Tabular ingest result: no rows produced")


def run_retrieve() -> None:
    """Run the text-to-SQL agent against the previously ingested LanceDB."""
    lancedb_kwargs = VDB_PARAMS.vdb_kwargs
    retriever = Retriever(
        vdb="lancedb",
        vdb_kwargs={
            "uri": lancedb_kwargs["uri"],
            "table_name": lancedb_kwargs["table_name"],
        },
        top_k=15,
        embedding_api_key=_NVIDIA_API_KEY,
        embedding_http_endpoint=EMBED_PARAMS.embed_invoke_url,
    )

    question = "List aircraft codes"

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
