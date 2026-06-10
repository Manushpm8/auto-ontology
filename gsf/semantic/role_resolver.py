"""Two-phase join-path resolution for question-based ROLE edges."""

from __future__ import annotations

import json
import logging
from typing import Any

from langchain_core.messages import HumanMessage, SystemMessage

from gsf.semantic import neo4j_dal
from gsf.semantic.llm import invoke_structured
from gsf.semantic.loaders import fetch_table_by_id, fetch_table_context
from gsf.semantic.models import SingleHopJoin
from gsf.semantic.paths import compute_join_path

logger = logging.getLogger(__name__)

_SYSTEM = """\
Determine whether two relational tables can be joined in a single hop.

You are given the full column lists for both tables (source annotated with
[PK]/[FK]) and the set of declared foreign-key columns from source to target.

Rules:
1. Return possible=true only when a direct key-based join can be strongly inferred.
2. Prefer explicit FK→PK pairs. Also accept clearly implicit joins
   (e.g. orders.customer_id → customers.id).
3. src_column must name a column from the source table; tgt_column must name a
   column from the target table (or be empty if unknown).
4. Return possible=false with empty column fields when no clear single-hop join exists."""


def _normalize_pk(pk: Any) -> list[str]:
    if not pk:
        return []
    if isinstance(pk, list):
        return [str(x) for x in pk]
    return [str(pk)]


def _col_lines(
    columns: list[dict[str, Any]],
    pk_cols: set[str],
    fk_cols: set[str],
) -> str:
    lines = []
    for col in columns:
        name = col.get("name", "")
        dtype = col.get("data_type", "")
        tags = []
        if name in pk_cols:
            tags.append("[PK]")
        if name in fk_cols:
            tags.append("[FK]")
        tag_str = " ".join(tags)
        lines.append(f"  {name} ({dtype}){(' ' + tag_str) if tag_str else ''}")
    return "\n".join(lines)


def _hop_has_columns(hop: dict[str, Any]) -> bool:
    """A hop is complete only when both join columns are named."""
    return bool(hop.get("source", {}).get("column")) and bool(
        hop.get("target", {}).get("column")
    )


def resolve_single_hop_join(
    src_table_id: str,
    src_table_name: str,
    tgt_table: dict[str, Any],
) -> list[dict[str, Any]] | None:
    """Return a hop-centric join_path or None.

    Each element: ``{"hop": N, "source": {"table": ..., "column": ...},
                                "target": {"table": ..., "column": ...}}``

    Priority:
    1. Deterministic graph path (FK/JOIN edges) — compute_join_path already
       returns None when any hop lacks column conditions.
    2. LLM inference: fetches full column metadata for both tables from Neo4j
       and asks the LLM whether a direct key-based join can be inferred.
    Returns None when neither resolves.
    """
    tgt_table_id = tgt_table["id"]
    tgt_table_name = tgt_table["name"]

    path = compute_join_path(src_table_id, tgt_table_id)
    if path is not None and all(_hop_has_columns(h) for h in path):
        return path
    if path is not None:
        logger.debug(
            "Graph path %s → %s found but missing column conditions; trying LLM",
            src_table_name,
            tgt_table_name,
        )

    src_row = fetch_table_by_id(src_table_id)
    src_pk = set(_normalize_pk(src_row.get("pk") if src_row else None))
    src_ctx = fetch_table_context(src_table_id)
    src_fk_cols = {
        fk["source_column"] for fk in src_ctx.get("fks", []) if fk.get("source_column")
    }
    explicit_fks_to_tgt = [
        fk["source_column"]
        for fk in src_ctx.get("fks", [])
        if fk.get("target_table") == tgt_table["name"] and fk.get("source_column")
    ]

    tgt_row = fetch_table_by_id(tgt_table_id)
    tgt_pk = set(_normalize_pk(tgt_row.get("pk") if tgt_row else None))
    tgt_ctx = fetch_table_context(tgt_table_id)

    src_lines = _col_lines(src_ctx.get("columns", []), src_pk, src_fk_cols)
    tgt_lines = _col_lines(tgt_ctx.get("columns", []), tgt_pk, set())
    explicit_str = ", ".join(explicit_fks_to_tgt) if explicit_fks_to_tgt else "none"

    user_msg = (
        f"Source table: {src_table_name}\n"
        f"Columns:\n{src_lines}\n\n"
        f"Target table: {tgt_table_name}\n"
        f"Columns:\n{tgt_lines}\n\n"
        f"Declared FK columns from source pointing at target: {explicit_str}"
    )

    try:
        result: SingleHopJoin = invoke_structured(
            messages=[SystemMessage(content=_SYSTEM), HumanMessage(content=user_msg)],
            schema=SingleHopJoin,
        )
    except Exception:
        logger.warning(
            "LLM single-hop inference failed for %s → %s",
            src_table_name,
            tgt_table["name"],
            exc_info=True,
        )
        return None
    if not result.possible or not result.src_column:
        logger.debug(
            "LLM: no single-hop join %s → %s (%s)",
            src_table_name,
            tgt_table_name,
            result.rationale,
        )
        return None

    logger.debug(
        "LLM inferred join %s.%s → %s.%s",
        src_table_name,
        result.src_column,
        tgt_table_name,
        result.tgt_column,
    )
    return [
        {
            "hop": 1,
            "source": {"table": src_table_name, "column": result.src_column},
            "target": {"table": tgt_table_name, "column": result.tgt_column},
        }
    ]


def find_semantic_role_path(
    src_term: str,
    tgt_term: str,
) -> list[dict[str, Any]] | None:
    """Concatenate hop lists along the shortest existing ROLE chain, renumbering hops.

    Used in Phase 2 when no direct single-hop join exists between the source
    and target tables, but a multi-hop path through already-created ROLE edges
    is available.
    """
    join_paths = neo4j_dal.find_shortest_role_path(src_term, tgt_term)
    if not join_paths:
        return None

    merged: list[dict[str, Any]] = []
    for jp_raw in join_paths:
        try:
            segment: list[dict[str, Any]] = (
                json.loads(jp_raw) if isinstance(jp_raw, str) else jp_raw
            )
        except (ValueError, TypeError):
            logger.warning(
                "Could not parse join_path segment for %s→%s", src_term, tgt_term
            )
            continue
        for hop in segment:
            if not _hop_has_columns(hop):
                logger.warning(
                    "Skipping incomplete hop in ROLE path %s→%s", src_term, tgt_term
                )
                continue
            merged.append({**hop, "hop": len(merged) + 1})

    return merged or None
