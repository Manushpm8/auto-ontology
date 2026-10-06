# SPDX-FileCopyrightText: Copyright (c) 2026, NVIDIA CORPORATION & AFFILIATES.
# All rights reserved.
# SPDX-License-Identifier: Apache-2.0

"""Deterministic column mapping — exclude FK columns."""

from __future__ import annotations

import logging
import re
from concurrent.futures import ThreadPoolExecutor, as_completed
from dataclasses import dataclass
from typing import Any

from langchain_core.messages import HumanMessage, SystemMessage

from auto_ontology.semantic.date_format import is_date_type
from auto_ontology.semantic.models import ColumnAttributeSpec, ColumnDescriptionResult
from auto_ontology.utils.llm_invoke import (
    get_llm_client,
    invoke_with_structured_output,
)

logger = logging.getLogger(__name__)

# Wide tables make the model emit one JSON object per column in a single
# structured-output response; large batches generate too many tokens and time
# out. Bucket columns into small batches and describe them concurrently so one
# slow/failed batch never wipes out the whole table's descriptions.
_DESCRIPTION_BATCH_SIZE = 15
_DESCRIPTION_MAX_WORKERS = 1

_DESCRIPTION_SYSTEM = """\
You are a data analyst documenting the columns of a relational table for a \
semantic layer. For every column you are given, do both of the following:

1. Write ONE concise sentence describing what the column represents in \
business terms.
2. Set unusable to true only when the description or the value_description \
says the column should not be used or is almost unusable: it is deprecated, \
says do not use, is not populated, is unreliable, or is almost always empty. \
A note that some records lack a value is not enough to mark it unusable. \
Missing data for inactive or historical records still leaves the column usable.

Rules:
- Use the column name, data type, sample values, description, and \
value_description as evidence.
- If a description is already provided, keep its meaning.
- Keep each description to a single, factual sentence — no speculation.
- Return exactly one entry per column provided, using the physical column name.
- Leave unusable false unless the text itself says the column is unusable \
or almost unusable."""


def _column_text(col: dict[str, Any], key: str) -> str:
    value = col.get(key)
    if value is None:
        return ""
    return str(value).strip()


def _description_repeats_column_name(col: dict[str, Any]) -> bool:
    """True when the stored description is only the physical column name.

    Ingest sometimes copies the name into the description. That is not a
    curated sentence, so the compile replaces it with the model sentence.
    """
    name = str(col.get("name") or "").strip()
    description = _column_text(col, "description")
    return bool(name) and description.casefold() == name.casefold()


def _curated_description(col: dict[str, Any]) -> str:
    """Description worth keeping, excluding a copy of the column name."""
    if _description_repeats_column_name(col):
        return ""
    return _column_text(col, "description")


@dataclass(frozen=True)
class ColumnRead:
    """One column's description and unusable judgment from the per-table LLM."""

    description: str = ""
    unusable: bool = False


def _as_column_read(value: ColumnRead | str) -> ColumnRead:
    """Accept a bare description string, which is what older stubs return."""
    if isinstance(value, str):
        return ColumnRead(description=value)
    return value


@dataclass(frozen=True)
class ColumnAttributeBuild:
    """Attribute specs plus the columns the per-table LLM judged."""

    specs: list[ColumnAttributeSpec]
    unusable_columns: tuple[str, ...]
    judged_columns: tuple[str, ...]


def to_term_name(table_name: str) -> str:
    """Return a human-readable fallback Term name from a physical table name."""
    separated = re.sub(
        r"(?<=[A-Z])(?=[A-Z][a-z])",
        " ",
        table_name.strip(),
    )
    separated = re.sub(r"(?<=[a-z0-9])(?=[A-Z])", " ", separated)
    parts = re.split(r"[^A-Za-z0-9]+", separated)
    return " ".join(
        part if part.isupper() else part.capitalize() for part in parts if part
    )


_FORMAT_MARKER = "format:"


def fk_source_columns(fks: list[dict[str, Any]]) -> set[str]:
    return {fk["source_column"] for fk in fks if fk.get("source_column")}


def _get_column_samples(
    col: dict[str, Any],
    columns_profiling_samples: dict[str, dict[str, Any]],
) -> list[str]:
    """Profiled sample values for a column.

    Date/time columns are excluded — concrete dates add no business meaning
    (to an LLM description prompt, or as a stand-in when no description
    needed to be generated).
    """
    if is_date_type(col.get("data_type")):
        return []
    name = col.get("name", "")
    samples = (columns_profiling_samples.get(name) or {}).get("sample_values") or []
    return [str(s) for s in samples]


