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


class JoinHop(BaseModel):
    """A single hop in a join path between two tables."""

    source_table: str
    source_schema: str = ""
    source_column: str
    target_table: str
    target_schema: str = ""
    target_column: str


class Provenance(BaseModel):
    """Links an ontology element back to its physical source."""

    source_table: str
    source_schema: str = ""
    source_column: str | None = None
    target_table: str | None = None
    target_schema: str = ""
    target_column: str | None = None
    derivation: DerivationType


# ---------------------------------------------------------------------------
# Core ontology elements
# ---------------------------------------------------------------------------


class BusinessTerm(BaseModel):
    """A business entity node in the ontology (e.g. Customer, Order)."""

    id: str | None = Field(None, description="UUID assigned during Neo4j write.")
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

    id: str | None = Field(None, description="UUID assigned during Neo4j write.")
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
    is_primary_key: bool = Field(
        default=False,
        description="True when this attribute corresponds to a primary-key column.",
    )


class ObjectProperty(BaseModel):
    """A named directed edge between two Terms written as a ROLE edge."""

    name: str = Field(
        ...,
        description="Relationship name, e.g. 'places', 'belongsTo', 'reportsTo'.",
    )
    source_term: str
    target_term: str
    provenance: Provenance
    join_path: list[JoinHop] = Field(default_factory=list)


class AggregationType(str, Enum):
    SUM = "SUM"
    COUNT = "COUNT"
    AVG = "AVG"
    MAX = "MAX"
    MIN = "MIN"
    OTHER = "OTHER"


