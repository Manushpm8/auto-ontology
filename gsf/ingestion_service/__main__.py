# SPDX-FileCopyrightText: Copyright (c) 2024-2026, NVIDIA CORPORATION & AFFILIATES.
# All rights reserved.
# SPDX-License-Identifier: Apache-2.0

"""Ingestion service.

Runs ``ingest()`` once at startup, then every 24 hours at the same wall-clock
time (anchored to startup) — independent of how long each run takes.

Reloads connections from Neo4j on every pass so newly added connections are
picked up without restarting this process.

Usage::

    uv run --no-sync python gsf/ingestion_service/main.py
"""

from __future__ import annotations

import asyncio
import logging
import os
from datetime import datetime, timedelta, timezone

from gsf.ingestion_service.ingest import run_ingest
from gsf.server.connections import dal as connections_dal

logger = logging.getLogger("gsf.ingestion_service")

INGEST_INTERVAL = timedelta(hours=24)


async def ingest() -> None:
    """Run one ingestion pass for all configured connections."""
    connections = connections_dal.list_connections_for_ingest()
    if not connections:
        raw = os.environ.get("CONNECTION_STRINGS", "")
        connections = [(None, cs.strip()) for cs in raw.split(",") if cs.strip()]
    if not connections:
        logger.info(
            "ingest: no connections configured. "
            "Add a connection in Settings → Connections or set CONNECTION_STRINGS in your .env."
        )
        return

    logger.info("ingest: starting (%s connection(s))", len(connections))
    for connection_id, connection_string in connections:
        try:
            run_ingest(connection_string)
        except Exception:
            logger.exception(
                "ingest: failed for connection %s",
                connection_id or connection_string,
            )
    logger.info("ingest: finished")


async def _run_forever() -> None:
    next_run = datetime.now(timezone.utc)
    while True:
        delay = (next_run - datetime.now(timezone.utc)).total_seconds()
        if delay > 0:
            logger.info(
                "ingest: next run at %s (in %.0fs)", next_run.isoformat(), delay
            )
            await asyncio.sleep(delay)

        try:
            await ingest()
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
    try:
        asyncio.run(_run_forever())
    except KeyboardInterrupt:
        logger.info("ingestion_service: shutting down")
        raise SystemExit(0)


if __name__ == "__main__":
    main()