def _add_samples_suffix(text: str, samples: list[str]) -> str:
    """Append " — samples: v1, v2" to *text* when samples are present."""
    if not samples:
        return text
    return f"{text} — samples: {', '.join(samples)}"


def _date_format_clause(
    col: dict[str, Any],
    columns_profiling_samples: dict[str, dict[str, Any]],
) -> str | None:
    """How stored values are written, when a single notation fits them all.

    ``format`` is the column's storage notation — currently filled only for
    dates, but the same property would hold an id or address pattern later.
    """
    profile = columns_profiling_samples.get(col.get("name", "")) or {}
    notation = profile.get("format") or col.get("format")
    if not notation:
        return None
    return f"{_FORMAT_MARKER} {notation}"


def _add_date_format_clause(text: str, clause: str | None) -> str:
    """Append the notation, idempotently on its own marker."""
    if not clause or _FORMAT_MARKER in text:
        return text
    return f"{text} — {clause}" if text else clause


def _enrich_description(
    col: dict[str, Any],
    columns_profiling_samples: dict[str, dict[str, Any]],
    text: str | None,
) -> str | None:
    """Attach date notation (and, for existing descriptions, samples)."""
    enriched = _add_date_format_clause(
        text or "", _date_format_clause(col, columns_profiling_samples)
    )
    return enriched or None


def _column_prompt_line(
    col: dict[str, Any],
    columns_profiling_samples: dict[str, dict[str, Any]],
) -> str:
    """One column for the description prompt, including both text fields."""
    name = col.get("name", "")
    data_type = col.get("data_type") or "unknown"
    samples = _get_column_samples(col, columns_profiling_samples)
    line = _add_samples_suffix(f"  - {name} ({data_type})", samples)
    description = _curated_description(col)
    value_description = _column_text(col, "value_description")
    if description:
        line += f"\n    description: {description}"
    if value_description:
        line += f"\n    value_description: {value_description}"
    return line


def _describe_column_batch(
    columns: list[dict[str, Any]],
    columns_profiling_samples: dict[str, dict[str, Any]],
) -> dict[str, ColumnRead]:
    """Ask the LLM for a description and unusable judgment of one batch.

    Returns a ``{column_name: ColumnRead}`` map; empty when the call fails.
    """
    lines = [_column_prompt_line(col, columns_profiling_samples) for col in columns]

    prompt = "Columns:\n" + "\n".join(lines)

    try:
        result = invoke_with_structured_output(
            get_llm_client(temperature=0.0),
            [
                SystemMessage(content=_DESCRIPTION_SYSTEM),
                HumanMessage(content=prompt),
            ],
            ColumnDescriptionResult,
        )
    except Exception:
        logger.warning(
            "column description batch errored — proceeding without them",
            exc_info=True,
        )
        return {}
    if result is None:
        logger.warning("column description batch failed — proceeding without them")
        return {}

    # The prompt annotates each column as "<name> (<dtype>)". When a column name
    # contains spaces or parentheses, the model sometimes echoes the annotation back as
    # the column_name (e.g. "Order Date (TEXT)"). Resolve each returned name
    # to the requested physical name: exact match first, then the longest
    # requested name the returned string starts with.
    requested_names = [c.get("name", "") for c in columns if c.get("name")]
    requested_set = set(requested_names)
    out: dict[str, ColumnRead] = {}
    for d in result.descriptions:
        raw = d.column_name or ""
        desc = (d.description or "").strip()
        read = ColumnRead(description=desc, unusable=bool(d.unusable))
        if not desc and not read.unusable:
            continue
        if raw in requested_set:
            out.setdefault(raw, read)
            continue
        prefixes = [n for n in requested_names if n and raw.startswith(n)]
        if prefixes:
            out.setdefault(max(prefixes, key=len), read)
    return out


def _generate_column_descriptions(
    columns: list[dict[str, Any]],
    columns_profiling_samples: dict[str, dict[str, Any]] | None,
) -> dict[str, ColumnRead]:
    """Ask the LLM for a business description of each column.

    Columns are bucketed into small batches described concurrently, so wide
    tables don't overflow a single structured-output response (which times out).

    Returns a ``{column_name: ColumnRead}`` map; empty when no columns are
    provided. Batches that fail are simply skipped.
    """
    if not columns:
        return {}

    profiling = columns_profiling_samples or {}
    batches = [
        columns[i : i + _DESCRIPTION_BATCH_SIZE]
        for i in range(0, len(columns), _DESCRIPTION_BATCH_SIZE)
    ]

    descriptions: dict[str, ColumnRead] = {}
    if len(batches) == 1:
        return _describe_column_batch(batches[0], profiling)

    max_workers = min(len(batches), _DESCRIPTION_MAX_WORKERS)
    with ThreadPoolExecutor(max_workers=max_workers) as pool:
        futures = [
            pool.submit(_describe_column_batch, batch, profiling) for batch in batches
        ]
        for future in as_completed(futures):
            descriptions.update(future.result())

    return descriptions


