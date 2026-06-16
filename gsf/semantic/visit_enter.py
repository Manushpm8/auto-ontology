"""Per-table taxonomy compilation: FK detection, Term + ColumnAttributes."""

from __future__ import annotations

import logging
from typing import Any

from gsf.semantic import neo4j_dal
from gsf.semantic.deterministic import column_attribute_specs
from gsf.semantic.domain import DomainSummary
from gsf.semantic.fk_suggester import suggest_potential_foreign_keys
from gsf.semantic.models import ColumnAttributeSpec
from gsf.semantic.term_extractor import apply_display_names_to_specs, extract_term

logger = logging.getLogger(__name__)


def _terms_with_assignments(
    term_result: Any,
    spec_by_column: dict[str, ColumnAttributeSpec],
) -> list[tuple[Any, list[Any]]]:
    """Terms that have at least one resolvable column attribute."""
    persisted = []
    for term in term_result.terms:
        assignments = [a for a in term.attributes if a.source_column in spec_by_column]
        if assignments:
            persisted.append((term, assignments))
    return persisted


def process_table(
    table: dict[str, Any],
    ctx: dict[str, Any],
    *,
    domain_summary: DomainSummary | None,
) -> None:
    """Build taxonomy nodes for one table: Term and ColumnAttributes."""
    table_id = table["id"]
    table_name = table["name"]

    # --- FK detection (LLM + declared); results not written to Neo4j ---
    declared_fks = ctx.get("fks", [])
    fk_suggestions = suggest_potential_foreign_keys(table, ctx)
    suggested_fk_names = {s.column_name for s in fk_suggestions.suggestions}
    declared_fk_names = {
        fk["source_column"] for fk in declared_fks if fk.get("source_column")
    }
    all_fk_names = declared_fk_names | suggested_fk_names

    # --- Build attribute specs for non-FK columns ---
    specs = column_attribute_specs(
        ctx.get("columns", []),
        declared_fks,
        suggested_fk_columns=suggested_fk_names,
    )
    if not specs:
        logger.warning(
            "Table %s has no non-FK columns — skipping Term creation", table_name
        )
        return

    # --- LLM: propose Term(s) and display names ---
    term_result = extract_term(table, ctx, specs, domain_summary=domain_summary)
    apply_display_names_to_specs(term_result, specs)
    spec_by_column = {spec.source_column: spec for spec in specs}
    persisted_terms = _terms_with_assignments(term_result, spec_by_column)

    if not persisted_terms:
        logger.warning(
            "Table %s: LLM assigned no columns to any Term (%d candidates)",
            table_name,
            len(specs),
        )
        return

    attr_count = 0
    for term, assignments in persisted_terms:
        neo4j_dal.merge_term(
            term.name, term.description, table_id, synonyms=term.synonyms
        )
        for assignment in assignments:
            spec = spec_by_column[assignment.source_column]
            neo4j_dal.merge_column_attribute(
                term_name=term.name,
                table_id=table_id,
                source_column=spec.source_column,
                attr_name=spec.display_name,
                datatype=spec.datatype,
                description=spec.description,
            )
            attr_count += 1

    term_names = [t.name for t, _ in persisted_terms]
    logger.info(
        "Processed %s → Terms %s (%d attrs, %d suspected FKs)",
        table_name,
        term_names,
        attr_count,
        len(all_fk_names),
    )
