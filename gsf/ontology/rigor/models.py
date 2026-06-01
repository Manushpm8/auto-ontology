"""Pydantic models for the Rigor ontology construction pipeline.

Defines the core ontology representation (BusinessTerms, Attributes,
ObjectProperties, Metrics), the per-table Delta proposed by the Gen-LLM,
and the JudgeVerdict returned by the Judge-LLM.

Graph model:
    Column -[:HAS_ATTRIBUTE]-> Attribute -[:IS_PROPERTY_OF]-> BusinessTerm
    Not every column becomes an Attribute — FK columns become ObjectProperty
    edges, PK/ID columns are structural, denormalized columns may become
    separate BusinessTerms.
"""

from __future__ import annotations

from enum import Enum
from typing import Literal

from pydantic import BaseModel, Field


# ---------------------------------------------------------------------------
# Provenance — tracks where every ontology element came from
# ---------------------------------------------------------------------------

DerivationType = Literal[
    "declared_fk",
    "implicit_id_pattern",
    "self_referential",
    "denormalized_entity",
    "deterministic",
    "llm_proposed",
    "sql_join_inferred",
    "sql_metric_inferred",
]


class Provenance(BaseModel):
    """Links an ontology element back to its physical source."""

    source_table: str
    source_column: str | None = None
    derivation: DerivationType


# ---------------------------------------------------------------------------
# Core ontology elements
# ---------------------------------------------------------------------------


class BusinessTerm(BaseModel):
    """A business entity node in the ontology (e.g. Customer, Order)."""

    name: str = Field(..., description="CamelCase business term name, e.g. 'Customer'.")
    description: str = Field(
        ..., description="One-sentence description of this business term."
    )
    provenance: list[Provenance] = Field(default_factory=list)
    parent: str | None = Field(
        None, description="Parent term name for SubClassOf hierarchy."
    )


class Attribute(BaseModel):
    """A typed data attribute linked to a source Column and a BusinessTerm.

    Graph: Column -[:HAS_ATTRIBUTE]-> Attribute -[:IS_PROPERTY_OF]-> BusinessTerm
    """

    name: str = Field(..., description="Attribute name, e.g. 'email', 'unitPrice'.")
    datatype: str = Field(
        ..., description="SQL or logical data type, e.g. 'text', 'integer'."
    )
    term_name: str = Field(
        ..., description="Name of the BusinessTerm this attribute belongs to."
    )
    source_column: str = Field(
        ...,
        description="Name of the source Column this attribute is derived from.",
    )
    provenance: Provenance
    description: str | None = Field(
        None, description="Business description of this attribute."
    )
    formula: str | None = Field(
        None, description="Derivation formula if this is a computed column."
    )
    usage_hint: str | None = Field(
        None, description="Guidance on how to use this column in queries/analysis."
    )


class ObjectProperty(BaseModel):
    """A named directed edge between two BusinessTerms (e.g. PLACES)."""

    name: str = Field(
        ...,
        description="Relationship name, e.g. 'places', 'belongsTo', 'reportsTo'.",
    )
    source_term: str
    target_term: str
    provenance: Provenance


class AggregationType(str, Enum):
    SUM = "SUM"
    COUNT = "COUNT"
    AVG = "AVG"
    MAX = "MAX"
    MIN = "MIN"
    OTHER = "OTHER"


class Metric(BaseModel):
    """A business metric derived from SQL aggregation patterns."""

    name: str = Field(
        ..., description="Metric name, e.g. 'TotalRevenue', 'AvgOrderValue'."
    )
    expression: str = Field(..., description="SQL expression, e.g. 'SUM(amount)'.")
    source_tables: list[str] = Field(default_factory=list)
    aggregation_type: AggregationType = AggregationType.OTHER


# ---------------------------------------------------------------------------
# DeltaOntology — proposed by Gen-LLM for one table
# ---------------------------------------------------------------------------


class ProposedBusinessTerm(BaseModel):
    """A business term proposed by the Gen-LLM for a single table."""

    name: str = Field(..., description="CamelCase business term name.")
    description: str = Field(..., description="One-sentence description.")
    parent: str | None = Field(
        None, description="Parent term for SubClassOf, if detected."
    )


