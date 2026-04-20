"""Connector registry — available connectors and their class mappings."""

from __future__ import annotations

import logging

from nemo_retriever.tabular_data.sql_database import SQLDatabase
from gsf.connectors.duckdb import DuckDBDatabase
from gsf.connectors.postgres import PostgresDatabase

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Connector class registry
# ---------------------------------------------------------------------------

_CONNECTOR_CLASSES: dict[str, type[SQLDatabase]] = {
    "postgres": PostgresDatabase,
    "duckdb": DuckDBDatabase,
}

# ---------------------------------------------------------------------------
# Registered connectors
# ---------------------------------------------------------------------------

_CONNECTORS: list[dict] = [
    {
        "id": "duckdb-local",
        "name": "duckdb",
        "connection_string": "../spider2.duckdb",
    },
]

_CONNECTORS_BY_ID: dict[str, dict] = {c["id"]: c for c in _CONNECTORS}


def list_connectors() -> list[dict]:
    """Return public metadata for every registered connector (no secrets)."""
    return [
        {"id": c["id"], "name": c["name"]}
        for c in _CONNECTORS
    ]


def get_connector(connector_id: str) -> SQLDatabase:
    """Instantiate and return a ready-to-use connector for a registry ID."""
    entry = _CONNECTORS_BY_ID.get(connector_id)
    if entry is None:
        raise KeyError(
            f"Connector '{connector_id}' not found. "
            f"Available: {list(_CONNECTORS_BY_ID)}"
        )

    cls = _CONNECTOR_CLASSES.get(entry["name"])
    if cls is None:
        raise ValueError(
            f"Connector '{entry['name']}' is not installed. "
            f"Available: {list(_CONNECTOR_CLASSES)}"
        )

    return cls(entry["connection_string"])
