# SPDX-FileCopyrightText: Copyright (c) 2024-2026, NVIDIA CORPORATION & AFFILIATES.
# All rights reserved.
# SPDX-License-Identifier: Apache-2.0

"""HTTP API routes for the ingestion service."""

from __future__ import annotations

from typing import Any

from fastapi import APIRouter, Body, Request
from pydantic import BaseModel

from gsf.ingestion_service.ingest import trigger_ingest, trigger_ingest_delete

router = APIRouter()


class DatabaseRef(BaseModel):
    database_name: str


@router.get("/health")
async def health() -> dict[str, str]:
    return {"status": "ok"}


@router.post("/ingest", status_code=202)
async def ingest_connection(connection: dict[str, Any] = Body(...)) -> dict[str, str]:
    """Trigger a non-blocking ingest for a single connection."""
    trigger_ingest(connection)
    return {"status": "accepted"}


@router.post("/ingest/delete", status_code=202)
async def delete_ingest(ref: DatabaseRef) -> dict[str, str]:
    """Trigger a non-blocking teardown of a database's ingested data."""
    trigger_ingest_delete(ref.database_name)
    return {"status": "accepted"}


@router.post("/semantic/compile", status_code=202)
async def trigger_semantic_compile(request: Request) -> dict[str, str]:
    """Trigger a non-blocking semantic compilation pass.

    Ensures the scheduler is running first (``start()`` is idempotent), so this
    also takes effect when compilation is enabled while the service is already
    up — no restart needed. Runs on the scheduler's own task; when it finishes,
    the next automatic run is rescheduled for 24h later.
    """
    scheduler = request.app.state.semantic_scheduler
    # start() performs an immediate startup run on its own. Only ask for an
    # extra run when the scheduler was already running; otherwise the startup
    # run and the trigger would compile twice.
    if not scheduler.start():
        scheduler.trigger()
    return {"status": "accepted"}
