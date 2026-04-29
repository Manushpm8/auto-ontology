"""Ingestion service.

Runs ``ingest()`` once at startup, then every 24 hours at the same wall-clock
time (anchored to startup) — independent of how long each run takes.

Usage::

    PYTHONPATH=gsf uv run --no-sync python gsf/ingestion-service/main.py
"""

from __future__ import annotations

import asyncio
import logging
from datetime import datetime, timedelta, timezone

logger = logging.getLogger("gsf.ingestion-service")

INGEST_INTERVAL = timedelta(hours=24)


async def ingest() -> None:
    """Run one ingestion pass.

    Stub — replace with the real ingestion call (e.g. invoking the
    ``scripts.ingest_local_postgres`` flow against the configured sources).
    """
    logger.info("ingest: starting")
    await asyncio.sleep(0)
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
        logger.info("ingestion-service: shutting down")


if __name__ == "__main__":
    main()
