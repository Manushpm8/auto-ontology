"""Deterministic column mapping — exclude FK columns."""

from __future__ import annotations

import re
from typing import Any

from gsf.semantic.models import ColumnAttributeSpec


def to_term_name(table_name: str) -> str:
    """CamelCase provisional Term from snake_case table name."""
    parts = re.split(r"[_\s]+", table_name.strip())
    return "".join(p.capitalize() for p in parts if p)


def fk_source_columns(fks: list[dict[str, Any]]) -> set[str]:
    return {fk["source_column"] for fk in fks if fk.get("source_column")}


def column_attribute_specs(
    columns: list[dict[str, Any]],
    fks: list[dict[str, Any]],
    suggested_fk_columns: set[str] | None = None,
) -> list[ColumnAttributeSpec]:
    """Non-FK columns mapped 1:1 to ColumnAttribute candidates."""
    fk_cols = fk_source_columns(fks)
    if suggested_fk_columns:
        fk_cols |= suggested_fk_columns
    specs: list[ColumnAttributeSpec] = []
    for col in columns:
        name = col.get("name", "")
        if not name or name in fk_cols:
            continue
        attr_name = _column_to_attr_name(name)
        specs.append(
            ColumnAttributeSpec(
                source_column=name,
                name=attr_name,
                datatype=str(col.get("data_type") or ""),
                description=col.get("description") or None,
            )
        )
    return specs


def fk_target_table_names(fks: list[dict[str, Any]]) -> list[str]:
    seen: set[str] = set()
    out: list[str] = []
    for fk in fks:
        tgt = fk.get("target_table")
        if tgt and tgt not in seen:
            seen.add(tgt)
            out.append(tgt)
    return out


def _column_to_attr_name(column_name: str) -> str:
    """Convert a physical column name to a human-readable Title Case label.

    Handles snake_case, camelCase, PascalCase, and unseparated names.
    """
    parts = re.split(r"[_\s]+", column_name.strip())
    words: list[str] = []
    for part in parts:
        words.extend(re.findall(r"[A-Z]?[a-z]+|[A-Z]+(?=[A-Z][a-z]|\d|\b)|[A-Z]+$|\d+", part))
    if not words:
        return column_name.capitalize() if column_name else column_name
    return " ".join(w.capitalize() for w in words)
