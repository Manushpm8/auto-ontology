"""Two-phase join-path resolution for question-based ROLE edges."""

from __future__ import annotations

import json
import logging
from typing import Any

from langchain_core.messages import HumanMessage, SystemMessage

from gsf.semantic import neo4j_dal
from gsf.semantic.llm import invoke_structured
from gsf.semantic.models import SingleHopJoin
from gsf.semantic.paths import compute_join_path

logger = logging.getLogger(__name__)

_SYSTEM = """\
Determine whether two relational tables can be joined in a single hop.

You are given: source table columns (annotated [PK]/[FK]), declared FK columns
pointing at the target, and the target table's PK column(s).

Rules:
1. Return possible=true only when a direct key-based join can be strongly inferred.
2. Prefer explicit FK→PK pairs. Also accept clearly implicit joins
   (e.g. orders.customer_id → customers.id).
3. src_column must name a column from the source table; tgt_column must be one of
   the target PK columns listed (or empty if unknown).
4. Return possible=false with empty column fields when no clear single-hop join exists."""


def _normalize_pk(pk: Any) -> list[str]:
    if not pk:
        return []
    if isinstance(pk, list):
        return [str(x) for x in pk]
    return [str(pk)]


def _src_col_lines(
    ctx: dict[str, Any],
    src_pk: list[str],
    suggested_fk_names: set[str],
) -> str:
    explicit_fk_cols = {
        fk["column_name"] for fk in ctx.get("fks", []) if fk.get("column_name")
    }
    lines = []
    for col in ctx.get("columns", []):
        name = col.get("name", "")
        dtype = col.get("type", "")
        tags = []
        if name in src_pk:
            tags.append("[PK]")
        if name in explicit_fk_cols or name in suggested_fk_names:
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
    src_ctx: dict[str, Any],
    src_pk: Any,
    suggested_fk_names: set[str],
    tgt_table: dict[str, Any],
) -> list[dict[str, Any]] | None:
    """Return a hop-centric join_path or None.

    Each element: ``{"hop": N, "source": {"table": ..., "column": ...},
                                "target": {"table": ..., "column": ...}}``

    Priority:
    1. Deterministic graph path (FK/JOIN edges) — compute_join_path already
       returns None when any hop lacks column conditions.
    2. LLM inference from column annotations and suggested foreign keys.
    Returns None when neither resolves; caller falls through to Phase 2.
    """
    tgt_table_id = tgt_table["id"]

    path = compute_join_path(src_table_id, tgt_table_id)
    if path is not None and all(_hop_has_columns(h) for h in path):
        return path
    if path is not None:
        logger.debug(
            "Graph path %s → %s found but missing column conditions; trying LLM",
            src_table_name,
            tgt_table["name"],
        )

    tgt_pk = _normalize_pk(tgt_table.get("pk"))
    src_pk_list = _normalize_pk(src_pk)
    explicit_fks_to_tgt = [
        fk["column_name"]
        for fk in src_ctx.get("fks", [])
        if fk.get("target_table") == tgt_table["name"] and fk.get("column_name")
    ]

    col_lines = _src_col_lines(src_ctx, src_pk_list, suggested_fk_names)
    tgt_pk_str = ", ".join(tgt_pk) if tgt_pk else "(unknown)"
    explicit_str = ", ".join(explicit_fks_to_tgt) if explicit_fks_to_tgt else "none"

    user_msg = (
        f"Source table: {src_table_name}\n"
        f"Columns:\n{col_lines}\n\n"
        f"Explicit FKs pointing at target: {explicit_str}\n\n"
        f"Target table: {tgt_table['name']}\n"
        f"Target PK columns: {tgt_pk_str}"
    )

    result: SingleHopJoin = invoke_structured(
        messages=[SystemMessage(content=_SYSTEM), HumanMessage(content=user_msg)],
        schema=SingleHopJoin,
    )
    if not result.possible or not result.src_column:
        logger.debug(
            "LLM: no single-hop join %s → %s (%s)",
            src_table_name,
            tgt_table["name"],
            result.rationale,
        )
        return None

    logger.debug(
        "LLM inferred join %s.%s → %s.%s",
        src_table_name,
        result.src_column,
        tgt_table["name"],
        result.tgt_column,
    )
    return [
        {
            "hop": 1,
            "source": {"table": src_table_name, "column": result.src_column},
            "target": {"table": tgt_table["name"], "column": result.tgt_column},
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
