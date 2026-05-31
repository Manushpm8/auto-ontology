"""Targeted LanceDB upsert for a known set of catalog node ids.

The PATCH handler already knows exactly which ``Table``/``Column`` ids were
affected by an edit (the node itself plus, for column-description changes,
its parent table). This module takes that list, pulls the matching rows out
of Neo4j, hands the resulting ``(tables_df, columns_df)`` pair to the
embedding pipeline, and merge-inserts the result into LanceDB:

    Neo4j (by id)  →  (tables_df, columns_df)
                   →  TabularFetchEmbeddingsOp  (build embed-ready rows)
                   →  filter rows by affected_ids
                   →  _BatchEmbedActor          (embed only those rows)
                   →  UpsertVdbOperator         (merge_insert on ``id``)

The table-level embedding text concatenates every child column, so a
column edit must pull *all* sibling columns of the parent table to
rebuild that text correctly. The post-fetch ``id`` filter is what keeps
the actual embedding + upsert work limited to the rows the caller
listed; siblings only participate in text assembly and are dropped
before the embedding endpoint is called.
"""

from __future__ import annotations

import logging
from typing import Any, Iterable

import pandas as pd
from nemo_retriever.graph.tabular_fetch_embeddings_operator import (
    TabularFetchEmbeddingsOp,
)
from nemo_retriever.tabular_data.ingestion.model.reserved_words import Edges, Labels
from nemo_retriever.tabular_data.neo4j import get_neo4j_conn
from nemo_retriever.text_embed.operators import _BatchEmbedActor
from nemo_retriever.vdb.operators import UpsertVdbOperator

from gsf.server.ingestion.params import get_embed_params, get_vdb_params

logger = logging.getLogger(__name__)


_TABLES_COLUMNS = ["id", "table_name", "table_schema", "description"]
_COLUMNS_COLUMNS = [
    "id",
    "table_name",
    "table_schema",
    "column_name",
    "data_type",
    "description",
    "sample_values",
]


def _load_tables_and_columns(ids: list[str]) -> tuple[pd.DataFrame, pd.DataFrame]:
    """Fetch ``(tables_df, columns_df)`` for every Table that ``ids`` touches.

    A Table is "touched" when either its own id is in ``ids`` or one of
    its child Columns is. For each such Table we return its row plus
    *every* child column — even unaffected ones — because the table
    embedding text in :class:`TabularFetchEmbeddingsOp` concatenates the
    full column list. Downstream :func:`sync_node_vectors` filters the
    embed-ready DataFrame by ``affected_ids`` so the sibling columns
    only participate in text assembly.
    """
    neo4j_conn = get_neo4j_conn()

    rows = neo4j_conn.query_read(
        f"""
        MATCH (t:{Labels.TABLE})
        WHERE t.id IN $ids
           OR EXISTS {{ (t)-[:{Edges.CONTAINS}]->(c:{Labels.COLUMN})
                       WHERE c.id IN $ids }}
        OPTIONAL MATCH (t)-[:{Edges.CONTAINS}]->(c:{Labels.COLUMN})
        RETURN t.id AS table_id,
               t.name AS table_name,
               t.schema_name AS table_schema,
               t.description AS table_description,
               c.id AS column_id,
               c.name AS column_name,
               c.data_type AS column_data_type,
               c.description AS column_description,
               c.sample_values AS column_sample_values
        """,
        {"ids": ids},
    )

    table_records: dict[str, dict[str, Any]] = {}
    column_records: list[dict[str, Any]] = []
    for r in rows:
        table_id = r["table_id"]
        table_name = r["table_name"]
        # Coerce a missing schema to "" so the (schema, table) key the embed
        # operator builds matches on both the tables_df and columns_df sides
        # (str(None).lower() == "none" would silently break the join).
        table_schema = r["table_schema"] or ""
        if table_id not in table_records:
            table_records[table_id] = {
                "id": table_id,
                "table_name": table_name,
                "table_schema": table_schema,
                "description": r["table_description"],
            }
        if r["column_id"] is not None:
            column_records.append(
                {
                    "id": r["column_id"],
                    "table_name": table_name,
                    "table_schema": table_schema,
                    "column_name": r["column_name"],
                    "data_type": r["column_data_type"],
                    "description": r["column_description"],
                    "sample_values": r["column_sample_values"],
                }
            )

    tables_df = pd.DataFrame(list(table_records.values()), columns=_TABLES_COLUMNS)
    columns_df = pd.DataFrame(column_records, columns=_COLUMNS_COLUMNS)
    return tables_df, columns_df


def _filter_embed_rows(embed_df: pd.DataFrame, keep_ids: set[str]) -> pd.DataFrame:
    """Drop embed-ready rows whose ``metadata.id`` is not in ``keep_ids``."""
    if embed_df.empty:
        return embed_df

    def _row_id(meta: Any) -> str | None:
        if isinstance(meta, dict):
            value = meta.get("id")
            if value is not None:
                return str(value)
        return None

    mask = embed_df["metadata"].apply(lambda m: _row_id(m) in keep_ids)
    return embed_df.loc[mask].reset_index(drop=True)


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
        tables_df, columns_df = _load_tables_and_columns(ids)
    except Exception as exc:  # noqa: BLE001 — surface Neo4j fetch failures
        logger.exception(
            "sync_node_vectors: Neo4j fetch failed for db=%s ids=%s",
            database_name,
            ids,
        )
        return {
            "database_name": database_name,
            "node_ids": ids,
            "status": "error",
            "upserted": 0,
            "error": f"neo4j fetch failed: {exc}",
        }

    if tables_df.empty:
        logger.info(
            "sync_node_vectors: no tabular rows for %s ids=%s; nothing to upsert.",
            database_name,
            ids,
        )
        return {
            "database_name": database_name,
            "node_ids": ids,
            "status": "skipped_no_rows",
            "upserted": 0,
        }

    try:
        fetch_op = TabularFetchEmbeddingsOp(database_name=database_name)
        embed_rows_df = fetch_op.run((tables_df, columns_df))
        embed_rows_df = _filter_embed_rows(embed_rows_df, set(ids))
        if embed_rows_df.empty:
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

        embed_actor = _BatchEmbedActor(params=embed_params)
        result_df = embed_actor.run(embed_rows_df)
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

    if result_df is None or result_df.empty:
        logger.info(
            "sync_node_vectors: embed step produced no rows for %s ids=%s.",
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
