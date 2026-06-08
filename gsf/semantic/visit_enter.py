"""Enter phase: reviewed → ColumnAttributes + Term + weave → discovery → enqueue."""

from __future__ import annotations

import logging
import os
from typing import Any

from nemo_retriever.retriever import Retriever

from gsf.semantic import neo4j_dal
from gsf.semantic.deterministic import column_attribute_specs, fk_target_table_names
from gsf.semantic.domain import DomainSummary
from gsf.semantic.fk_suggester import suggest_potential_foreign_keys
from gsf.semantic.queue import TablesQueue
from gsf.semantic.term_extractor import apply_display_names_to_specs, extract_term
from gsf.semantic.models import BusinessQuestionItem, ColumnAttributeSpec
from gsf.semantic.vdb_discovery import (
    discover_tables_via_vdb,
    generate_business_questions,
)
from gsf.semantic.visit_finalize import write_question_role_edges

logger = logging.getLogger(__name__)

_EMBED_ENDPOINT = os.environ.get(
    "EMBED_ENDPOINT", "https://integrate.api.nvidia.com/v1"
)
_EMBED_MODEL = os.environ.get("EMBED_MODEL", "nvidia/llama-nemotron-embed-1b-v2")
_NVIDIA_API_KEY = os.environ.get("NVIDIA_API_KEY", "")


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


def visit_enter(
    table: dict[str, Any],
    ctx: dict[str, Any],
    *,
    queue: TablesQueue,
    hop: int,
    retriever: Retriever | None,
    domain_summary: DomainSummary | None,
) -> None:
    """First visit: local semantic nodes, discovery, enqueue, question ROLE edges."""
    table_id = table["id"]
    table_name = table["name"]

    neo4j_dal.mark_table_reviewed(table_id)
    fk_suggestions = suggest_potential_foreign_keys(table, ctx)
    neo4j_dal.mark_suspected_foreign_keys(
        table_id,
        [s.model_dump() for s in fk_suggestions.suggestions],
    )
    suggested_fk_names = {s.column_name for s in fk_suggestions.suggestions}
    specs = column_attribute_specs(
        ctx.get("columns", []),
        ctx.get("fks", []),
        suggested_fk_columns=suggested_fk_names,
    )
    term_result = extract_term(table, ctx, specs, domain_summary=domain_summary)
    apply_display_names_to_specs(term_result, specs)
    spec_by_column: dict[str, ColumnAttributeSpec] = {
        spec.source_column: spec for spec in specs
    }
    attr_count = 0

    for term in term_result.terms:
        neo4j_dal.merge_term(term.name, term.description, table_id)
        if term.is_a_parent:
            neo4j_dal.merge_is_a(term.name, term.is_a_parent)
        if term.part_of_target:
            neo4j_dal.merge_part_of(term.name, term.part_of_target)

        for assignment in term.attributes:
            spec = spec_by_column.get(assignment.source_column)
            if spec is None:
                continue
            neo4j_dal.merge_column_attribute(
                term_name=term.name,
                table_id=table_id,
                source_column=spec.source_column,
                attr_name=spec.display_name,
                datatype=spec.datatype,
                description=spec.description,
            )
            attr_count += 1

    vdb_names: list[str] = []
    questions_by_term: list[tuple[str, list[BusinessQuestionItem]]] = []
    question_items: list[BusinessQuestionItem] = []
    if retriever is not None:
        seen_questions: set[tuple[str, str, str]] = set()
        for term in term_result.terms:
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
        vdb_names = discover_tables_via_vdb(question_items, retriever)

    fk_targets = fk_target_table_names(ctx.get("fks", []))
    queue.discover_neighbors(
        table_id,
        table_name,
        hop,
        vdb_table_names=vdb_names,
        fk_targets=fk_targets,
    )

    role_count = 0
    for anchor_term, term_items in questions_by_term:
        role_count += write_question_role_edges(
            table_id,
            anchor_term,
            table_name,
            term_items,
        )

    term_names = [t.name for t in term_result.terms]
    logger.info(
        "Enter %s → Terms %s (%d attrs, %d suspected FKs, %d vdb neighbors, %d question ROLE)",
        table_name,
        term_names,
        attr_count,
        len(fk_suggestions.suggestions),
        len(vdb_names),
        role_count,
    )
