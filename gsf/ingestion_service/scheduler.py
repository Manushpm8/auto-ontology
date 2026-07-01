# SPDX-FileCopyrightText: Copyright (c) 2024-2026, NVIDIA CORPORATION & AFFILIATES.
# All rights reserved.
# SPDX-License-Identifier: Apache-2.0

"""Interval scheduler shared by the ingestion service's background jobs.

A scheduler runs its job once at startup, then again every ``interval``
measured from the *end* of the previous run. An API-triggered run happens
immediately and, because the loop always reschedules from "now" after any run,
resets that interval timer.

Subclasses implement :meth:`_run_once`.
"""

from __future__ import annotations

import asyncio
import logging
from datetime import datetime, timedelta, timezone

logger = logging.getLogger(__name__)

# Upper bound on a single sleep chunk so shutdown/trigger events are observed
# promptly even while waiting out a full interval.
_WAIT_CHUNK_SECONDS = 500


class IntervalScheduler:
    """Runs a job at startup, on a fixed interval, and on demand."""

    #: Short label used as a log prefix; overridden by subclasses.
    name = "scheduler"

    def __init__(self, interval: timedelta) -> None:
        self._interval = interval
        self._stop = asyncio.Event()
        self._trigger = asyncio.Event()
        self._task: asyncio.Task[None] | None = None

    def start(self) -> None:
        """Launch the background loop; runs one pass immediately."""
        if self._task is None:
            self._task = asyncio.create_task(self._run_forever())

    async def stop(self) -> None:
        """Signal the loop to exit and wait for the in-flight wait to unwind."""
        self._stop.set()
        if self._task is not None:
            await self._task

    def trigger(self) -> None:
        """Request an immediate run without blocking the caller.

        The run happens on the scheduler's own task; when it finishes the loop
        reschedules the next automatic run one interval later.
        """
        self._trigger.set()

    async def _run_once(self) -> None:
        """Perform one pass of the job. Implemented by subclasses."""
        raise NotImplementedError

    async def _wait_for_next(self, next_run: datetime) -> None:
        """Sleep until ``next_run``, returning early on stop or trigger."""
        while not self._stop.is_set() and not self._trigger.is_set():
            remaining = (next_run - datetime.now(timezone.utc)).total_seconds()
            if remaining <= 0:
                return
            waiters = [
                asyncio.create_task(self._stop.wait()),
                asyncio.create_task(self._trigger.wait()),
            ]
            try:
                await asyncio.wait(
                    waiters,
                    timeout=min(remaining, _WAIT_CHUNK_SECONDS),
                    return_when=asyncio.FIRST_COMPLETED,
                )
            finally:
                for w in waiters:
                    w.cancel()

    async def _run_forever(self) -> None:
        # Run once at startup so a fresh deploy refreshes existing connections
        # without waiting for the next scheduled slot.
        try:
            await self._run_once()
        except Exception:
            logger.exception("%s: unhandled error; will retry on next tick", self.name)

        while not self._stop.is_set():
            # Schedule the next run one interval out from now, so a long run or a
            # manual trigger naturally pushes the next automatic run back.
            next_run = datetime.now(timezone.utc) + self._interval
            logger.info("%s: next run at %s", self.name, next_run.isoformat())
            await self._wait_for_next(next_run)
            if self._stop.is_set():
                break

            # A trigger fired (or the interval elapsed) — consume it and run now.
            self._trigger.clear()
            try:
                await self._run_once()
            except Exception:
                logger.exception(
                    "%s: unhandled error; will retry on next tick", self.name
                )
