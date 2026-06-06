"""LLM business questions + data-layer VDB table discovery."""

from __future__ import annotations

import json
import logging
from typing import Any

from langchain_core.messages import HumanMessage, SystemMessage
from nemo_retriever.retriever import Retriever
from nemo_retriever.tabular_data.ingestion.model.reserved_words import Labels

from gsf.semantic.llm import invoke_structured
from gsf.semantic.models import BusinessQuestionsResult

logger = logging.getLogger(__name__)

_QUESTION_SYSTEM = """\
Generate up to 3 simple business questions spanning at most two entities.
Extract the business entity names mentioned. Questions help discover related tables."""


def generate_business_questions(
    table: dict[str, Any],
    ctx: dict[str, Any],
    term_name: str,
) -> BusinessQuestionsResult:
    desc = table.get("description") or ""
    cols = ", ".join(c["name"] for c in ctx.get("columns", [])[:20])
    prompt = (
        f"Table: {table['name']}\nTerm: {term_name}\nDescription: {desc}\n"
        f"Columns: {cols}"
    )
    try:
        return invoke_structured(
            [
                SystemMessage(content=_QUESTION_SYSTEM),
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
    for entity in entities[:5]:
        query = f"table related to {entity} business entity"
        try:
            hits = retriever.query(query, top_k=top_k)
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
