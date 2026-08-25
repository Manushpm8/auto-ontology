# SPDX-FileCopyrightText: Copyright (c) 2026, NVIDIA CORPORATION & AFFILIATES.
# All rights reserved.
# SPDX-License-Identifier: Apache-2.0

"""Resolve a described value to one physical column and one live database row."""

from __future__ import annotations

import logging
import re
import time
from typing import Any

from nemo_retriever.tabular_data.sql_database import SQLDatabase
from sqlglot import exp

from gsf.dal.attributes import fetch_attr_column_contexts
from gsf.retrieval.data_access.semantic_search import search_semantic_index
from gsf.retrieval.text_to_sql.connector_routing import (
    resolve_target_database_name,
)
from gsf.retrieval.text_to_sql.db_probe.executor import ProbeExecutor
from gsf.semantic.constants import LABEL_COLUMN_ATTRIBUTE
from gsf.utils.sample_values import parse_sample_values
from gsf.utils.sql_dialects import get_sqlglot_dialect

logger = logging.getLogger(__name__)

_TOKEN_RE = re.compile(r"[^\W_]+", re.UNICODE)


def find_column_value(
    *,
    retriever: object,
    connectors: list[SQLDatabase],
    value: str,
    description: str,
    database_name: str | None = None,
) -> dict[str, Any]:
    started = time.perf_counter()
    logger.info(
        "Value search started (database=%s, connectors=%d)",
        database_name or "auto",
        len(connectors),
    )
    try:
        return _find_column_value(
            retriever=retriever,
            connectors=connectors,
            value=value,
            description=description,
            database_name=database_name,
        )
    finally:
        logger.info(
            "Value search finished (database=%s, elapsed_ms=%d)",
            database_name or "auto",
            int((time.perf_counter() - started) * 1000),
        )


def _find_column_value(
    *,
    retriever: object,
    connectors: list[SQLDatabase],
    value: str,
    description: str,
    database_name: str | None = None,
) -> dict[str, Any]:
    """Return one qualified field and its exact stored value, or a null result."""
    started = time.perf_counter()
    target_database = None
    if database_name:
        target_database = resolve_target_database_name(database_name, connectors)

    query = _semantic_query(value, description)
    hits = search_semantic_index(
        retriever,
        query,
        label_filter=[LABEL_COLUMN_ATTRIBUTE],
        per_label_k=1,
        database_name=target_database,
    )
    semantic_ms = int((time.perf_counter() - started) * 1000)
    if not hits:
        logger.info("Value search found no semantic field in %d ms", semantic_ms)
        return _no_match()

    hit_id = str(hits[0].get("id") or "")
    attr_ids = [hit_id] if hit_id else []
    contexts = fetch_attr_column_contexts(
        attr_ids,
        database_name=target_database,
    )
    context = contexts.get(hit_id)
    if not _is_complete_context(context):
        logger.info(
            "Value search top semantic hit had no physical column context "
            "(semantic_ms=%d)",
            semantic_ms,
        )
        return _no_match()

    selected_database = resolve_target_database_name(
        str(context["database_name"]),
        connectors,
    )
    sampled_value = _find_sample_value(context.get("sample_values"), value)
    if sampled_value is not None:
        logger.info(
            "Value search matched a stored column sample "
            "(database=%s, field=%s, semantic_ms=%d)",
            selected_database,
            _qualified_field(context),
            semantic_ms,
        )
        return {
            "field": _qualified_field(context),
            "value": sampled_value,
        }

    connector = next(
        connector
        for connector in connectors
        if str(getattr(connector, "database_name", "")) == selected_database
    )
    sql = build_value_lookup_sql(
        context,
        value,
        getattr(connector, "dialect", None),
    )
    if not sql:
        return _no_match()

    probe_started = time.perf_counter()
    with ProbeExecutor(connector, max_calls=1, max_rows=1) as executor:
        result = executor.run(sql, purpose="find column value")
    probe_ms = int((time.perf_counter() - probe_started) * 1000)
    if not result["ok"]:
        raise RuntimeError(result.get("error") or "Database value lookup failed.")

    rows = result.get("rows") or []
    logger.info(
        "Value search completed (database=%s, field=%s, semantic_ms=%d, probe_ms=%d)",
        selected_database,
        _qualified_field(context),
        semantic_ms,
        probe_ms,
    )
    if not rows:
        return _no_match()
    stored_value = next(iter(rows[0].values()), None)
    if stored_value is None:
        return _no_match()
    return {
        "field": _qualified_field(context),
        "value": stored_value,
    }


def build_value_lookup_sql(
    context: dict[str, Any],
    value: str,
    dialect: str | None,
) -> str | None:
    """Build a quoted, case-insensitive lookup ordered to prefer short matches."""
    table_name = str(context.get("table_name") or "")
    column_name = str(context.get("col_name") or "")
    if not table_name or not column_name or not value.strip():
        return None

    schema_name = str(context.get("schema_name") or "")
    table = exp.Table(
        this=exp.to_identifier(table_name),
        db=exp.to_identifier(schema_name) if schema_name else None,
    )
    text_column = exp.cast(exp.column(column_name), "TEXT")
    normalized_column = exp.Lower(this=text_column.copy())
    tokens = _search_tokens(value)
    predicates = [
        normalized_column.copy().like(exp.Literal.string(f"%{token}%"))
        for token in tokens
    ]
    predicate = exp.and_(*predicates)
    query = (
        exp.select(text_column.copy().as_("matched_value"))
        .from_(table)
        .where(predicate)
        .order_by(
            exp.Length(this=text_column.copy()),
            normalized_column.copy(),
        )
        .limit(1)
    )
    dialect_name = get_sqlglot_dialect(
        dialect,
        preserve_unknown=True,
    )
    try:
        return query.sql(dialect=dialect_name, identify=True)
    except ValueError:
        return query.sql(identify=True)


def _semantic_query(value: str, description: str) -> str:
    """Describe the field intent while retaining the literal as useful context."""
    return f"Description: {description.strip()}\nValue: {value.strip()}"


def _search_tokens(value: str) -> list[str]:
    """Return unique normalized tokens suitable for literal LIKE patterns."""
    tokens = [token.casefold() for token in _TOKEN_RE.findall(value)]
    return list(dict.fromkeys(tokens)) or [value.strip().casefold()]


def _find_sample_value(raw_samples: Any, value: str) -> str | None:
    """Return the shortest stored sample containing every requested token."""
    samples = parse_sample_values(raw_samples) or []
    tokens = _search_tokens(value)
    matches = [
        sample
        for sample in samples
        if all(token in sample.casefold() for token in tokens)
    ]
    if not matches:
        return None
    return min(matches, key=lambda sample: (len(sample), sample.casefold()))


def _is_complete_context(context: dict[str, Any] | None) -> bool:
    return bool(
        context
        and context.get("database_name")
        and context.get("table_name")
        and context.get("col_name")
    )


def _qualified_field(context: dict[str, Any]) -> str:
    parts = [
        context.get("schema_name"),
        context.get("table_name"),
        context.get("col_name"),
    ]
    return ".".join(str(part) for part in parts if part)


def _no_match() -> dict[str, None]:
    return {"field": None, "value": None}


__all__ = ["build_value_lookup_sql", "find_column_value"]
