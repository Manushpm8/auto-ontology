# SPDX-FileCopyrightText: Copyright (c) 2024-2026, NVIDIA CORPORATION & AFFILIATES.
# All rights reserved.
# SPDX-License-Identifier: Apache-2.0

"""API routes for UI-managed database connections."""

from __future__ import annotations

import logging
from typing import Any

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel, Field

from gsf.server.connections import dal, service

logger = logging.getLogger(__name__)

router = APIRouter()


class ConnectionPublic(BaseModel):
    id: str
    type: str
    database_name: str


class ConnectionDatabase(BaseModel):
    db_name: str = Field(alias="databaseName")

    model_config = {"populate_by_name": True}


class ConnectionCreate(BaseModel):
    type: str
    connection_string: str = Field(default="", alias="connectionString")
    database: ConnectionDatabase

    model_config = {"populate_by_name": True}


def _public_connections(rows: list[dict[str, Any]]) -> list[dict[str, Any]]:
    return [ConnectionPublic.model_validate(row).model_dump() for row in rows]


class ConnectionTest(BaseModel):
    type: str
    connection_string: str = Field(default="", alias="connectionString")

    model_config = {"populate_by_name": True}


@router.get("/connections")
def list_connections() -> dict:
    rows = _public_connections(dal.list_connections())
    return {"data": rows, "count": len(rows)}


@router.post("/connections/test")
def test_connection(body: ConnectionTest) -> dict:
    try:
        service.test_connection(body.type, body.connection_string)
    except ValueError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc
    except Exception as exc:
        raise HTTPException(
            status_code=422,
            detail=f"Connection test failed: {exc}",
        ) from exc
    return {"success": True}


@router.post("/connections", status_code=201)
def create_connection(body: ConnectionCreate) -> dict:
    try:
        row = service.create_connection(
            connection_type=body.type,
            connection_string=body.connection_string,
            database=body.database.model_dump(by_alias=False),
        )
    except ValueError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc
    except Exception as exc:
        raise HTTPException(
            status_code=422,
            detail=f"Failed to create connection: {exc}",
        ) from exc

    return {"data": ConnectionPublic.model_validate(row).model_dump()}


@router.delete("/connections/{connection_id}")
def delete_connection(connection_id: str) -> dict:
    try:
        row = service.delete_connection(connection_id)
    except ValueError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc
    except Exception as exc:
        raise HTTPException(
            status_code=422,
            detail=f"Failed to delete connection: {exc}",
        ) from exc

    if row is None:
        raise HTTPException(status_code=404, detail="Connection not found")

    return {"data": row}
