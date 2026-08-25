# SPDX-FileCopyrightText: Copyright (c) 2024-2026, NVIDIA CORPORATION & AFFILIATES.
# All rights reserved.
# SPDX-License-Identifier: Apache-2.0

"""Postgres-backed history of semantic compilation passes.

Unlike the ``configurations`` table (owned by the frontend's Prisma schema),
this table is owned by GSF itself: it is written and read only by Python
services, so its schema is declared and created here rather than in
``frontend/prisma/schema.prisma``.

The scheduler records a start/end row for every pass (see
``semantic_scheduler.py``); the ingestion service's status endpoint reads the
last successful — and, if more recent, the last failed/aborted — one back so
the settings page can show either without a second, frontend-owned source of
truth.

``succeeded`` is a required (``NOT NULL``) string outcome — one of
``RUN_SUCCEEDED``/``RUN_FAILED``/``RUN_ABORTED`` — rather than a nullable
boolean. The row is inserted with the pessimistic default ``RUN_ABORTED``
*before* the pass does any work, and only overwritten once the pass actually
concludes. This means a process that's hard-killed (SIGKILL, OOM, power loss —
anything that skips the ``finally`` in ``semantic_scheduler.py``) leaves a row
that already reads "aborted" instead of an ambiguous ``NULL`` that would sit
there forever looking like it's still running.
"""

from __future__ import annotations

import logging

import psycopg

from gsf.infra.postgres import get_postgres_connection_string

logger = logging.getLogger(__name__)

# The three outcomes a row can end up with. "Aborted" covers both an explicit
# stop/mid-run disable and a row nobody ever got to finish (crash, kill) —
# see the module docstring on why that pessimistic default is set at insert
# time rather than left NULL.
RUN_SUCCEEDED = "succeeded"
RUN_FAILED = "failed"
RUN_ABORTED = "aborted"

_VALID_OUTCOMES = frozenset({RUN_SUCCEEDED, RUN_FAILED, RUN_ABORTED})

_CREATE_TABLE_SQL = f"""
    CREATE TABLE IF NOT EXISTS semantic_compilation_history (
        id BIGSERIAL PRIMARY KEY,
        started_at TIMESTAMPTZ NOT NULL,
        finished_at TIMESTAMPTZ,
        succeeded TEXT NOT NULL DEFAULT '{RUN_ABORTED}'
            CHECK (succeeded IN ('{RUN_SUCCEEDED}', '{RUN_FAILED}', '{RUN_ABORTED}'))
    )
"""


def ensure_history_table() -> None:
    """Create the history table if it doesn't exist yet.

    Called once at ingestion service startup. Best-effort: a failure here
    (e.g. Postgres briefly unreachable) is logged and swallowed rather than
    crashing the service — ``record_run_start``/``record_run_finish`` already
    degrade gracefully (they no-op) when the table isn't there yet.

    Doesn't handle migrating a stale table left over from before ``succeeded``
    became a required string (it used to be a nullable boolean) — that never
    made it past developer machines, so drop it manually if you have one
    lying around locally; a fresh one gets created here with the current
    schema.
    """
    try:
        with psycopg.connect(
            get_postgres_connection_string(), connect_timeout=3
        ) as conn:
            with conn.cursor() as cur:
                cur.execute(_CREATE_TABLE_SQL)
            conn.commit()
    except Exception:
        logger.exception("Failed to ensure semantic_compilation_history exists")


def record_run_start() -> int | None:
    """Insert a row for a pass starting now; returns its id.

    ``succeeded`` takes its column default (``RUN_ABORTED``) so the row is
    already in its pessimistic final state the instant it's inserted — see
    the module docstring.

    Returns ``None`` on failure (best-effort — a DB hiccup must not block a
    compilation pass). Callers should skip ``record_run_finish`` in that case,
    since there is no row to update.
    """
    try:
        with psycopg.connect(
            get_postgres_connection_string(), connect_timeout=3
        ) as conn:
            with conn.cursor() as cur:
                cur.execute(
                    """
                    INSERT INTO semantic_compilation_history (started_at)
                    VALUES (now())
                    RETURNING id
                    """
                )
                row = cur.fetchone()
            conn.commit()
        return int(row[0]) if row else None
    except Exception:
        logger.exception("Failed to record semantic compilation run start")
        return None


