"""Targeted LanceDB upsert for a known set of catalog node ids.

The PATCH handler already knows exactly which ``Table``/``Column`` ids were
affected by an edit (the node itself plus, for column-description changes,
its parent table). This module takes that list straight to the embedding +
upsert pipeline:

    Neo4j (by id)  →  TabularFetchEmbeddingsOp(node_ids=...)
                   →  _BatchEmbedActor
                   →  UpsertVdbOperator (merge_insert on ``id``)

No ``needs_embed`` flag, no full-database dirty scan — every row the
pipeline touches is one the caller explicitly listed.
"""

from __future__ import annotations

import logging
from typing import Any, Iterable

from nemo_retriever.graph import Graph
from nemo_retriever.text_embed.operators import _BatchEmbedActor

from server.graph.tabular_fetch_embeddings_operator import TabularFetchEmbeddingsOp
from server.ingestion.params import get_embed_params, get_vdb_params
from vdb.operators import UpsertVdbOperator

logger = logging.getLogger(__name__)


def sync_node_vectors(
    database_name: str,
    node_ids: Iterable[str],
) -> dict[str, Any]:
    """Re-embed and upsert the LanceDB rows for the listed ``node_ids``.

    Returns a small status dict for the HTTP response::

        {
            "database_name": "<db>",
            "node_ids": ["<uuid>", ...],
            "status": "upserted" | "skipped_no_ids" | "skipped_no_rows"
                     | "error",
            "upserted": <int>,
            "error": "<message>"  # only when status == "error"
        }

    ``upserted`` is the success path; this also covers the case where
    the target LanceDB table did not exist yet and was auto-created from
    the rows being written (see :meth:`vdb.lancedb.LanceDB.upsert`).
    ``skipped_no_ids`` means the caller passed an empty list (no work to
    do). ``skipped_no_rows`` means the ids exist but did not produce any
    embeddable rows (e.g. they refer to non-tabular nodes). ``error`` is
    reserved for hard failures from the embedding endpoint or LanceDB;
    the row is left untouched so the caller can retry.
    """
    ids = [str(n) for n in node_ids if n is not None and str(n)]
    if not ids:
        return {
            "database_name": database_name,
            "node_ids": [],
            "status": "skipped_no_ids",
            "upserted": 0,
        }

    try:
        embed_params = get_embed_params()
        vdb_params = get_vdb_params()
    except EnvironmentError as exc:
        logger.exception("sync_node_vectors: embed/vdb params unavailable: %s", exc)
        return {
            "database_name": database_name,
            "node_ids": ids,
            "status": "error",
            "upserted": 0,
            "error": str(exc),
        }

    try:
        embed_graph = (
            Graph()
            >> TabularFetchEmbeddingsOp(database_name=database_name, node_ids=ids)
            >> _BatchEmbedActor(params=embed_params)
        )
        results = embed_graph.execute(None)
    except Exception as exc:  # noqa: BLE001 — surface any embed failure to the API caller
        logger.exception(
            "sync_node_vectors: embed step failed for db=%s ids=%s", database_name, ids
        )
        return {
            "database_name": database_name,
            "node_ids": ids,
            "status": "error",
            "upserted": 0,
            "error": f"embed failed: {exc}",
        }

    result_df = results[0] if results else None
    if result_df is None or result_df.empty:
        logger.info(
            "sync_node_vectors: no embeddable rows for %s ids=%s; nothing to upsert.",
            database_name,
            ids,
        )
        return {
            "database_name": database_name,
            "node_ids": ids,
            "status": "skipped_no_rows",
            "upserted": 0,
        }

    # Upsert must not overwrite the whole table; strip the ingest-time flag.
    upsert_kwargs = {k: v for k, v in vdb_params.vdb_kwargs.items() if k != "overwrite"}
    try:
        upsert_op = UpsertVdbOperator(
            vdb_op=vdb_params.vdb_op,
            vdb_kwargs=upsert_kwargs,
            key="id",
            table_name=vdb_params.vdb_kwargs.get("table_name"),
        )
        upsert_op(result_df.to_dict(orient="records"))
    except Exception as exc:  # noqa: BLE001 — same rationale as embed step
        logger.exception(
            "sync_node_vectors: LanceDB upsert failed for db=%s ids=%s",
            database_name,
            ids,
        )
        return {
            "database_name": database_name,
            "node_ids": ids,
            "status": "error",
            "upserted": 0,
            "error": f"upsert failed: {exc}",
        }

    upserted_count = len(result_df)
    logger.info(
        "sync_node_vectors: upserted %d row(s) into LanceDB (db=%s, ids=%s)",
        upserted_count,
        database_name,
        ids,
    )
    return {
        "database_name": database_name,
        "node_ids": ids,
        "status": "upserted",
        "upserted": upserted_count,
    }
