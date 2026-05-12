"""Ingest the local docker-compose Postgres into Neo4j via NeMo Retriever.

Run after ``docker compose up -d`` and ``python dev-tools/seed_local_postgres.py``.

Usage (from repo root)::

    PYTHONPATH=gsf uv run --no-sync python dev-tools/ingest_postgres.py
    PYTHONPATH=gsf uv run --no-sync python dev-tools/ingest_postgres.py --mode update-meta

``PYTHONPATH=gsf`` exposes the ``server.*`` packages (params, graph
operators, datasources) — the same way the FastAPI server resolves them.
"""

from __future__ import annotations

import argparse
import logging
import os
import sys
from pathlib import Path

from nemo_retriever.graph import Graph
from nemo_retriever.graph.tabular_schema_extract_operator import TabularSchemaExtractOp
from nemo_retriever.params import TabularExtractParams
from nemo_retriever.retriever import Retriever
from nemo_retriever.tabular_data.dev_tools.postgres_connector import PostgresDatabase
from nemo_retriever.tabular_data.retrieval.deep_agent.main import (
    get_agent_response as get_deep_agent_response,
)
from nemo_retriever.tabular_data.retrieval.deep_agent.state import (
    AgentPayload as DeepAgentPayload,
)
from nemo_retriever.tabular_data.retrieval.text_to_sql.main import get_agent_response
from nemo_retriever.tabular_data.retrieval.text_to_sql.state import AgentPayload
from nemo_retriever.text_embed.operators import _BatchEmbedActor
from nemo_retriever.vdb import IngestVdbOperator

from server.graph.tabular_fetch_embeddings_operator import TabularFetchEmbeddingsOp
from server.ingestion.params import get_embed_params, get_vdb_params

# Make sibling dev-tools modules importable when invoking this file directly
# (``python dev-tools/ingest_postgres.py``). The folder name contains a hyphen
# so it cannot be a Python package; this keeps the import surface flat.
sys.path.insert(0, str(Path(__file__).resolve().parent))

from apply_metadata import apply_metadata  # noqa: E402

logger = logging.getLogger("ingest_postgres")


DATABASE: str = os.environ.get("POSTGRES_DATABASE") or os.environ.get(
    "POSTGRES_DB", "gsf"
)


def _conn_string(db: str) -> str:
    host = os.environ.get("POSTGRES_HOST", "localhost")
    port = os.environ.get("POSTGRES_PORT", "5432")
    user = os.environ["POSTGRES_USER"]
    password = os.environ["POSTGRES_PASSWORD"]
    # ``gssencmode=disable`` keeps libpq from probing Kerberos on macOS where a
    # stale ``KERBEROS.MICROSOFTONLINE.COM`` ticket in the local cache causes
    # intermittent ``Cannot find KDC for realm`` failures before the regular
    # password/SSL handshake even starts. Override via ``POSTGRES_GSSENCMODE``
    # (e.g. ``prefer`` or ``require``) if you actually need GSS-encrypted auth.
    gssencmode = os.environ.get("POSTGRES_GSSENCMODE", "disable")
    sslmode = os.environ.get("POSTGRES_SSLMODE", "require")
    return (
        f"postgresql://{user}:{password}@{host}:{port}/{db}"
        f"?gssencmode={gssencmode}&sslmode={sslmode}"
    )


_CONNECTOR: PostgresDatabase | None = None


def _get_connector() -> PostgresDatabase:
    """Open the Postgres connection lazily and reuse across phases."""
    global _CONNECTOR
    if _CONNECTOR is None:
        _CONNECTOR = PostgresDatabase(_conn_string(DATABASE))
    return _CONNECTOR


def run_ingest() -> None:
    """Ingest the Postgres schema into Neo4j and write embeddings to LanceDB.

    A full ingest covers every table and column and overwrites the LanceDB
    table, so the database is fully in sync with Neo4j at the end of the
    run. ``apply_metadata`` is called as part of this flow but its
    ``affected_ids`` return value is intentionally discarded — we already
    re-embed everything below.
    """
    connector = _get_connector()
    embed_params = get_embed_params()
    vdb_params = get_vdb_params()

    tabular_params = TabularExtractParams(connector=connector)

    extract_graph = Graph() >> TabularSchemaExtractOp(tabular_params=tabular_params)
    extract_graph.execute(None)

    apply_metadata(connector.database_name)

    embed_graph = (
        Graph()
        >> TabularFetchEmbeddingsOp(database_name=connector.database_name)
        >> _BatchEmbedActor(params=embed_params)
    )
    results = embed_graph.execute(None)
    result_df = results[0] if results else None

    if result_df is not None and not result_df.empty:
        ingest_op = IngestVdbOperator(
            vdb_op=vdb_params.vdb_op,
            vdb_kwargs=vdb_params.vdb_kwargs,
        )
        ingest_op(result_df.to_dict(orient="records"))
        logger.info("Tabular ingest result: %d rows written to LanceDB", len(result_df))
    else:
        logger.info("Tabular ingest result: no rows produced")


