# SPDX-FileCopyrightText: Copyright (c) 2024-2026, NVIDIA CORPORATION & AFFILIATES.
# All rights reserved.
# SPDX-License-Identifier: Apache-2.0

"""Read ingestion-service feature flags from the GSF metadata DB.

The frontend manages these via Prisma (the ``configurations`` key/value table in
the same Postgres instance the backend uses). We read them here with psycopg so
the ingestion service can gate behaviour on them without a frontend round-trip.
"""

from __future__ import annotations

import logging
from datetime import datetime, timezone

import psycopg

from gsf.infra.postgres import get_postgres_connection_string

logger = logging.getLogger(__name__)

# Key stored in the ``configurations`` table by the Semantic Compilation
# settings tab. Its value is the string ``"true"`` or ``"false"``.
SEMANTIC_COMPILATION_ENABLED_KEY = "semantic_compilation_enabled"

# Key holding the UTC timestamp (ISO 8601, with offset) of the last semantic
# compilation pass that ran to completion. Written by the scheduler, read by
# the settings page via the server's status route.
SEMANTIC_COMPILATION_LAST_SUCCESS_KEY = "semantic_compilation_last_success_at"


def is_semantic_compilation_enabled() -> bool:
    """Return whether semantic compilation is enabled in settings.

    Best-effort: any DB error (including the table not existing yet) is treated
    as "disabled" so a missing/unreachable config never crashes startup.
    """
    try:
        with psycopg.connect(
            get_postgres_connection_string(), connect_timeout=3
        ) as conn:
            with conn.cursor() as cur:
                cur.execute(
                    "SELECT value FROM configurations WHERE key = %s",
                    (SEMANTIC_COMPILATION_ENABLED_KEY,),
                )
                row = cur.fetchone()
    except Exception:
        logger.exception(
            "Failed to read %s; treating semantic compilation as disabled",
            SEMANTIC_COMPILATION_ENABLED_KEY,
        )
        return False

    return bool(row) and str(row[0]).strip().lower() == "true"


def record_semantic_compilation_success() -> None:
    """Persist "now" (UTC, with offset) as the last successful compilation pass.

    Best-effort: a DB error here must not fail the compilation pass itself, so
    it is logged and swallowed rather than raised.
    """
    timestamp = datetime.now(timezone.utc).isoformat()
    try:
        with psycopg.connect(
            get_postgres_connection_string(), connect_timeout=3
        ) as conn:
            with conn.cursor() as cur:
                cur.execute(
                    """
                    INSERT INTO configurations (key, value, updated_at)
                    VALUES (%s, %s, now())
                    ON CONFLICT (key) DO UPDATE
                        SET value = EXCLUDED.value, updated_at = now()
                    """,
                    (SEMANTIC_COMPILATION_LAST_SUCCESS_KEY, timestamp),
                )
            conn.commit()
    except Exception:
        logger.exception("Failed to record %s", SEMANTIC_COMPILATION_LAST_SUCCESS_KEY)


def get_semantic_compilation_last_success() -> str | None:
    """Return the ISO 8601 timestamp of the last successful pass, if any.

    Best-effort: any DB error is treated as "unknown" rather than raised, so a
    missing/unreachable config never breaks the status endpoint.
    """
    try:
        with psycopg.connect(
            get_postgres_connection_string(), connect_timeout=3
        ) as conn:
            with conn.cursor() as cur:
                cur.execute(
                    "SELECT value FROM configurations WHERE key = %s",
                    (SEMANTIC_COMPILATION_LAST_SUCCESS_KEY,),
                )
                row = cur.fetchone()
    except Exception:
        logger.exception("Failed to read %s", SEMANTIC_COMPILATION_LAST_SUCCESS_KEY)
        return None

    return str(row[0]) if row else None
