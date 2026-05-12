"""Stub of nemo_retriever.tabular_data.sql_database.SQLDatabase."""

from __future__ import annotations

from abc import ABC, abstractmethod
from typing import Any, Optional


class SQLDatabase(ABC):
    """Abstract base class — must match the real ABC closely enough that
    ``gsf.connectors.{postgres,duckdb}`` can subclass it without import errors.
    """

    @property
    @abstractmethod
    def dialect(self) -> str: ...

    @abstractmethod
    def execute(self, sql: str, parameters: Optional[list] = None) -> Any: ...

    def get_tables(self) -> Any:
        raise NotImplementedError

    def get_columns(self) -> Any:
        raise NotImplementedError

    def get_views(self) -> Any:
        raise NotImplementedError

    def get_pks(self) -> Any:
        raise NotImplementedError

    def get_fks(self) -> Any:
        raise NotImplementedError

    def get_queries(self) -> Any:
        raise NotImplementedError

    def close(self) -> None:
        return None
