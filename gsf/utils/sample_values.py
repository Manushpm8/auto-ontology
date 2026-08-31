# SPDX-FileCopyrightText: Copyright (c) 2024-2026, NVIDIA CORPORATION & AFFILIATES.
# All rights reserved.
# SPDX-License-Identifier: Apache-2.0

"""Helpers for Column.sample_values stored on catalog nodes."""

from __future__ import annotations

import json
from typing import Any


def parse_sample_values(raw: Any) -> list[Any] | None:
    """Normalize Column.sample_values (JSON string or list) to a Python list.

    Values keep the type they were profiled as (see
    ``gsf.semantic.visit_enter._json_ready_sample``) and Neo4j stores them in a
    native property array, so a numeric column reads back as ``[10, 20]``
    rather than ``["10", "20"]``. Legacy nodes that predate the native-array
    switch still hold a JSON string; decoding one yields the same scalar types.
    ``None`` / JSON ``null`` entries are dropped so no consumer has to guard
    against them.

    Callers that need display text want ``stringify_sample_values`` instead.
    """
    if raw is None:
        return None
    if isinstance(raw, list):
        values: list[Any] = raw
    elif isinstance(raw, str):
        try:
            parsed = json.loads(raw)
        except (TypeError, ValueError):
            return None
        if not isinstance(parsed, list):
            return None
        values = parsed
    else:
        return None
    return [value for value in values if value is not None]


def stringify_sample_values(
    raw: Any,
    *,
    max_len: int | None = None,
) -> list[str] | None:
    """Render sample values as the string list prompts and the API expect.

    ``None`` passes through, so a column with no ``sample_values`` property
    stays distinguishable from one profiled as having no samples. *max_len*
    drops values whose rendered form exceeds the cap, which is what keeps
    prose and blobs out of prompts.
    """
    values = parse_sample_values(raw)
    if values is None:
        return None
    rendered = [render_sample_value(value) for value in values]
    if max_len is None:
        return rendered
    return [value for value in rendered if len(value) <= max_len]


def _sample_family(value: Any) -> str | None:
    """Group a sample by the property type Neo4j could store it as.

    ``bool`` is tested before ``int`` because Python booleans are ints, and
    ``[True, 1]`` is exactly the mix a property array cannot hold. Returns
    ``None`` for a value no array can hold natively — the lists and dicts
    Postgres returns for array and JSON columns.
    """
    if isinstance(value, bool):
        return "bool"
    if isinstance(value, (int, float)):
        return "number"
    if isinstance(value, str):
        return "str"
    return None


def as_neo4j_property_array(values: list[Any]) -> list[Any]:
    """Reduce sample values to a homogeneous Neo4j property array.

    A property array holds one primitive type, which a single SQL column does
    not guarantee: semi-structured types (Postgres JSONB, Snowflake VARIANT)
    hold any JSON value, a SQLite column declared without an affinity keeps
    whatever was inserted, and an ordinary Postgres NUMERIC arrives here as a
    mix of int and float (see
    ``gsf.semantic.visit_enter._json_ready_sample``).

    A column of one scalar family survives as itself, with int and float
    widening to float so a numeric column stays numeric. A column holding only
    containers renders to JSON text, since no array can nest one. A column
    whose families disagree yields nothing at all: samples that cannot say
    which type the column holds would misinform every reader of them.
    ``None`` entries are dropped throughout.

    Every write of ``Column.sample_values`` goes through this, whether the
    values were profiled from a live warehouse or supplied by a model import.
    """
    kept = [value for value in values if value is not None]
    if not kept:
        return []
    families = {_sample_family(value) for value in kept}
    if len(families) > 1:
        return []
    if families == {None}:
        return [render_sample_value(value) for value in kept]
    if families == {"number"} and any(isinstance(value, float) for value in kept):
        return [float(value) for value in kept]
    return kept


def render_sample_value(value: Any) -> str:
    """Render one sample value as display text.

    Non-scalars go through ``json.dumps`` rather than ``str()`` so a JSONB
    sample reads as ``{"a": 1}`` instead of Python's ``{'a': 1}``. This is also
    how a container is persisted, since a Neo4j property array cannot nest one
    (see ``gsf.dal.datasources.store_column_sample_values``) — sharing the
    rendering keeps the stored form and the prompt form identical.
    """
    if isinstance(value, str):
        return value
    if isinstance(value, (bool, int, float)):
        return str(value)
    try:
        return json.dumps(value, sort_keys=True, default=str)
    except (TypeError, ValueError):
        return str(value)
