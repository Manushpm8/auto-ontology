"""Stamp table/column metadata onto the Neo4j graph.

This module reads ``<repo_root>/data/<database_name>.json`` (e.g.
``data/dor_prod.json`` for the ``dor_prod`` database) and writes descriptions
and sample values onto the ``Table`` and ``Column`` nodes that the tabular
ingest pipeline created in Neo4j. It is intentionally a small, dev-tools-only
helper and is meant to be invoked at the end of an ingest run.

JSON shape (per table)::

    {
        "<table_name>": {
            "description": "...",
            "columns": [
                {
                    "name": "...",
                    "description": "...",
                    "value_examples": ["...", ...] | "single value" | null,
                    ...
                },
                ...
            ]
        },
        ...
    }

The JSON ``value_examples`` field maps to the Neo4j ``Column.sample_values``
property. It accepts a list of values, a single scalar (string/int/float/bool —
wrapped into a one-element list), or ``null`` / missing (left untouched).
"""

from __future__ import annotations

import json
import logging
from pathlib import Path

logger = logging.getLogger(__name__)

METADATA_DIR = Path(__file__).resolve().parent.parent / "data"


def _default_metadata_path(database_name: str) -> Path:
    """Return the conventional ``<repo_root>/data/<database_name>.json`` path."""
    return METADATA_DIR / f"{database_name}.json"


def _coerce_value_examples(
    raw_value: object,
    *,
    table_name: str,
    column_name: str,
) -> list[str] | None:
    """Map a JSON ``value_examples`` entry to a Neo4j ``sample_values`` list.

    JSON shapes accepted (per column):

    * ``null`` / missing / empty string  → ``None`` (no change to Neo4j)
    * ``[...]``                          → list of stringified items
    * scalar (str / int / float / bool)  → wrapped as ``[str(scalar)]``
    * anything else (dict, etc.)         → ``None`` + warning

    The scalar branch mirrors how ``description`` accepts a single value, so a
    plain string like ``"value_examples": "ABC-123"`` is treated as one sample
    rather than silently dropped.
    """
    if raw_value is None:
        return None
    if isinstance(raw_value, list):
        items = [str(v) for v in raw_value if v is not None and str(v) != ""]
        return items or None
    if isinstance(raw_value, str):
        stripped = raw_value.strip()
        return [stripped] if stripped else None
    if isinstance(raw_value, (int, float, bool)):
        return [str(raw_value)]
    logger.warning(
        "value_examples for %s.%s has unsupported type %s; ignoring",
        table_name,
        column_name,
        type(raw_value).__name__,
    )
    return None


