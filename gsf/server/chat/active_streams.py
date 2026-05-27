# SPDX-FileCopyrightText: Copyright (c) 2024-2026, NVIDIA CORPORATION & AFFILIATES.
# All rights reserved.
# SPDX-License-Identifier: Apache-2.0

"""Per-conversation stream registry.

Keeps an in-memory mapping of ``conversation_id -> asyncio.Event``. When a new
chat-completion request arrives for a conversation that already has a stream
in flight, the registry signals the older stream to abort cooperatively at the
next event boundary. This protects the conversation history from concurrent
writes coming from a second browser tab or a rapid double-submit.

The registry is single-process. If the API is ever scaled horizontally, this
must move to a shared store (Redis pub/sub, Postgres LISTEN/NOTIFY, ...).
"""

from __future__ import annotations

import asyncio
import logging

logger = logging.getLogger(__name__)


_active_streams: dict[str, asyncio.Event] = {}
_registry_lock = asyncio.Lock()


async def claim(conversation_id: str | None) -> asyncio.Event | None:
    """Register a new stream for ``conversation_id``.

    Returns the cancellation event the caller must check between yields. If an
    older stream is registered for the same conversation, its event is set so
    the older generator can exit gracefully. ``None`` is returned for streams
    without a conversation id (nothing to deduplicate against).
    """
    if not conversation_id:
        return None
    async with _registry_lock:
        previous = _active_streams.get(conversation_id)
        if previous is not None and not previous.is_set():
            logger.info(
                "Superseding active stream for conversation %s", conversation_id
            )
            previous.set()
        cancel_event = asyncio.Event()
        _active_streams[conversation_id] = cancel_event
        return cancel_event


async def release(
    conversation_id: str | None, cancel_event: asyncio.Event | None
) -> None:
    """Remove the stream from the registry if it still owns the slot.

    A slot may have been overwritten by a newer stream — in that case we must
    not evict the newer entry. Matching by identity (``is``) makes this safe.
    """
    if not conversation_id or cancel_event is None:
        return
    async with _registry_lock:
        current = _active_streams.get(conversation_id)
        if current is cancel_event:
            del _active_streams[conversation_id]
