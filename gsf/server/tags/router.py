# SPDX-FileCopyrightText: Copyright (c) 2026, NVIDIA CORPORATION & AFFILIATES.
# All rights reserved.
# SPDX-License-Identifier: Apache-2.0

"""API route handlers for tags."""

from __future__ import annotations

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel

from gsf.dal import tags as dal
from gsf.server.responses import IdResponse, TagListResponse, TagResponse

router = APIRouter()

#: Longest name a tag may have, counted after trimming. Mirrored by the create
#: dialog, which shows it as "(Max. 25)" and stops typing there — this is the
#: half that a caller bypassing the UI still meets.
MAX_TAG_NAME_LENGTH = 25


class TagCreate(BaseModel):
    name: str


@router.get("/tags", response_model=TagListResponse)
def list_tags() -> dict:
    """Return every tag."""
    rows = dal.list_tags()
    return {"data": rows, "count": len(rows)}


@router.post("/tags", status_code=201, response_model=TagResponse)
def create_tag(body: TagCreate) -> dict:
    """Create a tag.

    Surrounding whitespace is trimmed before anything else, so the name that is
    length-checked, uniqueness-checked and stored is the one a reader sees.
    """
    name = body.name.strip()
    if name == "":
        raise HTTPException(status_code=400, detail="Tag name is required")
    if len(name) > MAX_TAG_NAME_LENGTH:
        raise HTTPException(
            status_code=400,
            detail=f"Tag name must be at most {MAX_TAG_NAME_LENGTH} characters",
        )

    try:
        row = dal.create_tag(name=name)
    except ValueError as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc
    return {"data": row}


@router.delete("/tags/{tag_id}", response_model=IdResponse)
def delete_tag(tag_id: str) -> dict:
    """Delete one tag by id.

    404 rather than 204 for an id that is not there: the settings page deletes
    from a list it has already read, so a missing tag means its list is stale,
    and answering "done" would leave the row on screen with nothing to explain
    it.
    """
    if not dal.delete_tag(tag_id):
        raise HTTPException(status_code=404, detail=f"Tag {tag_id!r} not found")
    return {"data": {"id": tag_id}}
