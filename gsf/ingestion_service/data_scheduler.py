# SPDX-FileCopyrightText: Copyright (c) 2024-2026, NVIDIA CORPORATION & AFFILIATES.
# All rights reserved.
# SPDX-License-Identifier: Apache-2.0

"""Recurring scheduler for data ingestion.

Runs :func:`run_ingest` for every configured connection once at startup, then
again every 24h measured from the end of the previous run. Connections are
reloaded on every pass so newly added ones are picked up without a restart.
"""

from __future__ import annotations

import asyncio
import logging
import os
from datetime import timedelta

from gsf.connectors.connection_string_factory import build_connection_string
from gsf.dal.connections import list_connections
from gsf.ingestion_service.ingest import run_ingest
from gsf.ingestion_service.scheduler import IntervalScheduler

logger = logging.getLogger(__name__)

# Cadence measured from the end of the previous run rather than a fixed
# wall-clock time.
INGEST_INTERVAL = timedelta(hours=24)


def _connection_strings() -> list[str]:
    """Resolve the connection strings to ingest for the current pass."""
    raw = os.environ.get("CONNECTION_STRINGS", "")
    connections = [cs.strip() for cs in raw.split(",") if cs.strip()]
    if connections:
        logger.info("ingest: using connections from CONNECTION_STRINGS")
        return connections

    try:
        return [build_connection_string(conn) for conn in list_connections()]
    except Exception:
        logger.exception("ingest: failed to load connections from Neo4j")
        return []


class DataScheduler(IntervalScheduler):
    """Drives data ingestion on startup, on a 24h timer, and on demand."""

    name = "ingest"

    def __init__(self, interval: timedelta = INGEST_INTERVAL) -> None:
        super().__init__(interval)

    async def _run_once(self) -> None:
        connections = _connection_strings()
        if not connections:
            logger.info(
                "ingest: no connections configured. "
                "Add a connection in Settings → Connections or set "
                "CONNECTION_STRINGS in your .env."
            )
            return

        logger.info("ingest: starting (%s connection(s))", len(connections))
        for connection_string in connections:
            try:
                await asyncio.to_thread(run_ingest, connection_string)
            except Exception:
                logger.exception("ingest: failed for connection %s", connection_string)
        logger.info("ingest: finished")