class ProposedAttribute(BaseModel):
    """An attribute proposed by the Gen-LLM for a non-FK, non-ID column."""

    name: str = Field(..., description="Attribute name (matches source column name).")
    datatype: str
    term_name: str = Field(
        ..., description="Which proposed business term this belongs to."
    )
    source_column: str = Field(
        ..., description="The source column name this attribute comes from."
    )


class ProposedObjectProperty(BaseModel):
    """An object property (edge) proposed by the Gen-LLM."""

    name: str = Field(..., description="Relationship name, e.g. 'belongsTo'.")
    source_term: str
    target_term: str


# ---------------------------------------------------------------------------
# Column enrichment — LLM output for per-table attribute enrichment
# ---------------------------------------------------------------------------


class EnrichedColumn(BaseModel):
    """LLM-produced enrichment for a single column."""

    source_column: str = Field(
        ..., description="Raw column name (must match input exactly)."
    )
    canonical_name: str = Field(
        ..., description="Business-friendly name, e.g. 'DistrictName' for 'A2'."
    )
    description: str = Field(
        ..., description="What this attribute represents in business terms."
    )
    formula: str | None = Field(
        None,
        description="Derivation formula if computed, e.g. 'revenue - cost'.",
    )
    usage_hint: str | None = Field(
        None,
        description="How to use this column in queries or analysis.",
    )


class ColumnEnrichmentResult(BaseModel):
    """LLM output: enrichment for all non-PK/FK columns of a table."""

    columns: list[EnrichedColumn] = Field(default_factory=list)


class DeltaOntology(BaseModel):
    """The Gen-LLM output: proposed ontology additions for one table."""

    business_terms: list[ProposedBusinessTerm] = Field(default_factory=list)
    attributes: list[ProposedAttribute] = Field(default_factory=list)
    object_properties: list[ProposedObjectProperty] = Field(default_factory=list)


# ---------------------------------------------------------------------------
# JudgeVerdict — returned by Judge-LLM
# ---------------------------------------------------------------------------


class RejectedItem(BaseModel):
    """An element rejected by the Judge-LLM."""

    element_type: Literal["business_term", "attribute", "object_property"]
    name: str
    reason: str


class MergeInstruction(BaseModel):
    """Instruction to merge a proposed business term into an existing one."""

    proposed_name: str = Field(
        ..., description="Name of the newly proposed business term."
    )
    merge_into: str = Field(
        ..., description="Name of the existing business term to merge into."
    )
    reason: str = Field(..., description="Why these should be merged.")


class JudgeVerdict(BaseModel):
    """The Judge-LLM output: validated delta with rejections and merges."""

    approved_business_terms: list[ProposedBusinessTerm] = Field(default_factory=list)
    approved_attributes: list[ProposedAttribute] = Field(default_factory=list)
    approved_object_properties: list[ProposedObjectProperty] = Field(
        default_factory=list
    )
    rejected: list[RejectedItem] = Field(default_factory=list)
    merge_instructions: list[MergeInstruction] = Field(default_factory=list)

    def apply(self, delta: DeltaOntology, source_table: str) -> DeltaOntology:
        """Return a new DeltaOntology with rejections removed and merges
        applied. Does NOT mutate the input."""
        rename_map: dict[str, str] = {
            mi.proposed_name: mi.merge_into for mi in self.merge_instructions
        }
        rejected_names: set[str] = {r.name for r in self.rejected}

        terms = [
            ProposedBusinessTerm(
                name=rename_map.get(t.name, t.name),
                description=t.description,
                parent=t.parent,
            )
            for t in self.approved_business_terms
            if t.name not in rejected_names
        ]

        def _remap(name: str) -> str:
            return rename_map.get(name, name)

        attributes = [
            ProposedAttribute(
                name=attr.name,
                datatype=attr.datatype,
                term_name=_remap(attr.term_name),
                source_column=attr.source_column,
            )
            for attr in self.approved_attributes
            if attr.name not in rejected_names
        ]

        obj_props = [
            ProposedObjectProperty(
                name=op.name,
                source_term=_remap(op.source_term),
                target_term=_remap(op.target_term),
            )
            for op in self.approved_object_properties
            if op.name not in rejected_names
        ]

        return DeltaOntology(
            business_terms=terms,
            attributes=attributes,
            object_properties=obj_props,
        )


