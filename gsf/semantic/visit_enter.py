"""Enter phase: reviewed → ColumnAttributes + Term + weave → discovery → enqueue."""

from __future__ import annotations

import logging
import os
from typing import Any

from nemo_retriever.retriever import Retriever

from gsf.semantic import neo4j_dal
from gsf.semantic.deterministic import column_attribute_specs, fk_target_table_names
from gsf.semantic.domain import DomainSummary
from gsf.semantic.queue import TablesQueue
from gsf.semantic.term_extractor import extract_term
from gsf.semantic.models import BusinessQuestionItem
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

    # TODO:
    # 1. Extract more than one Term per table (optional).
    # 2. User friendly term name and columns (specs) names.
    # 3. Set on column nodes (neo4j) suspected as fk. pk columns are not fks.
    # 4. Do not merge column attributes for fks.

    neo4j_dal.mark_table_reviewed(table_id)

    specs = column_attribute_specs(ctx.get("columns", []), ctx.get("fks", []))

    proposal = extract_term(table, ctx, domain_summary=domain_summary)
    neo4j_dal.merge_term(proposal.name, proposal.description, table_id)

    if proposal.is_a_parent:
        neo4j_dal.merge_is_a(proposal.name, proposal.is_a_parent)
    if proposal.part_of_target:
        neo4j_dal.merge_part_of(proposal.name, proposal.part_of_target)

    for spec in specs:
        neo4j_dal.merge_column_attribute(
            term_name=proposal.name,
            table_id=table_id,
            source_column=spec.source_column,
            attr_name=spec.name,
            datatype=spec.datatype,
            description=spec.description,
        )

    vdb_names: list[str] = []
    question_items: list[BusinessQuestionItem] = []
    if retriever is not None:
        questions = generate_business_questions(table, ctx, proposal.name)
        question_items = questions.items
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
    if question_items:
        role_count = write_question_role_edges(
            table_id,
            proposal.name,
            table_name,
            question_items,
        )

    logger.info(
        "Enter %s → Term %r (%d attrs, %d vdb neighbors, %d question ROLE)",
        table_name,
        proposal.name,
        len(specs),
        len(vdb_names),
        role_count,
    )
