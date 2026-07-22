# SPDX-FileCopyrightText: Copyright (c) 2024-2026, NVIDIA CORPORATION & AFFILIATES.
# All rights reserved.
# SPDX-License-Identifier: Apache-2.0

"""Shared helpers for connector connection-string parsing."""

from __future__ import annotations

from urllib.parse import parse_qs, unquote


def query_param(query: dict[str, list[str]], key: str) -> str | None:
    """Return the first non-empty value for *key* in a ``parse_qs`` mapping."""
    values = query.get(key) or []
    for raw in values:
        value = unquote(raw).strip()
        if value:
            return value
    return None


def metadata_database_from_query(query: dict[str, list[str]]) -> str | None:
    """Optional logical name used for Neo4j / VDB / eval routing.

    When multiple connectors would otherwise share the same physical
    ``database_name`` (or when the eval ``db_id`` must differ from the backend
    name), set ``?metadata_database=...`` on the connection string.
    """
    return query_param(query, "metadata_database")


def parse_query(connection_string: str) -> dict[str, list[str]]:
    """Parse the query string of *connection_string* into a ``parse_qs`` dict."""
    from urllib.parse import urlparse

    return parse_qs(urlparse(connection_string).query)
