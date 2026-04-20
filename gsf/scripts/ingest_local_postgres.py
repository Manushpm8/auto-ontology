"""Ingest the local docker-compose Postgres into Neo4j via NeMo Retriever.

Run after ``docker compose up -d`` with the default credentials. Each physical
Postgres database is ingested under the display name that used to live in
Neo4j (e.g. ``northwind_dw`` -> ``Northwind DW``).

Usage::

    PYTHONPATH=gsf uv run --no-sync python -m scripts.ingest_local_postgres
"""

from __future__ import annotations

import logging
import os
from typing import Iterable

from nemo_retriever.tabular_data.ingestion.extract_data import (
    data_for_populate_tabular,
)
from nemo_retriever.tabular_data.ingestion.write_to_graph import populate_tabular_data

from server.connectors.postgres import PostgresDatabase
from server.env import load_server_env

logger = logging.getLogger("scripts.ingest_local_postgres")

DEFAULT_POSTGRES_HOST = "localhost"
DEFAULT_POSTGRES_PORT = 5432
DEFAULT_POSTGRES_USER = "gsf"
DEFAULT_POSTGRES_PASSWORD = "gsf"

# (physical_db_name, display_name)
SEEDED_DATABASES: tuple[tuple[str, str], ...] = (
    ("northwind_dw", "Northwind DW"),
    ("ops_oltp", "Ops OLTP"),
    ("sales_lake", "Sales Lake"),
    ("testdb", "testdb"),
)


def _conn_string(db: str) -> str:
    host = os.environ.get("GSF_POSTGRES_HOST", DEFAULT_POSTGRES_HOST)
    port = os.environ.get("GSF_POSTGRES_PORT", str(DEFAULT_POSTGRES_PORT))
    user = os.environ.get("GSF_POSTGRES_USER", DEFAULT_POSTGRES_USER)
    password = os.environ.get("GSF_POSTGRES_PASSWORD", DEFAULT_POSTGRES_PASSWORD)
    return f"postgresql://{user}:{password}@{host}:{port}/{db}"


def ingest(databases: Iterable[tuple[str, str]] = SEEDED_DATABASES) -> None:
    """Ingest each ``(physical_db, display_name)`` pair into Neo4j."""
    load_server_env()

    for physical_db, display_name in databases:
        logger.info("Ingesting %s (as %r)", physical_db, display_name)
        connector = PostgresDatabase(
            _conn_string(physical_db),
            database_name=display_name,
        )
        try:
            data = data_for_populate_tabular(connector)
            populate_tabular_data(data, num_workers=4, dialect="postgres")
        finally:
            connector.close()

    logger.info("Ingestion complete.")


if __name__ == "__main__":
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s %(levelname)s %(name)s: %(message)s",
    )
    ingest()