# ---------------------------------------------------------------------------
# CoreOntology — accumulated state across all tables
# ---------------------------------------------------------------------------


class CoreOntology(BaseModel):
    """The full ontology built incrementally across all tables."""

    business_terms: list[BusinessTerm] = Field(default_factory=list)
    attributes: list[Attribute] = Field(default_factory=list)
    object_properties: list[ObjectProperty] = Field(default_factory=list)
    metrics: list[Metric] = Field(default_factory=list)

    def has_term(self, name: str) -> bool:
        return any(t.name == name for t in self.business_terms)

    def get_term(self, name: str) -> BusinessTerm | None:
        return next((t for t in self.business_terms if t.name == name), None)

    def has_edge(self, source: str, target: str, name: str | None = None) -> bool:
        for op in self.object_properties:
            if op.source_term == source and op.target_term == target:
                if name is None or op.name == name:
                    return True
        return False

    def term_names(self) -> list[str]:
        return [t.name for t in self.business_terms]

    def merge(self, delta: DeltaOntology, source_table: str) -> None:
        """Integrate a validated DeltaOntology into the core ontology."""
        prov = Provenance(source_table=source_table, derivation="llm_proposed")

        for pt in delta.business_terms:
            existing = self.get_term(pt.name)
            if existing:
                existing.provenance.append(prov)
                if pt.description and len(pt.description) > len(existing.description):
                    existing.description = pt.description
            else:
                self.business_terms.append(
                    BusinessTerm(
                        name=pt.name,
                        description=pt.description,
                        provenance=[prov],
                        parent=pt.parent,
                    )
                )

        for attr in delta.attributes:
            if not self.has_term(attr.term_name):
                self.business_terms.append(
                    BusinessTerm(
                        name=attr.term_name,
                        description=f"(auto-created for attribute {attr.name})",
                        provenance=[prov],
                    )
                )
            col_prov = Provenance(
                source_table=source_table,
                source_column=attr.source_column,
                derivation="llm_proposed",
            )
            self.attributes.append(
                Attribute(
                    name=attr.name,
                    datatype=attr.datatype,
                    term_name=attr.term_name,
                    source_column=attr.source_column,
                    provenance=col_prov,
                )
            )

        for op in delta.object_properties:
            if not self.has_edge(op.source_term, op.target_term, op.name):
                self.object_properties.append(
                    ObjectProperty(
                        name=op.name,
                        source_term=op.source_term,
                        target_term=op.target_term,
                        provenance=prov,
                    )
                )

    def snapshot_for_prompt(self) -> str:
        """Compact text representation for LLM context windows."""
        lines: list[str] = []
        if self.business_terms:
            lines.append("## Existing Business Terms")
            for t in self.business_terms:
                parent_tag = f" (subclass of {t.parent})" if t.parent else ""
                lines.append(f"  - {t.name}{parent_tag}: {t.description}")

        if self.object_properties:
            lines.append("\n## Existing Relationships")
            for op in self.object_properties:
                lines.append(
                    f"  - ({op.source_term}) --[{op.name}]--> ({op.target_term})"
                )

        if not lines:
            return "(Ontology is empty — you are building the first business terms.)"

        return "\n".join(lines)


# ---------------------------------------------------------------------------
# Deterministic analysis helpers
# ---------------------------------------------------------------------------


class DenormalizedCandidate(BaseModel):
    """A column flagged as potentially hiding an external entity."""

    column_name: str
    inferred_entity_name: str
    pattern: str = Field(
        ...,
        description="The naming pattern that triggered detection, "
        "e.g. '*_name', '*_type'.",
    )
    column_type: str