def _build_retriever() -> Retriever:
    embed_params = get_embed_params()
    vdb_params = get_vdb_params()
    lancedb_kwargs = vdb_params.vdb_kwargs
    return Retriever(
        vdb="lancedb",
        vdb_kwargs={
            "uri": lancedb_kwargs["uri"],
            "table_name": lancedb_kwargs["table_name"],
        },
        top_k=15,
        embedding_api_key=embed_params.api_key,
        embedding_http_endpoint=embed_params.embed_invoke_url,
    )


def run_retrieve() -> None:
    """Run the text-to-SQL agent against the previously ingested LanceDB."""
    connector = _get_connector()
    retriever = _build_retriever()

    payload: AgentPayload = {
        "question": "What are three most frequently occurring processes across all requests?",
        "retriever": retriever,
        "connector": connector,
        "path_state": {},
        "custom_prompts": "",
        "acronyms": "",
    }

    agent_result = get_agent_response(payload)
    logger.info("get_agent_response result: %s", agent_result)


def run_update_metadata() -> None:
    """Apply ``<db>.json`` edits and incrementally upsert only changed rows.

    Flow:
        1. ``apply_metadata`` writes table/column description and
           ``sample_values`` into Neo4j and returns the list of node ids
           whose embedding text actually changed (including parent tables
           hit by the column-description cascade).
        2. :func:`server.datasources.vector_sync.sync_node_vectors`
           re-embeds exactly those ids and merge-inserts them into
           LanceDB by ``id`` (no overwrite, no full reindex).

    If ``apply_metadata`` reports zero affected ids, the function exits
    early without touching the embedding endpoint or LanceDB.
    """
    from server.datasources.vector_sync import sync_node_vectors

    connector = _get_connector()

    affected_ids = apply_metadata(connector.database_name)
    if not affected_ids:
        logger.info(
            "Metadata update: no affected ids detected; skipping embed + upsert."
        )
        return

    logger.info(
        "Metadata update: %d affected node(s); re-embedding via sync_node_vectors.",
        len(affected_ids),
    )

    result = sync_node_vectors(connector.database_name, affected_ids)
    logger.info("Metadata update: sync_node_vectors result: %s", result)


def run_retrieve_deep() -> None:
    """Run the deep-agent text-to-SQL pipeline against the previously ingested LanceDB."""
    connector = _get_connector()
    retriever = _build_retriever()

    payload: DeepAgentPayload = {
        "question": "What are three most frequently occurring processes across all requests?",
        "retriever": retriever,
        "connector": connector,
        "path_state": {},
        "custom_prompts": "",
        "acronyms": "",
    }

    agent_result = get_deep_agent_response(payload)
    logger.info("get_deep_agent_response result: %s", agent_result)


_ALL_MODES = ("ingest", "update-meta", "retrieve", "retrieve-deep")
_DEFAULT_MODES = ("ingest", "retrieve", "retrieve-deep")


def _parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--mode",
        choices=_ALL_MODES,
        nargs="*",
        default=None,
        help=(
            "Phases to run. Pass one or more (e.g. --mode ingest retrieve, "
            "or --mode update-meta to incrementally re-embed only changed "
            "tables/columns). Default: ingest + retrieve + retrieve-deep."
        ),
    )
    return parser.parse_args()


if __name__ == "__main__":
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s %(levelname)s %(name)s: %(message)s",
    )
    args = _parse_args()
    modes = args.mode if args.mode else _DEFAULT_MODES
    if "ingest" in modes:
        run_ingest()
    if "update-meta" in modes:
        run_update_metadata()
    if "retrieve" in modes:
        run_retrieve()
    if "retrieve-deep" in modes:
        run_retrieve_deep()
