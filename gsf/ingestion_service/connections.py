# SPDX-FileCopyrightText: Copyright (c) 2024-2026, NVIDIA CORPORATION & AFFILIATES.
# All rights reserved.
# SPDX-License-Identifier: Apache-2.0

"""Resolve configured connections for the ingestion-service schedulers.

Connections come from the ``CONNECTION_STRINGS`` env var when set, otherwise
from the Neo4j catalog (Vault-resolved). Both the data and semantic schedulers
resolve through here so they see the *same* set — in particular env-var
connections, which never reach Neo4j.
"""

from __future__ import annotations

import logging
import os

from gsf.connectors.connection_string_factory import build_connection_string
from gsf.connectors.registry import create_connector
from gsf.dal.connections import list_connections

logger = logging.getLogger(__name__)


def resolve_connection_strings() -> list[str]:
    """Return the connection strings to process this pass (env var or Neo4j)."""
    raw = os.environ.get("CONNECTION_STRINGS", "")
    connections = [cs.strip() for cs in raw.split(",") if cs.strip()]
    if connections:
        logger.info("connections: using CONNECTION_STRINGS env (%d)", len(connections))
        return connections

    try:
        return [build_connection_string(conn) for conn in list_connections()]
    except Exception:
        logger.exception("connections: failed to load from Neo4j")
        return []


def _schema_filter(connection: dict) -> list[str] | None:
    """Clean optional ``schemas`` allowlist off a connection dict (None = all)."""
    raw = connection.get("schemas")
    if not isinstance(raw, list):
        return None
    schemas = [str(s).strip() for s in raw if str(s).strip()]
    return schemas or None


def resolve_connections() -> list[tuple[str, list[str] | None]]:
    """Return ``(connection_string, schema_allowlist)`` pairs for this pass.

    Env-var connections carry no schema filter (``None``). Neo4j connections may
    include an optional ``schemas`` list that scopes ingestion to those schemas.
    """
    raw = os.environ.get("CONNECTION_STRINGS", "")
    env_conns = [cs.strip() for cs in raw.split(",") if cs.strip()]
    if env_conns:
        logger.info("connections: using CONNECTION_STRINGS env (%d)", len(env_conns))
        return [(cs, None) for cs in env_conns]

    try:
        return [
            (build_connection_string(conn), _schema_filter(conn))
            for conn in list_connections()
        ]
    except Exception:
        logger.exception("connections: failed to load from Neo4j")
        return []


def resolve_database_names() -> list[str]:
    """Return the distinct database names behind the configured connections.

    The name is read from the connector (``SELECT current_database()`` on
    Postgres, etc.) so it matches exactly what ingestion used as the Neo4j /
    VDB key — the same value ``run_ingest`` derives. A connection that can't be
    opened is skipped rather than failing the whole pass.
    """
    names: list[str] = []
    for connection_string in resolve_connection_strings():
        try:
            connector = create_connector(connection_string)
        except Exception:
            logger.exception("connections: failed to open connector; skipping")
            continue
        try:
            name = str(connector.database_name or "").strip()
        finally:
            connector.close()
        if name:
            names.append(name)
    # Preserve order, drop duplicates.
    return list(dict.fromkeys(names))
