"""Pydantic DTOs for LLM structured I/O — not an in-memory graph store."""

from __future__ import annotations

from pydantic import BaseModel, ConfigDict, Field


class TermColumnRef(BaseModel):
    """LLM output: column assignment with a user-friendly display label."""

    model_config = ConfigDict(extra="forbid")

    source_column: str = Field(
        ...,
        description="Physical column name — must match a candidate column.",
    )
    display_name: str = Field(
        ...,
        description=(
            "User-friendly ColumnAttribute label with spaces between words "
            "(e.g. Order Date, Total Amount)."
        ),
    )


class TermAttributeAssignment(BaseModel):
    """Resolved column assignment on a Term."""

    source_column: str
    display_name: str


class RawTermProposal(BaseModel):
    """LLM output: one business Term with column assignments only."""

    model_config = ConfigDict(extra="forbid")

    name: str = Field(
        ...,
        description=(
            "User-friendly business Term name with spaces between words "
            "(e.g. Purchase Order)."
        ),
    )
    description: str = Field(default="", description="Short business definition.")
    attributes: list[TermColumnRef] = Field(
        default_factory=list,
        description=(
            "Candidate columns assigned to this Term with user-friendly display names."
        ),
    )


class RawTableTermsResult(BaseModel):
    """LLM output: Terms and column assignments for a single physical table."""

    model_config = ConfigDict(extra="forbid")

    terms: list[RawTermProposal] = Field(
        ...,
        description=(
            "Usually one Term. Propose multiple only when columns clearly belong "
            "to distinct business concepts."
        ),
    )


class TermProposal(BaseModel):
    """Sanitized Term with resolved ColumnAttribute assignments."""

    name: str
    description: str = ""
    attributes: list[TermAttributeAssignment] = Field(default_factory=list)


class TableTermsResult(BaseModel):
    """Sanitized Terms and column assignments for a single physical table."""

    terms: list[TermProposal] = Field(...)


class SeedSelectionResult(BaseModel):
    """LLM output: single seed table for BFS."""

    model_config = ConfigDict(extra="forbid")

    table_name: str = Field(..., description="Physical table name.")
    rationale: str = Field(default="")


class ColumnAttributeSpec(BaseModel):
    """Column candidate for Term assignment; display_name set by extract_term."""

    source_column: str
    name: str
    display_name: str = ""
    datatype: str = ""
    description: str | None = None


class PotentialFkSuggestion(BaseModel):
    """One column the LLM suspects is a foreign key."""

    model_config = ConfigDict(extra="forbid")

    column_name: str = Field(
        ...,
        description="Physical column name that likely references another table.",
    )
    rationale: str = Field(
        default="",
        description="Brief reason this column looks like a foreign key.",
    )


class PotentialFkResult(BaseModel):
    """LLM output: columns that may be FKs but lack graph FOREIGN_KEY edges."""

    model_config = ConfigDict(extra="forbid")

    suggestions: list[PotentialFkSuggestion] = Field(
        default_factory=list,
        description="Suspected FK columns; empty when none apply.",
    )


class FkHitSelection(BaseModel):
    """LLM output: selects the best matching Column hit from a VDB result list."""

    model_config = ConfigDict(extra="forbid")

    hit_index: int | None = Field(
        ...,
        description=(
            "0-based index of the VDB hit that is the primary-key column this FK "
            "references, or null if none of the hits are a plausible match."
        ),
    )
    rationale: str = Field(default="")
