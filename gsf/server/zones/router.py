# SPDX-FileCopyrightText: Copyright (c) 2024-2026, NVIDIA CORPORATION & AFFILIATES.
# All rights reserved.
# SPDX-License-Identifier: Apache-2.0

"""API route handlers for semantic zones."""

from __future__ import annotations

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel

from gsf.server.zones import dal

router = APIRouter()


class ZoneCreate(BaseModel):
    name: str
    description: str | None = None
    color: str
    items: list[str]


class ZoneUpdate(BaseModel):
    name: str | None = None
    description: str | None = None
    color: str | None = None
    items: list[str] | None = None


def _count_payload(data: object) -> dict:
    if isinstance(data, list):
        return {"data": data, "count": len(data)}
    return {"data": data, "count": 1}


@router.get("/zones")
def list_zones() -> dict:
    """All zones in the catalog."""
    rows = dal.list_zones()
    return _count_payload(rows)


@router.get("/zones/{zone_id}")
def get_zone(zone_id: str) -> dict:
    """One zone with its catalog data items."""
    row = dal.get_zone_by_id(zone_id)
    if row is None:
        raise HTTPException(status_code=404, detail=f"Zone {zone_id!r} not found")
    return {"data": row}


@router.post("/zones", status_code=201)
def create_zone(body: ZoneCreate) -> dict:
    """Create a zone and attach db / schema / table / column items."""
    normalized_name = body.name.strip()
    if normalized_name == "":
        raise HTTPException(status_code=400, detail="Zone name is required")
    if dal.zone_name_exists(normalized_name):
        raise HTTPException(
            status_code=409,
            detail=f"Zone with name {normalized_name!r} already exists",
        )

    row = dal.create_zone(
        name=normalized_name,
        description=body.description,
        color=body.color,
        item_ids=body.items,
    )
    return {"data": row}


@router.patch("/zones/{zone_id}")
def update_zone(zone_id: str, body: ZoneUpdate) -> dict:
    """Update zone fields and optionally replace catalog data items."""
    field_updates: dict[str, str | None] = {}
    provided_fields = body.model_fields_set

    if "name" in provided_fields:
        raw_name = body.name or ""
        normalized_name = raw_name.strip()
        if normalized_name == "":
            raise HTTPException(status_code=400, detail="Zone name is required")
        if dal.zone_name_exists(normalized_name, exclude_id=zone_id):
            raise HTTPException(
                status_code=409,
                detail=f"Zone with name {normalized_name!r} already exists",
            )
        field_updates["name"] = normalized_name

    if "description" in provided_fields:
        normalized_description = (body.description or "").strip()
        field_updates["description"] = normalized_description or None

    if "color" in provided_fields:
        field_updates["color"] = body.color

    row = dal.update_zone(
        zone_id=zone_id,
        updates=field_updates,
        item_ids=body.items if "items" in provided_fields else None,
    )
    if row is None:
        raise HTTPException(status_code=404, detail=f"Zone {zone_id!r} not found")
    return {"data": row}


@router.delete("/zones/{zone_id}")
def delete_zone(zone_id: str) -> dict:
    """Delete one zone by id."""
    if not dal.delete_zone(zone_id):
        raise HTTPException(status_code=404, detail=f"Zone {zone_id!r} not found")
    return {"data": {"id": zone_id}}