class Metric(BaseModel):
    """A business metric derived from SQL aggregation patterns."""

    id: str | None = Field(None, description="UUID assigned during Neo4j write.")
    name: str = Field(
        ..., description="Metric name, e.g. 'TotalRevenue', 'AvgOrderValue'."
    )
    expression: str = Field(..., description="SQL expression, e.g. 'SUM(amount)'.")
    source_tables: list[str] = Field(default_factory=list)
    source_column: str | None = Field(
        None, description="Column being aggregated, e.g. 'amount'."
    )
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
    """An attribute proposed for a non-FK column."""

    name: str = Field(..., description="Attribute name (matches source column name).")
    datatype: str
    term_name: str = Field(
        ..., description="Which proposed business term this belongs to."
    )
    source_column: str = Field(
        ..., description="The source column name this attribute comes from."
    )
    is_primary_key: bool = Field(
        default=False,
        description="True when this attribute corresponds to a primary-key column.",
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
                is_primary_key=attr.is_primary_key,
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
    table_to_term: dict[str, str] = Field(default_factory=dict)

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

    def resolve_term(self, table_name: str) -> str:
        """Return the current BusinessTerm name for a table.

        Uses the table_to_term mapping when available, otherwise
        falls back to the deterministic CamelCase conversion.
        """
        import re

        if table_name in self.table_to_term:
            return self.table_to_term[table_name]
        parts = re.split(r"[_\s]+", table_name)
        return "".join(p.capitalize() for p in parts if p)

    def rename_term(self, old_name: str, new_name: str) -> int:
        """Rename a business term and update all references.

        If *new_name* already exists, merges the old term's provenance
        into the existing one and removes the old term (avoids duplicates).

        Returns the number of references updated (edges + attributes).
        """
        existing = self.get_term(new_name)
        old_term = self.get_term(old_name)

        if existing and old_term and existing is not old_term:
            existing.provenance.extend(old_term.provenance)
            if old_term.description and len(old_term.description) > len(
                existing.description
            ):
                existing.description = old_term.description
            self.business_terms = [
                bt for bt in self.business_terms if bt is not old_term
            ]
        elif old_term:
            old_term.name = new_name

        updated = 0
        for attr in self.attributes:
            if attr.term_name == old_name:
                attr.term_name = new_name
                updated += 1
        for op in self.object_properties:
            if op.source_term == old_name:
                op.source_term = new_name
                updated += 1
            if op.target_term == old_name:
                op.target_term = new_name
                updated += 1
        for tbl, term in self.table_to_term.items():
            if term == old_name:
                self.table_to_term[tbl] = new_name
        return updated

    def merge(
        self,
        delta: DeltaOntology,
        source_table: str,
        source_schema: str = "",
    ) -> None:
        """Integrate a validated DeltaOntology into the core ontology."""
        prov = Provenance(
            source_table=source_table,
            source_schema=source_schema,
            derivation="llm_proposed",
        )

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
                source_schema=source_schema,
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
                target_table = ""
                target_schema_resolved = ""
                for tbl, term in self.table_to_term.items():
                    if term == op.target_term:
                        target_table = tbl
                        break

                if target_table:
                    tgt_term = self.get_term(op.target_term)
                    if tgt_term and tgt_term.provenance:
                        for p in tgt_term.provenance:
                            if p.source_table == target_table and p.source_schema:
                                target_schema_resolved = p.source_schema
                                break
                source_col = ""
                target_col = ""
                join_path: list[JoinHop] = []

                # Strategy 1: reuse join_path from an existing edge
                # between the same terms (e.g. deterministic FK).
                existing_edge = next(
                    (
                        e
                        for e in self.object_properties
                        if e.source_term == op.source_term
                        and e.target_term == op.target_term
                        and e.join_path
                    ),
                    None,
                )
                if existing_edge:
                    join_path = list(existing_edge.join_path)
                    source_col = existing_edge.provenance.source_column or ""
                    target_col = existing_edge.provenance.target_column or ""
                else:
                    # Strategy 2: match a FK-like column in attributes
                    target_lower = (target_table or op.target_term).lower()
                    for attr in self.attributes:
                        if attr.provenance.source_table == source_table:
                            col_lower = attr.source_column.lower()
                            if (
                                col_lower == f"{target_lower}id"
                                or col_lower == f"{target_lower}_id"
                            ):
                                source_col = attr.source_column
                                target_col = attr.source_column
                                break

                # Strategy 3: BFS for a multi-hop path through
                # existing deterministic edges.
                if not source_col and not join_path:
                    multi_hop = self.find_join_path(op.source_term, op.target_term)
                    if multi_hop:
                        join_path = multi_hop
                        source_col = multi_hop[0].source_column
                        target_col = multi_hop[-1].target_column

                op_prov = Provenance(
                    source_table=source_table,
                    source_schema=source_schema,
                    source_column=source_col,
                    target_table=target_table,
                    target_schema=target_schema_resolved,
                    target_column=target_col,
                    derivation="llm_proposed",
                )
                if source_col and not join_path:
                    join_path.append(
                        JoinHop(
                            source_table=source_table,
                            source_schema=source_schema,
                            source_column=source_col,
                            target_table=target_table,
                            target_schema=target_schema_resolved,
                            target_column=target_col,
                        )
                    )
                self.object_properties.append(
                    ObjectProperty(
                        name=op.name,
                        source_term=op.source_term,
                        target_term=op.target_term,
                        provenance=op_prov,
                        join_path=join_path,
                    )
                )

    def find_join_path(
        self, source_term: str, target_term: str, max_hops: int = 3
    ) -> list[JoinHop] | None:
        """BFS for the shortest multi-hop join path between two terms.

        Only follows edges that have a non-empty ``join_path`` (i.e.
        deterministic FK edges with known columns).  Returns ``None``
        when no path exists within *max_hops*.
        """
        from collections import deque

        adj: dict[str, list[ObjectProperty]] = {}
        for edge in self.object_properties:
            if edge.join_path:
                adj.setdefault(edge.source_term, []).append(edge)

        queue: deque[tuple[str, list[JoinHop]]] = deque()
        queue.append((source_term, []))
        visited: set[str] = {source_term}

        while queue:
            current, hops = queue.popleft()
            if len(hops) >= max_hops:
                continue
            for edge in adj.get(current, []):
                next_term = edge.target_term
                new_hops = hops + list(edge.join_path)
                if next_term == target_term:
                    return new_hops
                if next_term not in visited:
                    visited.add(next_term)
                    queue.append((next_term, new_hops))
        return None

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
