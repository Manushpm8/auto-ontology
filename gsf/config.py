"""Env-derived configuration helpers shared across server and dev-tools."""

from __future__ import annotations

import os


def get_postgres_connection_string(database: str | None = None) -> str:
    """Build the local Postgres URL from ``POSTGRES_*`` env vars.

    ``database`` overrides ``POSTGRES_DATABASE`` when given (useful for
    multi-DB tools that target several databases on the same instance).
    """
    host = os.environ.get("POSTGRES_HOST", "localhost")
    port = os.environ.get("POSTGRES_PORT", "5432")
    user = os.environ["POSTGRES_USER"]
    password = os.environ["POSTGRES_PASSWORD"]
    db = database or os.environ.get("POSTGRES_DATABASE", "gsf")
    return f"postgresql://{user}:{password}@{host}:{port}/{db}"
