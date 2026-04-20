"""Chat streaming endpoint — wraps the LangGraph text-to-SQL pipeline."""

from __future__ import annotations

import json
import logging
from typing import Generator

from fastapi import APIRouter
from fastapi.responses import StreamingResponse

from nemo_retriever.tabular_data.retrieval.text_to_sql.main import (
    stream_agent_response,
)
from nemo_retriever.tabular_data.retrieval.text_to_sql.state import AgentPayload

from server.chat.connectors import get_connector, list_connectors
from server.chat.models import NODE_LABELS, ChatRequest

logger = logging.getLogger(__name__)

router = APIRouter()


def _sse(data: str) -> str:
    return f"data: {data}\n\n"


def _stream_chat(request: ChatRequest) -> Generator[str, None, None]:
    all_connectors = list_connectors()
    connector = get_connector(all_connectors[0]["id"])

    payload: AgentPayload = {
        "question": request.question,
        "connector": connector,
        "acronyms": request.acronyms or "",
        "custom_prompts": request.custom_prompts or "",
    }

    for event in stream_agent_response(payload):
        if event["type"] == "step":
            node = event["node"]
            label = NODE_LABELS.get(node, node)
            yield _sse(json.dumps({"type": "step", "node": node, "label": label}))
        else:
            yield _sse(json.dumps(event))

    yield _sse("[DONE]")


@router.get("/chat/connectors")
def get_connectors() -> list[dict]:
    """Return available connectors (id + name + type, no secrets)."""
    return list_connectors()


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
