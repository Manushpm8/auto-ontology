"""LLM Term extraction and hierarchy proposals."""

from __future__ import annotations

import re
from typing import Any

from langchain_core.messages import HumanMessage, SystemMessage

from gsf.semantic.deterministic import to_term_name
from gsf.semantic.domain import DomainSummary
from gsf.semantic.llm import invoke_structured
from gsf.semantic.models import (
    ColumnAttributeSpec,
    RawTableTermsResult,
    TableTermsResult,
    TermAttributeAssignment,
    TermProposal,
)
from gsf.semantic import neo4j_dal

_INVALID = {"unnamed", "unknown", "none", ""}
_LABEL_RE = re.compile(r"^[A-Z][A-Za-z0-9]*(?: [A-Za-z][A-Za-z0-9]*)*$")

_SYSTEM = """\
You propose business Terms for a relational table and assign candidate columns \
to each Term with user-friendly display labels.

Rules:
1. Default to ONE Term that best represents the table. Add a second or third Term \
only when columns clearly belong to distinct business concepts (e.g. audit metadata \
vs core entity fields).
2. Term names must be user-friendly with spaces between words (e.g. Purchase Order, \
not purchase_orders or PurchaseOrder).
3. Assign EVERY candidate column to exactly one Term. For each assignment return \
source_column exactly as given and a display_name — a user-friendly ColumnAttribute \
label with spaces between words (e.g. Order Date, Total Amount).
4. Optionally propose IS_A parent or PART_OF container per Term when clearly implied. \
Use the same user-friendly naming style for referenced Term names.
5. Do not propose ROLE relationships here."""


def _normalize_label(name: str, *, fallback: str) -> str:
    cleaned = name.strip()
    if cleaned.lower() in _INVALID or not _LABEL_RE.match(cleaned):
        return fallback
    return cleaned


def _format_spec_line(spec: ColumnAttributeSpec) -> str:
    desc = f" — {spec.description}" if spec.description else ""
    return f"  - source_column={spec.source_column} ({spec.datatype}){desc}"


def apply_display_names_to_specs(
    table_result: TableTermsResult,
    specs: list[ColumnAttributeSpec],
) -> None:
    """Write LLM display labels back onto column specs for downstream merges."""
    by_column = {spec.source_column: spec for spec in specs}
    for term in table_result.terms:
        for attr in term.attributes:
            spec = by_column.get(attr.source_column)
            if spec is not None:
                spec.display_name = attr.display_name
    for spec in specs:
        if not spec.display_name:
            spec.display_name = spec.name


def _fallback_result(
    table: dict[str, Any],
    specs: list[ColumnAttributeSpec],
) -> TableTermsResult:
    name = to_term_name(table["name"])
    attributes = [
        TermAttributeAssignment(
            source_column=spec.source_column,
            display_name=spec.name,
        )
        for spec in specs
    ]
    result = TableTermsResult(
        terms=[
            TermProposal(
                name=name,
                description=f"Business entity represented by table {table['name']}",
                attributes=attributes,
            )
        ]
    )
    apply_display_names_to_specs(result, specs)
    return result


def _sanitize_result(
    result: RawTableTermsResult,
    *,
    table: dict[str, Any],
    specs: list[ColumnAttributeSpec],
) -> TableTermsResult:
    spec_by_column = {spec.source_column: spec for spec in specs}
    allowed_columns = set(spec_by_column)
    default_term = to_term_name(table["name"])

    sanitized_terms: list[TermProposal] = []
    seen_term_names: set[str] = set()
    assigned_columns: set[str] = set()

    for raw_term in result.terms:
        term_name = _normalize_label(raw_term.name, fallback=default_term)
        if term_name in seen_term_names:
            continue
        seen_term_names.add(term_name)

        attributes: list[TermAttributeAssignment] = []
        for raw_attr in raw_term.attributes:
            source_column = raw_attr.source_column.strip()
            if (
                not source_column
                or source_column not in allowed_columns
                or source_column in assigned_columns
            ):
                continue
            spec = spec_by_column[source_column]
            attributes.append(
                TermAttributeAssignment(
                    source_column=source_column,
                    display_name=_normalize_label(
                        raw_attr.display_name,
                        fallback=spec.name,
                    ),
                )
            )
            assigned_columns.add(source_column)

        sanitized_terms.append(
            TermProposal(
                name=term_name,
                description=raw_term.description.strip(),
                is_a_parent=raw_term.is_a_parent,
                part_of_target=raw_term.part_of_target,
                attributes=attributes,
            )
        )

    if not sanitized_terms:
        return _fallback_result(table, specs)

    primary = sanitized_terms[0]
    for spec in specs:
        if spec.source_column not in assigned_columns:
            primary.attributes.append(
                TermAttributeAssignment(
                    source_column=spec.source_column,
                    display_name=spec.name,
                )
            )
            assigned_columns.add(spec.source_column)

    table_result = TableTermsResult(terms=sanitized_terms)
    apply_display_names_to_specs(table_result, specs)
    return table_result


def extract_term(
    table: dict[str, Any],
    ctx: dict[str, Any],
    specs: list[ColumnAttributeSpec],
    *,
    domain_summary: DomainSummary | None = None,
) -> TableTermsResult:
    """Propose one or more Terms and assign candidate column attributes to each."""
    if not specs:
        return _fallback_result(table, specs)

    neighbor_terms = neo4j_dal.fetch_neighbor_terms(table["id"])
    spec_lines = "\n".join(_format_spec_line(spec) for spec in specs[:40])
    domain_block = ""
    if domain_summary:
        domain_block = (
            f"\nDomains: {', '.join(domain_summary.domains[:8])}\n"
            f"Core entities: {', '.join(domain_summary.core_entities[:12])}\n"
        )
    prompt = (
        f"Table: {table['name']}\n"
        f"Description: {table.get('description') or ''}\n"
        f"Candidate columns (assign each to exactly one Term with a display_name):\n"
        f"{spec_lines}\n"
        f"Known neighbor Terms: {', '.join(neighbor_terms) or '(none)'}\n"
        f"{domain_block}"
    )
    try:
        result = invoke_structured(
            [SystemMessage(content=_SYSTEM), HumanMessage(content=prompt)],
            RawTableTermsResult,
            temperature=0.0,
            max_tokens=2048,
        )
    except Exception:
        return _fallback_result(table, specs)

    return _sanitize_result(result, table=table, specs=specs)
