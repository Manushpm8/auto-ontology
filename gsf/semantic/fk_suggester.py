"""LLM inference for columns that look like foreign keys."""

from __future__ import annotations

import json
from typing import Any

from langchain_core.messages import HumanMessage, SystemMessage

from gsf.semantic.deterministic import fk_source_columns
from gsf.semantic.llm import invoke_structured
from gsf.semantic.models import PotentialFkResult, PotentialFkSuggestion

_SYSTEM = """\
You review relational table metadata and identify columns that are likely foreign keys
but are not already declared as FOREIGN_KEY or primary-key columns.

A likely foreign key typically:
- ends with _id or Id and has a meaningful prefix naming a different entity (e.g. customer_id,
  orderId) — NOT a bare "id"/"_id"/"Id" column or one whose prefix matches the table name
- has a description that contains words like "references", "identifier of", or names another
- non unique sample values
- has an integer or string type consistent with identifiers
- semantically points to a row in another table

Return only column names from the candidate list provided. Omit columns that are
measures, timestamps, free text, flags, or otherwise unlikely to reference another table.
Return an empty list when no column qualifies."""


def _pk_column_names(table: dict[str, Any]) -> set[str]:
    pk = table.get("pk") or []
    if isinstance(pk, str):
        return {pk} if pk else set()
    return {str(name) for name in pk if name}


def _candidate_columns(
    columns: list[dict[str, Any]],
    *,
    excluded: set[str],
) -> list[dict[str, Any]]:
    return [
        col for col in columns if (name := col.get("name")) and name not in excluded
    ]


def _format_sample_values(raw: str | None) -> str:
    """Return a 'samples: ...' string filtered to ≤30-char non-null values, or empty."""
    if not raw:
        return ""
    try:
        values = json.loads(raw)
        non_null = [str(v) for v in values if v is not None and len(str(v)) <= 30]
        return ("samples: " + ", ".join(non_null)) if non_null else ""
    except Exception:
        return ""


def _has_unique_sample_values(raw: str | None) -> bool:
    """Return True when all non-null sample values are distinct (no repeats)."""
    if not raw:
        return False
    try:
        str_values = [str(v) for v in json.loads(raw) if v is not None]
        return len(str_values) > 1 and len(set(str_values)) == len(str_values)
    except Exception:
        return False


def _format_column_line(col: dict[str, Any]) -> str:
    desc = col.get("description") or ""
    suffix = f" — {desc}" if desc else ""
    sample_str = _format_sample_values(col.get("sample_values"))
    if sample_str:
        suffix += f" [{sample_str}]"
    return f"  - {col['name']} ({col.get('data_type', '')}){suffix}"


def suggest_potential_foreign_keys(
    table: dict[str, Any],
    ctx: dict[str, Any],
) -> PotentialFkResult:
    """Ask the LLM which non-PK, non-declared-FK columns may be foreign keys."""
    columns = ctx.get("columns", [])
    fks = ctx.get("fks", [])
    pk_names = _pk_column_names(table)
    known_fk_names = fk_source_columns(fks)
    excluded = pk_names | known_fk_names
    candidates = _candidate_columns(columns, excluded=excluded)

    if not candidates:
        return PotentialFkResult()

    schema_name = table.get("schema_name") or ""
    table_header = f"{schema_name}.{table['name']}" if schema_name else table["name"]
    known_fk_block = ", ".join(sorted(known_fk_names)) if known_fk_names else "(none)"
    pk_block = ", ".join(sorted(pk_names)) if pk_names else "(none)"
    candidate_lines = "\n".join(_format_column_line(col) for col in candidates)

    prompt = (
        f"Table: {table_header}\n"
        f"Description: {table.get('description') or ''}\n"
        f"Primary key columns (exclude from suggestions): {pk_block}\n"
        f"Known foreign key columns (exclude from suggestions): {known_fk_block}\n"
        f"Candidate columns:\n{candidate_lines}\n"
    )

    try:
        result = invoke_structured(
            [SystemMessage(content=_SYSTEM), HumanMessage(content=prompt)],
            PotentialFkResult,
            temperature=0.0,
        )
    except Exception:
        return PotentialFkResult()

    allowed = {col["name"] for col in candidates}
    filtered: list[PotentialFkSuggestion] = []
    seen: set[str] = set()
    for item in result.suggestions:
        name = item.column_name.strip()
        if not name or name in seen or name not in allowed:
            continue
        seen.add(name)
        filtered.append(
            PotentialFkSuggestion(column_name=name, rationale=item.rationale.strip())
        )
    for col in candidates:
        name = col.get("name", "")
        if name in seen:
            continue
        if (col.get("data_type") or "").lower() == "uuid" and not _has_unique_sample_values(
            col.get("sample_values")
        ):
            seen.add(name)
            filtered.append(
                PotentialFkSuggestion(
                    column_name=name,
                    rationale="uuid type with repeated sample values (likely FK reference)",
                )
            )

    return PotentialFkResult(suggestions=filtered)
