# SPDX-FileCopyrightText: Copyright (c) 2024-2026, NVIDIA CORPORATION & AFFILIATES.
# All rights reserved.
# SPDX-License-Identifier: Apache-2.0

"""Pydantic models for the Phase 0 Domain Pre-Reading pipeline."""

from __future__ import annotations

from pydantic import BaseModel, Field


class ChunkSignals(BaseModel):
    """Structured signals extracted from a single chunk during MAP phase."""

    domain_areas: list[str] = Field(default_factory=list)
    key_entities: list[str] = Field(default_factory=list)
    key_metrics: list[str] = Field(default_factory=list)
    business_rules: list[str] = Field(default_factory=list)
    glossary_hints: list[str] = Field(default_factory=list)
    join_patterns: list[str] = Field(default_factory=list)


class DomainSummary(BaseModel):
    """Compact domain summary produced by the REDUCE phase.

    ~500-1,500 tokens of distilled domain knowledge used as system context
    for downstream Phase 1 attribute extraction calls.
    """

    domains: list[str] = Field(
        default_factory=list,
        description="Distinct business domains present in the corpus",
    )
    core_entities: list[str] = Field(
        default_factory=list,
        description="Primary business entities found in the schema",
    )
    core_metrics: list[str] = Field(
        default_factory=list,
        description="Key business metrics and KPIs",
    )
    business_rules: list[str] = Field(
        default_factory=list,
        description="Implicit rules discovered from query patterns",
    )
    glossary_hints: list[str] = Field(
        default_factory=list,
        description="Column/alias mappings to business terms",
    )
    dominant_join_patterns: list[str] = Field(
        default_factory=list,
        description="Most frequent table join paths with approx. %",
    )
