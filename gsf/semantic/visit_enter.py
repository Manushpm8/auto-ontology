"""Enter phase + recursive post-order DFS driver for semantic compilation."""

from __future__ import annotations

import logging
import os
from dataclasses import dataclass, field
from enum import IntEnum
from typing import Any

from nemo_retriever.retriever import Retriever

from gsf.semantic import neo4j_dal
from gsf.semantic.deterministic import column_attribute_specs
from gsf.semantic.domain import DomainSummary
from gsf.semantic.embed import SemanticEmbedder
from gsf.semantic.fk_suggester import suggest_potential_foreign_keys
from gsf.semantic.loaders import (
    fetch_join_neighbors,
    fetch_table_by_name,
    fetch_table_context,
)
from gsf.semantic.models import (
    BusinessQuestionItem,
    ColumnAttributeSpec,
    PotentialFkSuggestion,
    TableTermsResult,
    TermAttributeAssignment,
    TermProposal,
)
from gsf.semantic.term_extractor import apply_display_names_to_specs, extract_term
from gsf.semantic.vdb_discovery import (
    discover_tables_via_vdb,
    generate_business_questions,
)

logger = logging.getLogger(__name__)

_EMBED_ENDPOINT = os.environ.get(
    "EMBED_ENDPOINT", "https://integrate.api.nvidia.com/v1"
)
_EMBED_MODEL = os.environ.get("EMBED_MODEL", "nvidia/llama-nemotron-embed-1b-v2")
_NVIDIA_API_KEY = os.environ.get("NVIDIA_API_KEY", "")


class _Priority(IntEnum):
    """Lower value = visited first within one parent's direct neighbours."""

    JOIN = 1
    FK = 2
    VDB = 3


@dataclass
class VisitContext:
    """Per-compilation traversal config. State lives in Neo4j (reviewed flag)."""

    retriever: Retriever | None
    embedder: SemanticEmbedder | None
    domain_summary: DomainSummary | None
    # (table_name, fk_suggestion) — one entry per PotentialFkSuggestion per table visit
    pending_fk_entries: list[tuple[str, PotentialFkSuggestion]] = field(
        default_factory=list
    )
    # (table_id, table_name, anchor_term, item)
    pending_question_roles: list[tuple[str, str, str, BusinessQuestionItem]] = field(
        default_factory=list
    )


def build_data_retriever(database_name: str) -> Retriever | None:
    if not _NVIDIA_API_KEY:
        return None
    try:
        from gsf.vdb import get_data_vdb

        return Retriever(
            top_k=10,
            vdb_kwargs={"vdb": get_data_vdb(database_name=database_name)},
            embed_kwargs={
                "model_name": _EMBED_MODEL,
                "embed_invoke_url": _EMBED_ENDPOINT,
                "api_key": _NVIDIA_API_KEY,
            },
        )
    except Exception:
        logger.warning("Could not build data-layer retriever")
        return None


def _terms_with_assignments(
    term_result: TableTermsResult,
    spec_by_column: dict[str, ColumnAttributeSpec],
) -> list[tuple[TermProposal, list[TermAttributeAssignment]]]:
    """Terms that have at least one resolvable column attribute."""
    persisted: list[tuple[TermProposal, list[TermAttributeAssignment]]] = []
    for term in term_result.terms:
        assignments = [
            assignment
            for assignment in term.attributes
            if assignment.source_column in spec_by_column
        ]
        if assignments:
            persisted.append((term, assignments))
    return persisted


