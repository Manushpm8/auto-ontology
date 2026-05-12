"""Stub of nemo_retriever.tabular_data.neo4j."""

from __future__ import annotations

from typing import Any


class _Neo4jConnection:
    """Surface used by gsf/server/main.py: ``neo4j_connection._conn``."""

    def __init__(self) -> None:
        self._conn: Any = None

    def query_read(self, _query: str, **_params: Any) -> list[dict[str, Any]]:
        raise NotImplementedError(
            "nemo_retriever stub: neo4j_connection.query_read is not implemented."
        )

    def query_write(self, _query: str, **_params: Any) -> list[dict[str, Any]]:
        raise NotImplementedError(
            "nemo_retriever stub: neo4j_connection.query_write is not implemented."
        )

    def close(self) -> None:
        self._conn = None


# Module-level singleton exposed by the real package.
neo4j_connection = _Neo4jConnection()


def get_neo4j_conn() -> _Neo4jConnection:
    """Return the singleton — real package likely lazy-initialises a driver here."""
    return neo4j_connection
