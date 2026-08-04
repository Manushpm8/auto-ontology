# SPDX-FileCopyrightText: Copyright (c) 2026, NVIDIA CORPORATION & AFFILIATES.
# All rights reserved.
# SPDX-License-Identifier: Apache-2.0

"""Pydantic models for the entity-coverage pipeline."""

from __future__ import annotations

from typing import Any

from pydantic import BaseModel, ConfigDict, Field


class QuestionExtractionModel(BaseModel):
    """Combined sanitize + entity extraction structured output."""

    model_config = ConfigDict(extra="forbid")

    sanitized_question: str = Field(
        ...,
        description=(
            "Concise, SQL-ready rewrite of the user's request. "
            "Preserve factual constraints and remove narrative fluff."
        ),
    )
    required_entity_name: list[str] = Field(
        ...,
        description=(
            "Concepts explicitly mentioned in the question that refer to "
            "database entities. Only extract what the question actually says. "
            "When words describe a single filterable item, keep them in one "
            "phrase instead of splitting. Keep brand names, product names, and "
            "descriptive named constants; omit numeric literals, dates, and "
            "number-based thresholds."
        ),
    )


class EntityCoverageResponse(BaseModel):
    """Final response for the entity-coverage endpoint."""

    model_config = ConfigDict(extra="forbid")

    coverage: float = Field(..., ge=0.0, le=1.0)
    candidates: list[dict[str, Any]]


__all__ = [
    "QuestionExtractionModel",
    "EntityCoverageResponse",
]
