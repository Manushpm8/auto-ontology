"""Connector registry — available connectors and their class mappings."""

from __future__ import annotations

import logging
from typing import TypedDict

from nemo_retriever.tabular_data.sql_database import SQLDatabase
from connectors.duckdb import DuckDBDatabase
from connectors.postgres import PostgresDatabase

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


class ConnectorEntry(TypedDict):
    name: str
    connection_string: str


_CONNECTORS: list[ConnectorEntry] = [
    {
        "name": "duckdb",
        "connection_string": "../spider2.duckdb",
    },
    {
        "name": "postgres",
        "connection_string": "postgresql://gsf:gsf@localhost:5432/testdb",
    },
]

_CONNECTORS_BY_NAME: dict[str, ConnectorEntry] = {c["name"]: c for c in _CONNECTORS}


def get_connector(connector_name: str) -> SQLDatabase:
    """Instantiate and return a ready-to-use connector by name."""
    entry = _CONNECTORS_BY_NAME.get(connector_name)
    if entry is None:
        raise KeyError(
            f"Connector '{connector_name}' not found. "
            f"Available: {list(_CONNECTORS_BY_NAME)}"
        )

    cls = _CONNECTOR_CLASSES.get(entry["name"])
    if cls is None:
        raise ValueError(
            f"Connector '{entry['name']}' is not installed. "
            f"Available: {list(_CONNECTOR_CLASSES)}"
        )

    return cls(entry["connection_string"])
