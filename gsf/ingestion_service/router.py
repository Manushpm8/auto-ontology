# SPDX-FileCopyrightText: Copyright (c) 2024-2026, NVIDIA CORPORATION & AFFILIATES.
# All rights reserved.
# SPDX-License-Identifier: Apache-2.0

"""HTTP API routes for the ingestion service."""

from __future__ import annotations

from typing import Any

from fastapi import APIRouter, Body
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
