"""Chat streaming endpoint — wraps the LangGraph text-to-SQL pipeline."""

from __future__ import annotations

import json
import logging
from typing import Generator

from fastapi import APIRouter
from fastapi.responses import StreamingResponse

from nemo_retriever.tabular_data.retrieval.text_to_sql.main import stream_agent_response
from nemo_retriever.tabular_data.retrieval.text_to_sql.state import AgentPayload

from server.chat.helpers import (
    NODE_LABELS,
    ChatRequest,
    get_connector,
    get_retriever,
)

logger = logging.getLogger(__name__)

router = APIRouter()


def _sse(event: dict) -> str:
    return f"data: {json.dumps(event)}\n\n"


def _stream_chat(request: ChatRequest) -> Generator[str, None, None]:
    try:
        payload = {
            "question": request.question,
            "retriever": get_retriever(),
            "connector": get_connector(),
            "acronyms": request.acronyms or "",
            "custom_prompts": request.custom_prompts or "",
        }
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
