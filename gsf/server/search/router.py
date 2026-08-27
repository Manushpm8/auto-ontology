# SPDX-FileCopyrightText: Copyright (c) 2024-2026, NVIDIA CORPORATION & AFFILIATES.
# All rights reserved.
# SPDX-License-Identifier: Apache-2.0

"""API route handlers for global discovery search."""

from __future__ import annotations

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel, Field

from gsf.server.responses import (
    DiscoveryCountResponse,
    DiscoverySearchListResponse,
)
from gsf.server.search import service as search_service

router = APIRouter()


class DiscoveryFilters(BaseModel):
    description: bool = False
    objects: list[str] | None = None


class DiscoverySearchRequest(BaseModel):
    search_term: str = Field(min_length=0)
    text_match_option: str = search_service.TEXT_MATCH_CONTAINS
    filters: DiscoveryFilters = Field(default_factory=DiscoveryFilters)


def _run(
    payload: DiscoverySearchRequest,
    *,
    count: bool,
) -> dict:
    try:
        kwargs = {
            "search_term": payload.search_term,
            "text_match_option": payload.text_match_option,
            "objects": payload.filters.objects,
            "include_description": payload.filters.description,
        }
        if count:
            return search_service.discovery_count(**kwargs)
        return search_service.discovery_search(**kwargs)
    except search_service.SearchValidationError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc


@router.post("/search/discovery", response_model=DiscoverySearchListResponse)
def post_discovery_search(payload: DiscoverySearchRequest) -> dict:
    """Fulltext discovery across catalog and semantic nodes.

    Requires at least two characters after trimming. ``contains`` is the only
    match option. Results are capped at 200 and ranked so names that contain
    the original term sort first.
    """
    return _run(payload, count=False)


@router.post("/search/discovery/count", response_model=DiscoveryCountResponse)
def post_discovery_count(payload: DiscoverySearchRequest) -> dict:
    """Hit counts by discovery type for the same query as ``/search/discovery``."""
    return _run(payload, count=True)
