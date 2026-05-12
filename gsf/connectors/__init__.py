from __future__ import annotations

from nemo_retriever.tabular_data.sql_database import SQLDatabase

from .duckdb import DuckDBDatabase
from .postgres import PostgresDatabase

__all__ = ["SQLDatabase", "DuckDBDatabase", "PostgresDatabase"]