def record_run_finish(run_id: int | None, outcome: str) -> None:
    """Mark a previously-started run as finished with *outcome*.

    *outcome* must be one of ``RUN_SUCCEEDED``/``RUN_FAILED``/``RUN_ABORTED``.
    No-ops when *run_id* is ``None`` — either the start was never recorded, or
    the table wasn't reachable at the time. Best-effort like the rest of this
    module: a DB error here must not fail the compilation pass itself.
    """
    if run_id is None:
        return
    if outcome not in _VALID_OUTCOMES:
        raise ValueError(f"invalid semantic compilation run outcome: {outcome!r}")
    try:
        with psycopg.connect(
            get_postgres_connection_string(), connect_timeout=3
        ) as conn:
            with conn.cursor() as cur:
                cur.execute(
                    """
                    UPDATE semantic_compilation_history
                    SET finished_at = now(), succeeded = %s
                    WHERE id = %s
                    """,
                    (outcome, run_id),
                )
            conn.commit()
    except Exception:
        logger.exception(
            "Failed to record semantic compilation run finish (id=%s)", run_id
        )


def get_last_successful_run() -> str | None:
    """Return the ISO 8601 timestamp of the most recently *finished* successful pass.

    Best-effort: any DB error (including the table not existing yet) is
    treated as "unknown" rather than raised, so a missing/unreachable table
    never breaks the status endpoint.
    """
    try:
        with psycopg.connect(
            get_postgres_connection_string(), connect_timeout=3
        ) as conn:
            with conn.cursor() as cur:
                cur.execute(
                    f"""
                    SELECT finished_at FROM semantic_compilation_history
                    WHERE succeeded = '{RUN_SUCCEEDED}' AND finished_at IS NOT NULL
                    ORDER BY finished_at DESC
                    LIMIT 1
                    """
                )
                row = cur.fetchone()
    except Exception:
        logger.exception("Failed to read last successful semantic compilation run")
        return None

    return row[0].isoformat() if row and row[0] is not None else None


def get_last_failure_if_most_recent() -> str | None:
    """Return the ISO 8601 timestamp of the last non-successful pass (failed or
    aborted), but only if it is more recent than the last *successful* one.

    "Non-successful" covers both ``RUN_FAILED`` (the pass ran and raised) and
    ``RUN_ABORTED`` (stopped on request, disabled mid-run, or never got to
    finish at all — see the module docstring). All three read the same to a
    user checking the settings page: compilation isn't in a known-good state.
    Excludes rows still in flight (``finished_at IS NULL``), which read
    ``RUN_ABORTED`` from their pessimistic default but haven't actually ended.

    Scoping to "more recent than the last success" means a stale failure from
    days ago stops being surfaced the moment a later run succeeds, without the
    caller having to compare two timestamps itself.

    Best-effort, like the rest of this module: any DB error is treated as "no
    failure to report" rather than raised.
    """
    try:
        with psycopg.connect(
            get_postgres_connection_string(), connect_timeout=3
        ) as conn:
            with conn.cursor() as cur:
                cur.execute(
                    f"""
                    SELECT finished_at FROM semantic_compilation_history
                    WHERE succeeded != '{RUN_SUCCEEDED}'
                    AND finished_at IS NOT NULL
                    AND finished_at > COALESCE(
                        (
                            SELECT MAX(finished_at) FROM semantic_compilation_history
                            WHERE succeeded = '{RUN_SUCCEEDED}'
                        ),
                        '-infinity'
                    )
                    ORDER BY finished_at DESC
                    LIMIT 1
                    """
                )
                row = cur.fetchone()
    except Exception:
        logger.exception("Failed to read last failed semantic compilation run")
        return None

    return row[0].isoformat() if row and row[0] is not None else None
