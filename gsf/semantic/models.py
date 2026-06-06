"""Pydantic DTOs for LLM structured I/O — not an in-memory graph store."""

from __future__ import annotations

from pydantic import BaseModel, Field


class TermProposal(BaseModel):
    """LLM output: one business Term for a physical table."""

    name: str = Field(..., description="CamelCase business Term name.")
    description: str = Field(default="", description="Short business definition.")
    is_a_parent: str | None = Field(
        default=None, description="Parent Term name for IS_A, if applicable."
    )
    part_of_target: str | None = Field(
        default=None, description="Container Term name for PART_OF, if applicable."
    )


class BusinessQuestionsResult(BaseModel):
    """LLM output: simple two-entity business questions for VDB discovery."""

    questions: list[str] = Field(default_factory=list)
    entities: list[str] = Field(default_factory=list)


class SeedSelectionResult(BaseModel):
    """LLM output: single seed table for BFS."""

    table_name: str = Field(..., description="Physical table name.")
    rationale: str = Field(default="")


class ColumnAttributeSpec(BaseModel):
    """Deterministic column → ColumnAttribute mapping (pre-write)."""

    source_column: str
    name: str
    datatype: str = ""
    description: str | None = None
