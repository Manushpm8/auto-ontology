# SPDX-FileCopyrightText: Copyright (c) 2026, NVIDIA CORPORATION & AFFILIATES.
# All rights reserved.
# SPDX-License-Identifier: Apache-2.0

"""Deterministic column mapping — exclude FK columns."""

from __future__ import annotations

import json
import logging
import re
from concurrent.futures import ThreadPoolExecutor, as_completed
from typing import Any

from langchain_core.messages import HumanMessage, SystemMessage

from auto_ontology.semantic.date_format import is_date_type
from auto_ontology.semantic.models import (
    ColumnAttributeSpec,
    ColumnDescription,
    ColumnDescriptionResult,
)
from auto_ontology.utils.llm_invoke import (
    get_non_reasoning_llm_client,
    invoke_with_structured_output,
)
from auto_ontology.utils.sample_values import stringify_sample_values

logger = logging.getLogger(__name__)

# Wide tables make the model emit one JSON object per column in a single
# structured-output response; large batches generate too many tokens and time
# out. Bucket columns into small batches and describe them concurrently so one
# slow/failed batch never wipes out the whole table's descriptions.
_DESCRIPTION_BATCH_SIZE = 15
_DESCRIPTION_MAX_WORKERS = 1

_DESCRIPTION_SYSTEM = """\
You normalize relational-column metadata for a semantic layer. Return exactly \
one entry per supplied column, using its physical column name.

For each entry:
- description: one concise factual sentence containing only what the column \
represents in business terms. Remove embedded examples, constraints, formulas, \
and usage instructions from it. If no source description exists, infer a \
conservative description from the name, type, and database samples.
- sample_values: only examples embedded inside source_description. Do not copy \
catalog_sample_values or database_sample_values into this field.
- constraints: explicit validity, padding, representation, or formatting rules \
from source_description or existing_constraints. Do not infer constraints from \
observed samples.
- usage_evidence: explicit formulas, relationships, or how/when-to-use guidance \
from source_description or existing_usage_evidence. Preserve equations exactly.

Use empty strings/lists when a category has no evidence. Never speculate."""


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


def fk_source_columns(fks: list[dict[str, Any]]) -> set[str]:
    return {fk["source_column"] for fk in fks if fk.get("source_column")}


def _describe_column_batch(
    columns: list[dict[str, Any]],
    columns_profiling_samples: dict[str, dict[str, Any]],
) -> dict[str, ColumnDescription]:
    """Ask the LLM to normalize a single batch of columns.

    Returns a ``{column_name: metadata}`` map; empty when the call fails.
    """
    lines = []
    for col in columns:
        name = col.get("name", "")
        profile = columns_profiling_samples.get(name) or {}
        payload = {
            "column_name": name,
            "data_type": col.get("data_type") or "unknown",
            "source_description": col.get("description") or "",
            "existing_constraints": col.get("constraints") or "",
            "existing_usage_evidence": col.get("usage_evidence") or "",
            "catalog_sample_values": stringify_sample_values(col.get("sample_values"))
            or [],
            "database_sample_values": (
                []
                if is_date_type(col.get("data_type"))
                else stringify_sample_values(
                    profile.get("database_sample_values", profile.get("sample_values"))
                )
                or []
            ),
        }
        lines.append(json.dumps(payload, ensure_ascii=False))

    prompt = "Columns (one JSON object per line):\n" + "\n".join(lines)

    try:
        result = invoke_with_structured_output(
            get_non_reasoning_llm_client(temperature=0.0),
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

    # Models sometimes echo type/context after the physical name, especially
    # when that name contains spaces or parentheses. Resolve exact matches
    # first, then the longest requested name that prefixes the returned text.
    requested_names = [c.get("name", "") for c in columns if c.get("name")]
    requested_set = set(requested_names)
    out: dict[str, ColumnDescription] = {}
    for d in result.descriptions:
        raw = d.column_name or ""
        resolved_name: str | None = None
        if raw in requested_set:
            resolved_name = raw
        else:
            prefixes = [n for n in requested_names if n and raw.startswith(n)]
            if prefixes:
                resolved_name = max(prefixes, key=len)
        if resolved_name:
            normalized = d.model_copy(
                update={
                    "column_name": resolved_name,
                    "description": (d.description or "").strip(),
                    "constraints": (d.constraints or "").strip(),
                    "usage_evidence": (d.usage_evidence or "").strip(),
                    "sample_values": [
                        value.strip()
                        for value in d.sample_values
                        if isinstance(value, str) and value.strip()
                    ],
                }
            )
            out.setdefault(resolved_name, normalized)
    return out


def _generate_column_descriptions(
    columns: list[dict[str, Any]],
    columns_profiling_samples: dict[str, dict[str, Any]] | None,
) -> dict[str, ColumnDescription]:
    """Ask the LLM to normalize metadata for each column.

    Columns are bucketed into small batches described concurrently, so wide
    tables don't overflow a single structured-output response (which times out).

    Returns a ``{column_name: metadata}`` map; empty when no columns are
    provided. Batches that fail are simply skipped.
    """
    if not columns:
        return {}

    profiling = columns_profiling_samples or {}
    batches = [
        columns[i : i + _DESCRIPTION_BATCH_SIZE]
        for i in range(0, len(columns), _DESCRIPTION_BATCH_SIZE)
    ]

    descriptions: dict[str, ColumnDescription] = {}
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


def normalize_column_metadata(
    columns: list[dict[str, Any]],
    columns_profiling_samples: dict[str, dict[str, Any]] | None = None,
) -> dict[str, ColumnDescription]:
    """Normalize all column metadata using the existing batched LLM call."""
    return _generate_column_descriptions(columns, columns_profiling_samples)


def column_attribute_specs(
    columns: list[dict[str, Any]],
    fks: list[dict[str, Any]],
    suggested_fk_columns: set[str] | None = None,
    columns_profiling_samples: dict[str, dict[str, Any]] | None = None,
    normalized_metadata: dict[str, ColumnDescription] | None = None,
) -> list[ColumnAttributeSpec]:
    """Non-FK columns mapped 1:1 to ColumnAttribute candidates."""
    fk_cols = fk_source_columns(fks)
    if suggested_fk_columns:
        fk_cols |= suggested_fk_columns

    candidates = [
        col for col in columns if (name := col.get("name", "")) and name not in fk_cols
    ]

    normalized = (
        normalized_metadata
        if normalized_metadata is not None
        else normalize_column_metadata(columns, columns_profiling_samples)
    )

    def _build_column_attribute_spec(
        col: dict[str, Any],
    ) -> ColumnAttributeSpec:
        name = col["name"]
        normalized_description = normalized.get(name)
        description = (
            normalized_description.description
            if isinstance(normalized_description, ColumnDescription)
            and normalized_description.description
            else normalized_description
            if isinstance(normalized_description, str) and normalized_description
            else col.get("description")
        )
        return ColumnAttributeSpec(
            source_column=name,
            name=_column_to_attr_name(name),
            datatype=str(col.get("data_type") or ""),
            description=description,
            constraints=(
                normalized_description.constraints
                if isinstance(normalized_description, ColumnDescription)
                else col.get("constraints")
            )
            or None,
            usage_evidence=(
                normalized_description.usage_evidence
                if isinstance(normalized_description, ColumnDescription)
                else col.get("usage_evidence")
            )
            or None,
        )

    return [_build_column_attribute_spec(col) for col in candidates]


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
