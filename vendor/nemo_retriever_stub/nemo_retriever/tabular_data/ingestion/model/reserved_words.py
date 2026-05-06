"""Stub of reserved Cypher labels and edge types used by gsf/server/datasources/dal.py.

Values are deliberately illustrative — they're only ever interpolated into
queries that target a real Neo4j instance, which is not present in stub builds.
"""

from __future__ import annotations


class Labels:
    DB = "Database"
    SCHEMA = "Schema"
    TABLE = "Table"
    COLUMN = "Column"
    VIEW = "View"


class Edges:
    CONTAINS = "CONTAINS"
    HAS_COLUMN = "HAS_COLUMN"
    REFERENCES = "REFERENCES"
