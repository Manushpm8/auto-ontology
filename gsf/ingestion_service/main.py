# SPDX-FileCopyrightText: Copyright (c) 2024-2026, NVIDIA CORPORATION & AFFILIATES.
# All rights reserved.
# SPDX-License-Identifier: Apache-2.0

"""Ingestion service.

Runs ``ingest()`` once at startup, then every 24 hours at the same wall-clock
time (anchored to startup) — independent of how long each run takes.

Usage::

    uv run --no-sync python gsf/ingestion_service/main.py
"""

from __future__ import annotations

import asyncio
import logging
from datetime import datetime, timedelta, timezone
from gsf.ingestion_service.ingest import run_ingest
import os

logger = logging.getLogger("gsf.ingestion_service")

INGEST_INTERVAL = timedelta(hours=24)


async def ingest(connection_strings: list[str]) -> None:
    """Run one ingestion pass."""
    logger.info("ingest: starting")
    for connection_string in connection_strings:
        logger.info(f"ingest: starting for {connection_string}")
        run_ingest(connection_string)
    logger.info("ingest: finished")


async def _run_forever(connection_strings: list[str]) -> None:
    next_run = datetime.now(timezone.utc)
    while True:
        delay = (next_run - datetime.now(timezone.utc)).total_seconds()
        if delay > 0:
            logger.info(
                "ingest: next run at %s (in %.0fs)", next_run.isoformat(), delay
            )
            await asyncio.sleep(delay)

        try:
            await ingest(connection_strings)
        except Exception:
            logger.exception("ingest: unhandled error; will retry on next tick")

        # Schedule the next run at the same wall-clock time. If a run overran
        # the interval (or the host was suspended), skip past missed slots so
        # we don't burst-fire to catch up.
        next_run += INGEST_INTERVAL
        now = datetime.now(timezone.utc)
        while next_run <= now:
            logger.warning(
                "ingest: missed scheduled slot at %s, skipping", next_run.isoformat()
            )
            next_run += INGEST_INTERVAL


def main() -> None:
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s %(levelname)s %(name)s: %(message)s",
    )
    connection_strings = os.environ.get("CONNECTION_STRINGS", "").split(",")
    if len(connection_strings) > 0:
        try:
            asyncio.run(_run_forever(connection_strings))
        except KeyboardInterrupt:
            logger.info("ingestion_service: shutting down")
    else:
        logger.warning("No connection strings provided")


if __name__ == "__main__":
    main()