def apply_metadata(database_name: str) -> list[str]:
    """Stamp table/column metadata onto the Neo4j graph.

    Reads ``<repo_root>/data/<database_name>.json`` (keyed by table name) and
    updates the following properties for every table/column belonging to
    *database_name*:

    * ``Table.description``
    * ``Column.description``
    * ``Column.sample_values`` (from the JSON's ``value_examples`` field, when
      present and non-empty)

    Tables/columns that aren't present in the graph are silently skipped
    (the MATCH simply finds nothing). Properties for which the JSON has no
    value are left untouched (``coalesce`` preserves the existing value).

    Returns the list of node ids whose embedding text actually changed —
    i.e. tables/columns where ``description`` or ``sample_values`` flipped.
    A change to a column description also includes the parent table's id
    in the result, because the table-level embedding text in
    :func:`server.ingestion.embeddings.query_neo4j_tables_for_embedding`
    interpolates every child column's description. The caller can pass
    this list straight to
    :func:`server.datasources.vector_sync.sync_node_vectors` for a
    targeted LanceDB upsert.
    """
    from nemo_retriever.tabular_data.neo4j import get_neo4j_conn

    metadata_path = _default_metadata_path(database_name)

    if not metadata_path.exists():
        logger.warning("metadata file not found at %s; skipping", metadata_path)
        return []

    with metadata_path.open() as f:
        raw = json.load(f)

    table_rows: list[dict[str, str]] = []
    column_rows: list[dict[str, str | list[str] | None]] = []
    samples_count = 0
    for table_name, table_meta in raw.items():
        table_desc = table_meta.get("description")
        if table_desc:
            table_rows.append({"table_name": table_name, "description": table_desc})

        for col in table_meta.get("columns", []) or []:
            col_desc = col.get("description")
            sample_values = _coerce_value_examples(
                col.get("value_examples"),
                table_name=table_name,
                column_name=col["name"],
            )
            if not col_desc and sample_values is None:
                continue
            if sample_values is not None:
                samples_count += 1
            column_rows.append(
                {
                    "table_name": table_name,
                    "column_name": col["name"],
                    "description": col_desc or None,
                    "sample_values": sample_values,
                }
            )

    conn = get_neo4j_conn()

    affected_table_ids: list[str] = []
    affected_column_ids: list[str] = []
    parent_table_ids_from_columns: list[str] = []

    if table_rows:
        # Only flag the table as affected when the description value actually
        # changes — re-running with the same JSON should not trigger a
        # pointless re-embed. The SET still uses coalesce so missing values
        # in JSON leave the existing Neo4j property untouched.
        result = conn.query_write(
            query=(
                "UNWIND $rows AS row "
                "MATCH (d:Database {name: $db_name})-[:CONTAINS]->"
                "(:Schema)-[:CONTAINS]->(t:Table {name: row.table_name}) "
                "WITH t, row, coalesce(t.description, '') AS old_desc, "
                "     coalesce(row.description, '') AS new_desc "
                "SET t.description = coalesce(row.description, t.description) "
                "WITH t, old_desc, new_desc "
                "WHERE new_desc <> '' AND new_desc <> old_desc "
                "RETURN collect(t.id) AS ids"
            ),
            parameters={"rows": table_rows, "db_name": database_name},
        )
        if result:
            affected_table_ids = list(result[0].get("ids") or [])

    if column_rows:
        # Track description and sample_values independently: the column's own
        # vector is invalidated when either changes, but the parent table's
        # vector is only invalidated by a column-description change (the
        # table-level embedding text does not include sample_values).
        result = conn.query_write(
            query=(
                "UNWIND $rows AS row "
                "MATCH (d:Database {name: $db_name})-[:CONTAINS]->"
                "(:Schema)-[:CONTAINS]->(t:Table {name: row.table_name})"
                "-[:CONTAINS]->(c:Column {name: row.column_name}) "
                "WITH t, c, row, "
                "     coalesce(c.description, '') AS old_desc, "
                "     coalesce(row.description, '') AS new_desc, "
                "     coalesce(c.sample_values, []) AS old_samples, "
                "     coalesce(row.sample_values, c.sample_values, []) AS new_samples "
                "SET c.description = coalesce(row.description, c.description), "
                "    c.sample_values = coalesce(row.sample_values, c.sample_values) "
                "WITH t, c, row, old_desc, new_desc, old_samples, new_samples, "
                "     CASE WHEN new_desc <> '' AND new_desc <> old_desc THEN true ELSE false END AS desc_changed, "
                "     CASE WHEN row.sample_values IS NOT NULL AND new_samples <> old_samples "
                "          THEN true ELSE false END AS samples_changed "
                "WITH t, c, desc_changed, samples_changed, "
                "     (desc_changed OR samples_changed) AS column_changed "
                "WHERE column_changed "
                "RETURN collect(DISTINCT c.id) AS column_ids, "
                "       collect(DISTINCT CASE WHEN desc_changed THEN t.id END) AS parent_ids"
            ),
            parameters={"rows": column_rows, "db_name": database_name},
        )
        if result:
            affected_column_ids = list(result[0].get("column_ids") or [])
            # ``collect(DISTINCT CASE WHEN ... THEN t.id END)`` includes NULL
            # entries for the False branches; strip them.
            parent_table_ids_from_columns = [
                pid for pid in (result[0].get("parent_ids") or []) if pid is not None
            ]

    # Merge table ids from both paths into a single deduplicated list so
    # ``sync_node_vectors`` receives every id exactly once.
    affected_ids: list[str] = list(
        dict.fromkeys(
            [*affected_table_ids, *parent_table_ids_from_columns, *affected_column_ids]
        )
    )

    logger.info(
        "Applied metadata: %d table description(s), %d column description(s), "
        "%d column sample_values from %s — affected ids: %d total "
        "(tables_direct=%d, tables_via_column_desc=%d, columns=%d)",
        len(table_rows),
        sum(1 for r in column_rows if r.get("description")),
        samples_count,
        metadata_path,
        len(affected_ids),
        len(affected_table_ids),
        len(parent_table_ids_from_columns),
        len(affected_column_ids),
    )
    return affected_ids
