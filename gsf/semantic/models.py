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
    is_a_parent: str | None = Field(
        default=None, description="Parent Term name for IS_A, if applicable."
    )
    part_of_target: str | None = Field(
        default=None, description="Container Term name for PART_OF, if applicable."
    )
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
    is_a_parent: str | None = None
    part_of_target: str | None = None
    attributes: list[TermAttributeAssignment] = Field(default_factory=list)


class TableTermsResult(BaseModel):
    """Sanitized Terms and column assignments for a single physical table."""

    terms: list[TermProposal] = Field(...)


class BusinessQuestionItem(BaseModel):
    """One business question with its target entity and ontology ROLE for finalize."""

    model_config = ConfigDict(extra="forbid")

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

    model_config = ConfigDict(extra="forbid")

    items: list[BusinessQuestionItem] = Field(
        default_factory=list,
        description="Exactly 3 items — one per business angle.",
    )


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


class SingleHopJoin(BaseModel):
    """LLM output: whether two tables can be joined in one hop."""

    model_config = ConfigDict(extra="forbid")

    possible: bool = Field(
        ..., description="True when a direct key join can be inferred."
    )
    src_column: str = Field(
        default="", description="Source table column used for the join."
    )
    tgt_column: str = Field(default="", description="Target table PK column joined to.")
    rationale: str = Field(default="", description="Brief reasoning.")


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
