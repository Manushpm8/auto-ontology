# SPDX-FileCopyrightText: Copyright (c) 2024-2026, NVIDIA CORPORATION & AFFILIATES.
# All rights reserved.
# SPDX-License-Identifier: Apache-2.0

"""Chat streaming endpoint — wraps the LangGraph text-to-SQL pipeline."""

from __future__ import annotations

import json
import logging
from typing import AsyncGenerator

from fastapi import APIRouter, Request
from fastapi.responses import StreamingResponse
from starlette.concurrency import iterate_in_threadpool, run_in_threadpool

from nemo_retriever.tabular_data.retrieval.text_to_sql.main import stream_agent_response

from gsf.server.chat import active_streams
from gsf.server.chat.helpers import (
    NODE_LABELS,
    ChatRequest,
    get_connector,
    get_retriever,
)
from gsf.server.chat.settings_dal import fetch_acronyms, fetch_custom_prompts

logger = logging.getLogger(__name__)

router = APIRouter()


def _sse(event: dict) -> str:
    return f"data: {json.dumps(event)}\n\n"


async def _stream_chat(
    http_request: Request, request: ChatRequest
) -> AsyncGenerator[str, None]:
    try:
        payload = {
            "question": request.question,
            "retriever": get_retriever(),
            "connector": get_connector(),
            "acronyms": fetch_acronyms(),
            "custom_prompts": fetch_custom_prompts(),
        }
    except Exception as exc:
        logger.exception("Failed to build chat payload")
        yield _sse({"type": "error", "message": str(exc)})
        yield "data: [DONE]\n\n"
        return

    cancel_event = await active_streams.claim(request.conversation_id)
    try:
        # ``stream_agent_response`` is a blocking sync generator (LangGraph +
        # LLM calls). Even constructing the generator can do non-trivial
        # work at the call site (config loading, model warm-up), so we
        # build it in a worker thread to keep the event loop responsive.
        agent_iter = await run_in_threadpool(stream_agent_response, payload)

        # ``iterate_in_threadpool`` runs each ``next()`` in a worker
        # thread so the event loop stays free to detect client disconnects
        # between events.
        #
        # NOTE: when we ``return`` early below (client disconnect or
        # supersession), the async iterator is closed but the worker
        # thread currently blocked inside ``next()`` cannot be
        # interrupted — the in-flight LLM/LangGraph call will run to
        # completion in the background. This is wasted compute/$, not a
        # correctness issue. If/when ``stream_agent_response`` grows a
        # cooperative cancellation hook we should propagate
        # ``cancel_event`` into it.
        async for event in iterate_in_threadpool(agent_iter):
            if await http_request.is_disconnected():
                logger.info("Client disconnected, aborting chat stream")
                return
            if cancel_event is not None and cancel_event.is_set():
                logger.info(
                    "Stream superseded for conversation %s",
                    request.conversation_id,
                )
                yield _sse(
                    {
                        "type": "error",
                        "message": "Superseded by a newer request",
                    }
                )
                return
            if event.get("type") == "step":
                node_name = event.get("node", "")
                event = {**event, "label": NODE_LABELS.get(node_name, node_name)}
            yield _sse(event)
    except Exception as exc:
        logger.exception("Agent stream failed")
        yield _sse({"type": "error", "message": f"Agent stream failed: {exc}"})
    finally:
        await active_streams.release(request.conversation_id, cancel_event)

    yield "data: [DONE]\n\n"


@router.post("/chat/completions")
async def chat_completions(
    http_request: Request, request: ChatRequest
) -> StreamingResponse:
    return StreamingResponse(
        _stream_chat(http_request, request),
        media_type="text/event-stream",
        headers={
            "Cache-Control": "no-cache",
            "X-Accel-Buffering": "no",
        },
    )
