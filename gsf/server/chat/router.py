# SPDX-FileCopyrightText: Copyright (c) 2024-2026, NVIDIA CORPORATION & AFFILIATES.
# All rights reserved.
# SPDX-License-Identifier: Apache-2.0

"""Chat streaming endpoint — wraps the LangGraph text-to-SQL pipeline.

Each chat request runs in its own subprocess (``AgentWorker``) so we can
hard-cancel an in-flight stream if the user navigates away mid-thinking and
sends a new question on return. See ``worker.py`` for the rationale.

Concurrency model
-----------------
The agent pipeline is not safe to run in parallel (shared retriever/connector
state, single LLM rate budget), so only one stream is allowed at a time.

* If the slot is empty → request runs.
* If the slot is held by a stream whose **client is still connected**
  (typical case: a second browser window/tab posting concurrently) →
  the new request is rejected with HTTP 409.
* If the slot is held by an **orphaned** stream (its client navigated away
  and the TCP connection died) → the new request preempts: the orphan
  worker is SIGKILLed and the new one takes over. This is what makes
  "navigate away → come back → ask again" work without a 409.

Disconnect detection
--------------------
A relying-on-write-failure ("the next heartbeat will fail") detection is too
slow in practice — the kernel buffers small writes and disconnect-triggered
GeneratorExit only fires when the response object is garbage-collected,
which can be tens of seconds. We instead spawn a per-request async watchdog
that polls ASGI ``http.disconnect`` and clears ``client_alive`` within one
poll interval (~100 ms), freeing the slot for any follow-up request.
"""

from __future__ import annotations

import asyncio
import json
import logging
import threading
from dataclasses import dataclass
from typing import Generator

from fastapi import APIRouter, HTTPException, Request
from fastapi.responses import StreamingResponse

from gsf.server.chat.helpers import NODE_LABELS, ChatRequest
from gsf.server.chat.worker import AgentWorker

logger = logging.getLogger(__name__)

router = APIRouter()

# How often the watchdog polls the ASGI disconnect signal. Small enough that
# a navigate-away → come-back-and-ask flow always finds the slot free.
_DISCONNECT_POLL_S = 0.1


@dataclass
class _Slot:
    """In-flight stream descriptor stored in ``_active_slot``."""

    worker: AgentWorker
    # Set while the SSE response is actively streaming to a connected client.
    # Cleared by the watchdog on disconnect or by the response generator's
    # ``finally`` on natural completion. A cleared event means "no one is
    # listening" → safe to preempt.
    client_alive: threading.Event


_active_slot: _Slot | None = None
_slot_lock = threading.Lock()


def _sse(event: dict) -> str:
    return f"data: {json.dumps(event)}\n\n"


def _try_claim_slot(new_slot: _Slot) -> _Slot | None:
    """Install ``new_slot`` if the previous one's client is gone.

    Returns the displaced slot (caller must kill its worker) on success.
    Raises HTTPException(409) if the previous slot still has a live client.
    """

    global _active_slot
    with _slot_lock:
        prev = _active_slot
        if prev is not None and prev.client_alive.is_set():
            raise HTTPException(
                status_code=409,
                detail="Conversation in progress",
            )
        _active_slot = new_slot
        return prev


def _release_slot(slot: _Slot) -> None:
    """Mark ``slot``'s client as gone and clear it from the active slot."""

    global _active_slot
    slot.client_alive.clear()
    with _slot_lock:
        if _active_slot is slot:
            _active_slot = None
    slot.worker.kill()


async def _watch_disconnect(http_request: Request, slot: _Slot) -> None:
    """Free the slot the moment the ASGI client disconnects.

    Without this, the sync streaming generator only learns the client is
    gone when its next write to the socket fails — and the kernel happily
    buffers small heartbeat writes, so that signal can lag by tens of
    seconds, holding the slot and forcing follow-up requests into 409.
    """

    while slot.client_alive.is_set():
        try:
            disconnected = await http_request.is_disconnected()
        except Exception:  # noqa: BLE001 — defensive; never want to leak
            logger.exception("Disconnect watchdog failed")
            return
        if disconnected:
            _release_slot(slot)
            return
        await asyncio.sleep(_DISCONNECT_POLL_S)


def _stream_chat(worker: AgentWorker) -> Generator[str, None, None]:
    try:
        for item in worker.events():
            if item is None:
                # SSE comment — keeps the connection alive against any
                # intermediary (proxy/load balancer) that drops idle
                # streams. Clients ignore non-"data:" lines.
                yield ": ping\n\n"
                continue
            event = item
            if event.get("type") == "step":
                node_name = event.get("node", "")
                event = {**event, "label": NODE_LABELS.get(node_name, node_name)}
            yield _sse(event)
    except RuntimeError as exc:
        logger.exception("Agent stream failed")
        yield _sse({"type": "error", "message": f"Agent stream failed: {exc}"})

    yield "data: [DONE]\n\n"


def _stream_with_slot(slot: _Slot) -> Generator[str, None, None]:
    try:
        yield from _stream_chat(slot.worker)
    finally:
        # Idempotent with the watchdog's cleanup — whichever runs first
        # frees the slot; the second call is a no-op.
        _release_slot(slot)


@router.post("/chat/completions")
async def chat_completions(
    request: ChatRequest, http_request: Request
) -> StreamingResponse:
    worker = AgentWorker()
    worker.start(request.question)
    slot = _Slot(worker=worker, client_alive=threading.Event())
    slot.client_alive.set()

    try:
        displaced = _try_claim_slot(slot)
    except HTTPException:
        # Lost the race against a still-connected sibling — tear down the
        # subprocess we just spawned so we don't leak it.
        worker.kill()
        raise

    if displaced is not None:
        displaced.worker.kill()

    # Fire-and-forget: the task self-terminates when client_alive clears
    # (either by disconnect detection or by the stream's finally on natural
    # completion). No await/cancel needed from the response path.
    asyncio.create_task(_watch_disconnect(http_request, slot))

    return StreamingResponse(
        _stream_with_slot(slot),
        media_type="text/event-stream",
        headers={
            "Cache-Control": "no-cache",
            "X-Accel-Buffering": "no",
        },
    )
