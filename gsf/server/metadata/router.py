# SPDX-FileCopyrightText: Copyright (c) 2026, NVIDIA CORPORATION & AFFILIATES.
# All rights reserved.
# SPDX-License-Identifier: Apache-2.0

"""API routes for grading entity coverage of a free-text question."""

from __future__ import annotations

import logging
import time

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel, Field, field_validator

from gsf.retrieval.entity_coverage.state import DEFAULT_MAX_DISTANCE
from gsf.server.metadata import service as dal
from gsf.server.responses import EntityCoverageResponse, ValueSearchResponse

logger = logging.getLogger(__name__)

router = APIRouter()


class EntityCoverageRequest(BaseModel):
    """Payload for the entity-coverage route."""

    question: str = Field(..., min_length=1)
    max_distance: float = Field(
        default=DEFAULT_MAX_DISTANCE,
        gt=0.0,
        description=(
            "Maximum L2 vector distance for a candidate to count "
            "(lower score = closer match)."
        ),
    )


class ValueSearchRequest(BaseModel):
    """Payload for resolving a described value to one physical column."""

    value: str = Field(..., min_length=1)
    description: str = Field(..., min_length=1)
    database_name: str | None = None

    @field_validator("value", "description")
    @classmethod
    def require_non_blank(cls, value: str) -> str:
        """Trim required strings and reject whitespace-only input."""
        stripped = value.strip()
        if not stripped:
            raise ValueError("must not be blank")
        return stripped

    @field_validator("database_name")
    @classmethod
    def normalize_database_name(cls, value: str | None) -> str | None:
        """Treat a blank optional database name as omitted."""
        return value.strip() or None if value is not None else None


@router.post("/value-search", response_model=ValueSearchResponse)
def find_column_value(body: ValueSearchRequest) -> dict:
    """Return the most likely physical field and its exact stored value."""
    started = time.perf_counter()
    status_code = 200
    found: bool | None = None
    try:
        result = dal.find_column_value(
            value=body.value,
            description=body.description,
            database_name=body.database_name,
        )
        found = result.get("field") is not None
    except dal.ValueSearchUnavailableError as exc:
        status_code = 503
        raise HTTPException(status_code=503, detail=str(exc)) from exc
    except dal.ValueSearchError as exc:
        status_code = 422
        raise HTTPException(status_code=422, detail=str(exc)) from exc
    finally:
        elapsed_ms = int((time.perf_counter() - started) * 1000)
        logger.info(
            "value-search request completed "
            "(database=%s, status=%d, found=%s, elapsed_ms=%d)",
            body.database_name or "auto",
            status_code,
            found,
            elapsed_ms,
        )
    return {"data": result}


@router.post("/question-entity-coverage", response_model=EntityCoverageResponse)
def entity_coverage(body: EntityCoverageRequest) -> dict:
    """Return ranked semantic candidates and a 0–1 entity coverage grade.

    Extracts entities from the question, retrieves semantic candidates, filters
    by vector distance, enriches via Neo4j, and grades how many entities have
    at least one covering ColumnAttribute candidate.

    Returns 422 when the flow cannot produce a result for the question.
    """
    try:
        result = dal.entity_coverage(
            body.question,
            max_distance=body.max_distance,
        )
    except dal.PredictionFlowError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc
    return {"data": result}
