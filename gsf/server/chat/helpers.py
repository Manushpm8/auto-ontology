"""Chat-route helpers: request models, connector registry, retriever factory."""

from __future__ import annotations

import logging
import os

from pydantic import BaseModel, Field

from nemo_retriever.retriever import Retriever
from config import get_postgres_connection_string
from connectors.postgres import PostgresDatabase
from vdb.postgres import PostgresVDB

logger = logging.getLogger(__name__)


class ChatRequest(BaseModel):
    """Payload sent by the frontend to start a chat completion."""

    question: str = Field(..., min_length=1)
    connector_name: str | None = None
    acronyms: str | None = None
    custom_prompts: str | None = None


NODE_LABELS: dict[str, str] = {
    "entities_extraction": "Extracting entities…",
    "retrieve_candidates": "Searching relevant data…",
    "prepare_candidates": "Searching relevant data…",
    "construct_sql_from_candidates": "Constructing SQL query…",
    "construct_sql_not_from_snippets": "Constructing SQL query…",
    "validate_sql_query": "Validating SQL…",
    "validate_intent": "Validating SQL…",
    "reconstruct_sql": "Reconstructing SQL…",
    "execute_sql_query": "Executing query…",
    "format_and_respond": "Formatting response…",
    "unconstructable_sql_response": "Query could not be constructed",
}


# pgvector store containing tabular embeddings produced by
# dev-tools/ingest_local_postgres.py. Read-side: the Retriever embeds the
# query string itself (see EMBED_* below), then PostgresVDB.retrieval runs
# a similarity search by vector — so we don't pass an `embeddings` instance
# to the VDB on this path.
_VDB_COLLECTION = os.environ.get("VDB_COLLECTION", "nv_ingest_tabular")

# Remote NIM embedding endpoint — no local GPU required.
# MUST match the model used at ingest time (see EMBED_PARAMS in
# dev-tools/ingest_local_postgres.py); a mismatch produces garbage results
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

    Reads ``CONNECTOR_URL`` from the environment (set in ``.env``); the same
    URL is used by ``dev-tools/ingest_local_postgres.py`` so chat queries
    target the database whose schema/embeddings were ingested.
    """
    global _connector
    if _connector is None:
        url = os.environ.get("CONNECTOR_URL", "")
        if not url:
            raise EnvironmentError(
                "CONNECTOR_URL is not set. Add it to your .env, e.g.:\n\n"
                "    CONNECTOR_URL=postgresql://user:password@host:5432/dbname"
            )
        _connector = PostgresDatabase(url)
    return _connector


def get_retriever() -> Retriever:
    global _retriever
    if _retriever is None:
        vdb = PostgresVDB(
            connection_string=get_postgres_connection_string(),
            collection_name=_VDB_COLLECTION,
        )
        _retriever = Retriever(
            vdb_kwargs={"vdb": vdb},
            embed_kwargs={
                "model_name": _EMBED_MODEL,
                "embed_invoke_url": _EMBED_ENDPOINT,
                "api_key": _NVIDIA_API_KEY,
            },
        )
    return _retriever