def visit_enter(
    table: dict[str, Any],
    ctx: dict[str, Any],
    *,
    vctx: VisitContext,
    hop: int,
) -> None:
    """Merge Terms, recurse into neighbours, then post-order write ROLE edges."""
    table_id = table["id"]
    table_name = table["name"]

    neo4j_dal.mark_table_reviewed(table_id)
    fk_suggestions = suggest_potential_foreign_keys(table, ctx)
    neo4j_dal.mark_suspected_foreign_keys(
        table_id,
        [s.model_dump() for s in fk_suggestions.suggestions],
    )
    suggested_fk_names = {s.column_name for s in fk_suggestions.suggestions}
    for fk in fk_suggestions.suggestions:
        vctx.pending_fk_entries.append((table_name, fk))
    # Also include declared schema FKs (data-layer edges in Neo4j) so that
    # finalize_all_roles has the full picture of known join columns.
    declared_fk_names = {s.column_name for s in fk_suggestions.suggestions}
    for fk_dict in ctx.get("fks", []):
        col = fk_dict.get("source_column", "")
        if col and col not in declared_fk_names:
            vctx.pending_fk_entries.append(
                (
                    table_name,
                    PotentialFkSuggestion(column_name=col, rationale="declared"),
                )
            )
            declared_fk_names.add(col)
    specs = column_attribute_specs(
        ctx.get("columns", []),
        ctx.get("fks", []),
        suggested_fk_columns=suggested_fk_names,
    )
    term_result = extract_term(table, ctx, specs, domain_summary=vctx.domain_summary)
    apply_display_names_to_specs(term_result, specs)
    spec_by_column: dict[str, ColumnAttributeSpec] = {
        spec.source_column: spec for spec in specs
    }
    persisted_terms = _terms_with_assignments(term_result, spec_by_column)
    attr_count = 0

    for term, assignments in persisted_terms:
        term_id = neo4j_dal.merge_term(term.name, term.description, table_id)
        if term.is_a_parent:
            neo4j_dal.merge_is_a(term.name, term.is_a_parent)
        if term.part_of_target:
            neo4j_dal.merge_part_of(term.name, term.part_of_target)

        attr_ids: dict[str, str | None] = {}
        for assignment in assignments:
            spec = spec_by_column[assignment.source_column]
            attr_ids[assignment.source_column] = neo4j_dal.merge_column_attribute(
                term_name=term.name,
                table_id=table_id,
                source_column=spec.source_column,
                attr_name=spec.display_name,
                datatype=spec.datatype,
                description=spec.description,
            )
            attr_count += 1

        if vctx.embedder is not None:
            attrs_rows = [
                {
                    "name": spec_by_column[a.source_column].display_name,
                    "term_name": term.name,
                    "source_column": a.source_column,
                    "description": spec_by_column[a.source_column].description,
                    "id": attr_ids.get(a.source_column),
                }
                for a in assignments
            ]
            try:
                vctx.embedder.embed_term(
                    {
                        "name": term.name,
                        "description": term.description,
                        "id": term_id,
                    },
                    attrs_rows,
                )
            except Exception:
                logger.warning("Inline embed failed for %s.%s", table_name, term.name)

    vdb_names: list[str] = []
    questions_by_term: list[tuple[str, list[BusinessQuestionItem]]] = []
    question_items: list[BusinessQuestionItem] = []
    if vctx.retriever is not None:
        seen_questions: set[tuple[str, str, str]] = set()
        for term, _ in persisted_terms:
            term_questions = generate_business_questions(table, ctx, term.name)
            term_items: list[BusinessQuestionItem] = []
            for item in term_questions.items:
                key = (item.question, item.entity, item.role)
                if key in seen_questions:
                    continue
                seen_questions.add(key)
                term_items.append(item)
                question_items.append(item)
            if term_items:
                questions_by_term.append((term.name, term_items))
        vdb_names = discover_tables_via_vdb(question_items, vctx.retriever)

    _process_neighbors(
        table_id,
        hop=hop,
        fks=ctx.get("fks", []),
        vdb_names=vdb_names,
        vctx=vctx,
    )

    all_role_intents: list[tuple[str, BusinessQuestionItem]] = [
        (anchor_term, item)
        for anchor_term, term_items in questions_by_term
        for item in term_items
    ]
    for anchor_term, item in all_role_intents:
        vctx.pending_question_roles.append((table_id, table_name, anchor_term, item))

    term_names = [term.name for term, _ in persisted_terms]
    logger.info(
        "Enter %s -> Terms %s (%d attrs, %d suspected FKs, %d vdb neighbors, "
        "%d pending question intents)",
        table_name,
        term_names,
        attr_count,
        len(fk_suggestions.suggestions),
        len(vdb_names),
        len(all_role_intents),
    )


def _process_neighbors(
    table_id: str,
    *,
    hop: int,
    fks: list[dict[str, Any]],
    vdb_names: list[str],
    vctx: VisitContext,
) -> None:
    """Recursively visit this table's direct neighbours in JOIN/FK/VDB order."""
    for nbr in _collect_neighbours(table_id, fks, vdb_names):
        if nbr["id"] == table_id:
            continue
        nbr_ctx = fetch_table_context(nbr["id"])
        if nbr_ctx.get("reviewed"):
            logger.debug("Skip %s — already reviewed", nbr["name"])
            continue
        if not nbr_ctx.get("columns"):
            logger.warning("Table %s has no columns — skipping", nbr["name"])
            neo4j_dal.mark_table_reviewed(nbr["id"])
            continue
        visit_enter(nbr, nbr_ctx, vctx=vctx, hop=hop + 1)


def _collect_neighbours(
    table_id: str,
    fks: list[dict[str, Any]],
    vdb_names: list[str],
) -> list[dict[str, Any]]:
    """Dedup + priority-sort direct neighbours as full table dicts."""
    best: dict[str, tuple[int, dict[str, Any]]] = {}

    def _record(nbr: dict[str, Any] | None, priority: _Priority) -> None:
        if not nbr or not nbr.get("id") or not nbr.get("name"):
            return
        current = best.get(nbr["id"])
        if current is None or int(priority) < current[0]:
            best[nbr["id"]] = (int(priority), nbr)

    for nbr in fetch_join_neighbors(table_id):
        _record(nbr, _Priority.JOIN)
    for fk in fks:
        target_id = fk.get("target_table_id")
        target_name = fk.get("target_table")
        if target_id and target_name:
            _record({"id": target_id, "name": target_name}, _Priority.FK)
    for name in vdb_names:
        row = fetch_table_by_name(name)
        _record(row, _Priority.VDB)

    return [info[1] for _, info in sorted(best.items(), key=lambda kv: kv[1][0])]
