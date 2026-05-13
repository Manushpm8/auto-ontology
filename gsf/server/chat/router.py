"""Chat streaming endpoint — wraps the LangGraph text-to-SQL pipeline."""

from __future__ import annotations

import json
import logging
import os
from typing import Generator

from fastapi import APIRouter
from fastapi.responses import StreamingResponse

from nemo_retriever.retriever import Retriever
from nemo_retriever.tabular_data.retrieval.text_to_sql.main import stream_agent_response
from nemo_retriever.tabular_data.retrieval.text_to_sql.state import AgentPayload

from server.chat.connectors import get_connector
from server.chat.models import NODE_LABELS, ChatRequest

logger = logging.getLogger(__name__)

router = APIRouter()

# Must match the LanceDB table and embedder produced by
# scripts/ingest_local_postgres.py — otherwise query embeddings won't match
# the ingested vectors. Configured via LANCEDB_URI / LANCEDB_TABLE env vars
# so the API and ingest script can never drift apart.
_LANCEDB_URI = os.environ.get("LANCEDB_URI", "lancedb")
_LANCEDB_TABLE = os.environ.get("LANCEDB_TABLE", "nv-ingest-tabular")

# Remote NIM embedding endpoint — no local GPU required.
# MUST match the model used at ingest time (see EMBED_PARAMS in
# scripts/ingest_local_postgres.py); a mismatch produces garbage results
# or a dimension error from LanceDB.
_EMBED_ENDPOINT = os.environ.get(
    "EMBED_ENDPOINT", "https://integrate.api.nvidia.com/v1"
)
_EMBED_MODEL = os.environ.get("EMBED_MODEL", "nvidia/llama-nemotron-embed-1b-v2")

_retriever: Retriever | None = None


def _get_retriever() -> Retriever:
    global _retriever
    if _retriever is None:
        _retriever = Retriever(
            vdb="lancedb",
            vdb_kwargs={
                "uri": _LANCEDB_URI,
                "table_name": _LANCEDB_TABLE,
            },
            embedder=_EMBED_MODEL,
            embedding_http_endpoint=_EMBED_ENDPOINT,
            embedding_api_key=os.environ.get("NVIDIA_API_KEY", ""),
        )
    return _retriever


def _sse(event: dict) -> str:
    return f"data: {json.dumps(event)}\n\n"


def _build_payload(request: ChatRequest) -> AgentPayload:
    return {
        "question": request.question,
        "retriever": _get_retriever(),
        "connector": get_connector(),
        "acronyms": request.acronyms or "",
        "custom_prompts": request.custom_prompts or "",
    }


def _stream_chat(request: ChatRequest) -> Generator[str, None, None]:
    try:
        payload = _build_payload(request)
    except Exception as exc:
        logger.exception("Failed to build chat payload")
        yield _sse({"type": "error", "message": str(exc)})
        yield "data: [DONE]\n\n"
        return

    try:
        for event in stream_agent_response(payload):
            if event.get("type") == "step":
                node_name = event.get("node", "")
                event = {**event, "label": NODE_LABELS.get(node_name, node_name)}
            yield _sse(event)
    except Exception as exc:
        logger.exception("Agent stream failed")
        yield _sse({"type": "error", "message": f"Agent stream failed: {exc}"})

    yield "data: [DONE]\n\n"


@router.post("/chat/completions")
def chat_completions(request: ChatRequest) -> StreamingResponse:
    return StreamingResponse(
        _stream_chat(request),
        media_type="text/event-stream",
        headers={
            "Cache-Control": "no-cache",
            "X-Accel-Buffering": "no",
        },
    )
