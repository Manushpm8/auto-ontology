# SPDX-FileCopyrightText: Copyright (c) 2024-2026, NVIDIA CORPORATION & AFFILIATES.
# All rights reserved.
# SPDX-License-Identifier: Apache-2.0

"""Recurring scheduler for semantic compilation.

Runs :func:`run_semantic_compilation` for every configured connection once at
startup, then again every 24h measured from the end of the previous run. An
API-triggered run also resets that 24h timer, so the next automatic run is
always one interval after the most recent run — scheduled or manual.

Every pass re-checks the ``semantic_compilation_enabled`` settings flag and
no-ops when it is off, so disabling in the UI takes effect on the next run
(scheduled or triggered) without waiting for a service restart.
"""

from __future__ import annotations

import asyncio
import logging
from datetime import timedelta

from gsf.ingestion_service.config import is_semantic_compilation_enabled
from gsf.ingestion_service.connections import resolve_database_names
from gsf.ingestion_service.scheduler import IntervalScheduler
from gsf.semantic.compile import run_semantic_compilation

logger = logging.getLogger(__name__)

# Cadence measured from the end of the previous run rather than a fixed
# wall-clock time.
SEMANTIC_INTERVAL = timedelta(hours=24)


class SemanticScheduler(IntervalScheduler):
    """Drives semantic compilation on startup, on a 24h timer, and on demand."""

    name = "semantic"

    def __init__(self, interval: timedelta = SEMANTIC_INTERVAL) -> None:
        super().__init__(interval)

    async def _run_once(self) -> None:
        # Re-check the settings flag on every pass so a disable is honored live:
        # the loop keeps ticking but no-ops until re-enabled, rather than running
        # until the next service restart. Mirrors the lifespan startup gate.
        if not is_semantic_compilation_enabled():
            logger.info("semantic: compilation disabled in settings; skipping run")
            return

        databases = resolve_database_names()
        if not databases:
            logger.info(
                "semantic: no connections configured; nothing to compile. "
                "Add a connection in Settings → Connections."
            )
            return

        logger.info("semantic: starting (%d database(s))", len(databases))
        for database_name in databases:
            try:
                tables_processed = await asyncio.to_thread(
                    run_semantic_compilation, database_name
                )
                logger.info(
                    "Finished semantic compilation for database %s: %d tables processed",
                    database_name,
                    tables_processed,
                )
            except Exception:
                logger.exception("semantic: failed for database %s", database_name)
        logger.info("semantic: finished")