def _columns_to_read(
    columns: list[dict[str, Any]], fk_cols: set[str]
) -> list[dict[str, Any]]:
    """Columns the per-table LLM must see.

    Every non-FK column still needs a description. A column that already has
    a description or a value_description — including a foreign-key column,
    which never becomes an attribute — still has to be judged for unusable.
    """
    selected = []
    for col in columns:
        name = col.get("name") or ""
        if not name:
            continue
        has_text = bool(_curated_description(col)) or bool(
            _column_text(col, "value_description")
        )
        if name not in fk_cols or has_text:
            selected.append(col)
    return selected


def build_column_attributes(
    columns: list[dict[str, Any]],
    fks: list[dict[str, Any]],
    suggested_fk_columns: set[str] | None = None,
    columns_profiling_samples: dict[str, dict[str, Any]] | None = None,
) -> ColumnAttributeBuild:
    """Non-FK, usable columns mapped 1:1 to ColumnAttribute candidates.

    ``unusable_columns`` are the physical names the LLM flagged. They get no
    spec, whether or not they would have been foreign keys.
    """
    fk_cols = fk_source_columns(fks)
    if suggested_fk_columns:
        fk_cols |= suggested_fk_columns

    reads = {
        name: _as_column_read(value)
        for name, value in _generate_column_descriptions(
            _columns_to_read(columns, fk_cols), columns_profiling_samples
        ).items()
    }
    unusable = tuple(name for name, read in reads.items() if read.unusable)
    unusable_names = set(unusable)

    candidates = [
        col
        for col in columns
        if (name := col.get("name", ""))
        and name not in fk_cols
        and name not in unusable_names
    ]

    # A curated description is kept. The LLM still saw that column so it
    # could judge unusable, but its generated sentence is not used. A
    # description that only repeats the column name is not curated: ingest
    # copied the name, and the model sentence replaces it.
    cols_with_description = [col for col in candidates if _curated_description(col)]
    cols_without_description = [
        col for col in candidates if not _curated_description(col)
    ]
    llm_descriptions = {
        name: read.description for name, read in reads.items() if read.description
    }

    profiling = columns_profiling_samples or {}

    def _build_column_attribute_spec(
        col: dict[str, Any], description: str | None
    ) -> ColumnAttributeSpec:
        name = col["name"]
        return ColumnAttributeSpec(
            source_column=name,
            name=_column_to_attr_name(name),
            datatype=str(col.get("data_type") or ""),
            description=description,
            value_description=_column_text(col, "value_description") or None,
        )

    # Columns that already have a description keep it. The LLM saw them only
    # to judge unusable, so that sentence never included the samples — bundle
    # those in now via the same " — samples: ..." suffix.
    specs = [
        _build_column_attribute_spec(
            col,
            _enrich_description(
                col,
                profiling,
                _add_samples_suffix(
                    col["description"], _get_column_samples(col, profiling)
                ),
            ),
        )
        for col in cols_with_description
    ]
    # Columns without a description: keep the LLM description as generated
    specs += [
        _build_column_attribute_spec(
            col,
            _enrich_description(col, profiling, llm_descriptions.get(col["name"])),
        )
        for col in cols_without_description
    ]
    return ColumnAttributeBuild(
        specs=specs,
        unusable_columns=unusable,
        judged_columns=tuple(reads),
    )


def column_attribute_specs(
    columns: list[dict[str, Any]],
    fks: list[dict[str, Any]],
    suggested_fk_columns: set[str] | None = None,
    columns_profiling_samples: dict[str, dict[str, Any]] | None = None,
) -> list[ColumnAttributeSpec]:
    """Non-FK, usable columns mapped 1:1 to ColumnAttribute candidates."""
    return build_column_attributes(
        columns,
        fks,
        suggested_fk_columns=suggested_fk_columns,
        columns_profiling_samples=columns_profiling_samples,
    ).specs


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
    parts = re.split(r"[_\s]+", column_name.strip())
    if not parts:
        return column_name
    return parts[0].lower() + "".join(p.capitalize() for p in parts[1:] if p)
