# SPDX-FileCopyrightText: Copyright (c) 2024-2026, NVIDIA CORPORATION & AFFILIATES.
# All rights reserved.
# SPDX-License-Identifier: Apache-2.0

"""Run the chat agent in a dedicated subprocess so it can be hard-cancelled.

The text-to-SQL agent (``stream_agent_response``) is sync Python and spends
most of its time inside C-level calls (psycopg, httpx, embedding clients).
Once such a call is in flight there is no portable way to interrupt it from
another Python thread — neither AbortController on the client nor a
``threading.Event`` on the server can do anything until the call returns.

To support clean cancellation when the user navigates away from the chat
mid-stream, each chat request runs in its own ``multiprocessing`` worker
process. Cancelling the request means SIGTERM-then-SIGKILL the worker —
which the OS handles even mid-syscall, freeing the slot immediately.

Trade-off: each request pays a cold-start cost (Python interpreter spawn +
nemo_retriever import + connector/retriever init). This is acceptable for
the UX win of guaranteed cancellation; if it ever becomes a problem we can
add a "pre-warmed spare" pool here without changing the router contract.
"""

from __future__ import annotations

import logging
import multiprocessing as mp
from queue import Empty
from typing import Any, Generator

logger = logging.getLogger(__name__)

# Wire-format tags for items the worker pushes onto its outbound queue.
_ITEM_EVENT = "event"
_ITEM_DONE = "done"
_ITEM_ERROR = "error"

# How often the parent polls the worker's queue. Small enough that a kill()
# from another thread is reflected in events() within a quarter-second.
_QUEUE_POLL_S = 0.25

# Grace window between SIGTERM and SIGKILL. The worker has no cleanup that
# blocks for long, so a second is plenty.
_TERMINATE_GRACE_S = 1.0


def _worker_main(question: str, queue: "mp.Queue[tuple[str, Any]]") -> None:
    """Subprocess entry point — build agent inputs and stream events back.

    Imports happen here (not at module top) so the parent process doesn't
    pay the nemo_retriever import cost at server boot just to make this
    module importable. The subprocess builds its own retriever/connector
    singletons; the parent's instances are not shared across the process
    boundary (DB pools, sockets, etc. don't survive pickling anyway).
    """

    try:
        from nemo_retriever.tabular_data.retrieval.text_to_sql.main import (
            stream_agent_response,
        )

        from gsf.server.chat.helpers import get_connector, get_retriever
        from gsf.server.chat.settings_dal import (
            fetch_acronyms,
            fetch_custom_prompts,
        )

        payload = {
            "question": question,
            "retriever": get_retriever(),
            "connector": get_connector(),
            "acronyms": fetch_acronyms(),
            "custom_prompts": fetch_custom_prompts(),
        }

        for event in stream_agent_response(payload):
            queue.put((_ITEM_EVENT, event))
    except BaseException as exc:  # noqa: BLE001 — must catch to surface to parent
        # Includes SystemExit raised by terminate() handlers in some libs.
        logger.exception("Agent worker raised")
        try:
            queue.put((_ITEM_ERROR, str(exc)))
        except Exception:  # noqa: BLE001
            # Queue may be closed if parent already cancelled — nothing to do.
            pass
    finally:
        try:
            queue.put((_ITEM_DONE, None))
        except Exception:  # noqa: BLE001
            pass


class AgentWorker:
    """Owns one subprocess running the chat agent.

    Lifecycle:
        worker = AgentWorker()
        worker.start(question)
        for event in worker.events():
            ...
        worker.kill()  # idempotent

    `kill()` is safe to call from any thread — it just sends signals to the
    subprocess and joins, which is what releases the conversation slot when
    a newer request preempts an older one.
    """

    def __init__(self) -> None:
        # `spawn` is the only portable choice here: macOS defaults to spawn
        # since Python 3.8 and `fork` is unsafe in a multi-threaded server
        # (uvicorn worker thread pool, asyncio loop, etc.).
        self._ctx = mp.get_context("spawn")
        self._queue: "mp.Queue[tuple[str, Any]]" = self._ctx.Queue()
        self._proc: mp.process.BaseProcess | None = None

    def start(self, question: str) -> None:
        self._proc = self._ctx.Process(
            target=_worker_main,
            args=(question, self._queue),
            daemon=True,
        )
        self._proc.start()

    def events(self) -> Generator[dict[str, Any] | None, None, None]:
        """Yield agent events (dict) and heartbeat ticks (None).

        ``None`` is yielded whenever the queue is empty so the caller can
        write a no-op SSE comment to the socket. Without that, a long agent
        step with no events keeps the socket write-idle and the server
        only learns the client navigated away after the step finally
        produces output — which can be tens of seconds.

        Raises ``RuntimeError`` if the worker reported an internal error so
        the router can surface a single error event to the client.
        """

        proc = self._proc
        if proc is None:
            return

        while True:
            try:
                tag, value = self._queue.get(timeout=_QUEUE_POLL_S)
            except Empty:
                # No item this tick. If the worker has exited (e.g. killed
                # by a newer request) we'd otherwise spin forever — bail.
                if not proc.is_alive():
                    return
                yield None  # heartbeat — see docstring
                continue
            if tag == _ITEM_DONE:
                return
            if tag == _ITEM_ERROR:
                raise RuntimeError(value)
            yield value  # _ITEM_EVENT

    def kill(self) -> None:
        """Terminate the worker if it's still alive. Idempotent."""

        proc = self._proc
        if proc is None or not proc.is_alive():
            return
        proc.terminate()
        proc.join(timeout=_TERMINATE_GRACE_S)
        if proc.is_alive():
            # SIGTERM ignored (rare — but possible if a C extension swallows
            # the signal). Fall back to SIGKILL which the OS guarantees.
            proc.kill()
            proc.join(timeout=_TERMINATE_GRACE_S)
