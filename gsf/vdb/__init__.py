"""GSF VDB layer.

Hosts :class:`vdb.postgres.PostgresVDB` — a Postgres + pgvector
implementation of the NV-Ingest ``VDB`` operator.
"""

from .postgres import PostgresVDB

__all__ = ["PostgresVDB"]
