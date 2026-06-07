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


class BusinessQuestionItem(BaseModel):
    """One business question with its target entity and ontology ROLE for finalize."""

    question: str = Field(..., description="Plain question text — no prefixes.")
    entity: str = Field(
        ...,
        description=(
            "CamelCase business entity Term referenced in the question — never the "
            "anchor Term of the current table."
        ),
    )
    role: str = Field(
        ...,
        description=(
            "camelCase ROLE edge name from the anchor Term to this entity Term "
            "(e.g. placedBy, belongsTo, shippedVia)."
        ),
    )


class BusinessQuestionsResult(BaseModel):
    """LLM output: paired questions, entities, and ROLE names for VDB + finalize."""

    items: list[BusinessQuestionItem] = Field(
        default_factory=list,
        description="Exactly 3 items — one per business angle.",
    )


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
