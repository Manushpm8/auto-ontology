"""LLM business questions + data-layer VDB table discovery."""

from __future__ import annotations

import json
import logging
from typing import Any

from langchain_core.messages import HumanMessage, SystemMessage
from nemo_retriever.retriever import Retriever
from nemo_retriever.tabular_data.ingestion.model.reserved_words import Labels
from nemo_retriever.tabular_data.retrieval.data_access.semantic_search import (
    _build_metadata_where_clause,
    _metadata_filter_format,
)

from gsf.semantic.deterministic import fk_target_table_names
from gsf.semantic.llm import invoke_structured
from gsf.semantic.models import BusinessQuestionsResult

logger = logging.getLogger(__name__)


def _build_table_discovery_where(retriever: Retriever) -> dict[str, Any] | str | None:
    """Build a server-side metadata filter for table-only VDB hits."""
    vdb = (getattr(retriever, "vdb_kwargs", None) or {}).get("vdb")
    database_name = getattr(vdb, "database_name", None)
    return _build_metadata_where_clause(
        labels=[Labels.TABLE],
        database_name=database_name,
        fmt=_metadata_filter_format(retriever),
    )


def _question_system_prompt(anchor_term: str) -> str:
    return f"""\
Generate exactly 3 simple business questions to discover related database tables.

Anchor Term (current table — do NOT put this in entities): {anchor_term}

Rules:
1. COVERAGE: Each question explores a different business angle. Never repeat the same \
theme (e.g. only one question about dates/creation, only one about counts, etc.).
2. CROSS-ENTITY: Each question must involve the anchor Term plus at least one OTHER \
business entity suggested by FK targets, column names, or domain context.
3. entities: REQUIRED non-empty list. Collect every OTHER CamelCase entity referenced \
across all questions — never "{anchor_term}" or variants. entities is used for VDB \
table lookup; an empty list is invalid when questions mention other entities.
4. questions: plain question text only — no "Question:" prefix, no inline entity lists.

Example (anchor Term = Order):
{{
  "questions": [
    "How many orders did each customer place last month?",
    "Which orders are still awaiting shipment?",
    "What is the average order value by product category?"
  ],
  "entities": ["Customer", "Shipment", "Product"]
}}

In the example above, "Order" is the anchor term and belong only in questions, \
not in entities. Extract "Customer", "Shipment", etc. from what the questions reference."""


def _build_question_prompt(
    table: dict[str, Any],
    ctx: dict[str, Any],
    term_name: str,
) -> str:
    desc = table.get("description") or ""
    cols = ", ".join(c["name"] for c in ctx.get("columns", [])[:20])
    fk_targets = fk_target_table_names(ctx.get("fks", []))
    fk_block = ", ".join(fk_targets) if fk_targets else "(none)"
    neighbor_block = "(none)"
    table_id = table.get("id")
    if table_id:
        try:
            from gsf.semantic import neo4j_dal

            neighbors = neo4j_dal.fetch_neighbor_terms(table_id, limit=8)
            if neighbors:
                neighbor_block = ", ".join(neighbors)
        except Exception:
            pass

    return (
        f"Table: {table['name']}\n"
        f"Anchor Term: {term_name}\n"
        f"Description: {desc}\n"
        f"Columns: {cols}\n"
        f"FK target tables: {fk_block}\n"
        f"Known neighbor Terms: {neighbor_block}\n"
        "Use FK targets and column semantics to invent plausible OTHER business entities."
    )


def generate_business_questions(
    table: dict[str, Any],
    ctx: dict[str, Any],
    term_name: str,
) -> BusinessQuestionsResult:
    prompt = _build_question_prompt(table, ctx, term_name)
    try:
        return invoke_structured(
            [
                SystemMessage(content=_question_system_prompt(term_name)),
                HumanMessage(content=prompt),
            ],
            BusinessQuestionsResult,
            temperature=0.2,
        )
    except Exception:
        logger.warning("Business question generation failed for %s", table["name"])
        return BusinessQuestionsResult()


def _coerce_metadata_dict(value: Any) -> dict[str, Any]:
    if isinstance(value, dict):
        return value
    if isinstance(value, str) and value:
        try:
            parsed = json.loads(value)
        except json.JSONDecodeError:
            return {}
        return parsed if isinstance(parsed, dict) else {}
    return {}


def _metadata_from_hit(hit: dict[str, Any]) -> dict[str, Any]:
    """Normalize retriever hit metadata (dict or JSON string)."""
    meta = _coerce_metadata_dict(hit.get("metadata"))
    if not meta:
        # Some retrieval paths flatten fields onto the hit root.
        meta = {
            k: v
            for k, v in hit.items()
            if k not in {"text", "score", "rerank_score", "metadata"}
        }

    content = meta.get("content_metadata")
    content_dict = _coerce_metadata_dict(content)
    if content_dict:
        return content_dict
    return meta


def discover_tables_via_vdb(
    entities: list[str],
    retriever: Retriever,
    *,
    top_k: int = 5,
) -> list[str]:
    """Return table names from data-layer VDB hits (catalog filtering is in TablesQueue)."""
    discovered: list[str] = []
    where = _build_table_discovery_where(retriever)
    vdb_kwargs = {"where": where} if where else None
    for entity in entities[:5]:
        try:
            hits = retriever.query(
                entity,
                top_k=top_k,
                vdb_kwargs=vdb_kwargs,
            )
        except Exception:
            logger.warning("VDB search failed for entity %r", entity)
            continue
        for hit in hits or []:
            if not isinstance(hit, dict):
                continue
            meta = _metadata_from_hit(hit)
            if meta.get("label") == Labels.TABLE:
                name = meta.get("name", "")
                if name:
                    discovered.append(name)
    return list(dict.fromkeys(discovered))
