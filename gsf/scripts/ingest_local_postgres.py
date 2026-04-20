"""Ingest the local docker-compose Postgres into Neo4j via NeMo Retriever.

Run after ``docker compose up -d`` and ``scripts.seed_local_postgres``.

Usage::

    PYTHONPATH=gsf uv run --no-sync python -m scripts.ingest_local_postgres
"""

from __future__ import annotations

import logging
import os
from typing import Iterable

from nemo_retriever.tabular_data.ingestion.extract_data import data_for_populate_tabular
from nemo_retriever.tabular_data.ingestion.write_to_graph import populate_tabular_data

from server.connectors.postgres import PostgresDatabase
from server.env import load_server_env

logger = logging.getLogger("scripts.ingest_local_postgres")

DEFAULT_POSTGRES_HOST = "localhost"
DEFAULT_POSTGRES_PORT = 5432
DEFAULT_POSTGRES_USER = "gsf"
DEFAULT_POSTGRES_PASSWORD = "gsf"

DATABASES: tuple[str, ...] = ("testdb",)


def _conn_string(db: str) -> str:
    host = os.environ.get("GSF_POSTGRES_HOST", DEFAULT_POSTGRES_HOST)
    port = os.environ.get("GSF_POSTGRES_PORT", str(DEFAULT_POSTGRES_PORT))
    user = os.environ.get("GSF_POSTGRES_USER", DEFAULT_POSTGRES_USER)
    password = os.environ.get("GSF_POSTGRES_PASSWORD", DEFAULT_POSTGRES_PASSWORD)
    return f"postgresql://{user}:{password}@{host}:{port}/{db}"


def ingest(databases: Iterable[str] = DATABASES) -> None:
    """Ingest each Postgres database in ``databases`` into Neo4j."""
    load_server_env()

    for db in databases:
        logger.info("Ingesting %s", db)
        connector = PostgresDatabase(_conn_string(db))
        try:
            data = data_for_populate_tabular(connector)
            populate_tabular_data(data, num_workers=4, dialect=connector.dialect)
        finally:
            connector.close()

    logger.info("Ingestion complete.")


if __name__ == "__main__":
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s %(levelname)s %(name)s: %(message)s",
    )
    ingest()
