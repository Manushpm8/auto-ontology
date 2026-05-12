"""Data Access Layer — Neo4j driver, catalog graph, and datasource queries."""

from __future__ import annotations

from typing import Any

from nemo_retriever.tabular_data.ingestion.model.reserved_words import Edges, Labels
from nemo_retriever.tabular_data.neo4j import get_neo4j_conn


def _coerce_sample_values(raw: Any) -> list[str] | None:
    """Normalize ``Column.sample_values`` to ``list[str] | None`` on read.

    Legacy rows stored ``sample_values`` as a plain string (the PATCH
    endpoint previously accepted ``str``). The frontend now expects an
    array, so wrap stragglers into a one-element list and stringify any
    non-string list entries.
    """
    if raw is None:
        return None
    if isinstance(raw, list):
        return [str(v) for v in raw if v is not None]
    return [str(raw)]


# ---------------------------------------------------------------------------
# Graph queries (public API for routers / services)
# ---------------------------------------------------------------------------


def list_databases() -> list[dict[str, Any]]:
    """Return Database rows with schema counts only; ``schemas`` is empty for lazy trees."""
    neo4j_conn = get_neo4j_conn()

    rows = neo4j_conn.query_read(
        f"""
        MATCH (db:{Labels.DB})-[:{Edges.CONTAINS}]->(s:{Labels.SCHEMA})
        RETURN db.id AS id, db.name AS name, db.description AS description,
               count(s) AS schema_count
        ORDER BY name
        """,
    )

    return [
        {
            "id": r["id"],
            "name": r["name"],
            "description": r["description"],
            "num_of_schemas": int(r["schema_count"]),
            "schemas": [],
        }
        for r in rows
    ]


def list_schemas_for_database(db_id: str) -> dict[str, Any] | None:
    """Return schemas_count and a list of schema summaries for a database.

    Returns a dict with ``schemas_count`` and ``schemas`` — a list of
    ``{id, schema_name, tables_count}`` dicts.

    Returns ``None`` if no ``Database`` matches ``db_id``.
    """
    neo4j_conn = get_neo4j_conn()
    rows = neo4j_conn.query_read(
        f"""
        MATCH (db:{Labels.DB} {{id: $db_id}})-[:{Edges.CONTAINS}]->
              (s:{Labels.SCHEMA})-[:{Edges.CONTAINS}]->(t:{Labels.TABLE})
        WITH s.id AS id, s.name AS schema_name, s.description AS description,
             count(t) AS tables_count
        ORDER BY schema_name
        WITH collect({{id: id, schema_name: schema_name,
                      description: description,
                      tables_count: tables_count}}) AS schemas
        RETURN size(schemas) AS schemas_count, schemas
        """,
        {"db_id": db_id},
    )

    record = rows[0]
    return {
        "schemas_count": record["schemas_count"],
        "schemas": [dict(s) for s in record["schemas"]],
    }


def list_tables_for_schema(
    schema_id: str,
    *,
    database_name: str | None = None,
) -> list[dict[str, Any]]:
    """Return Table payloads with column counts for a given schema.

    Each table dict contains ``database_name``, ``schema_name``, ``name``,
    and ``columns_count``.
    """
    neo4j_conn = get_neo4j_conn()
    rows = neo4j_conn.query_read(
        f"""
        MATCH (s:{Labels.SCHEMA} {{id: $schema_id}})-[:{Edges.CONTAINS}]->
              (t:{Labels.TABLE})-[:{Edges.CONTAINS}]->(c:{Labels.COLUMN})
        RETURN t.id AS id,
               t.name AS name,
               t.db_name AS db_name,
               t.schema_name AS schema_name, t.description AS description,
               count(c) AS columns_count
        ORDER BY name
        """,
        {
            "schema_id": schema_id,
            "database_name": database_name,
        },
    )

    return rows


def list_columns_for_table(table_id: str) -> dict[str, Any] | None:
    """Return a table dict with nested columns, or None if the table is missing.

    Returns ``table_name``, ``schema_name``, ``db_name`` (all from the Table
    node), ``columns_count``, and ``columns`` — a list of
    ``{ordinal_position, column_name, data_type}`` dicts.

    ``sample_values`` is normalized to ``list[str] | None`` via
    :func:`_coerce_sample_values` so the frontend always receives an array
    (legacy rows that hold a scalar string are wrapped here).
    """
    neo4j_conn = get_neo4j_conn()
    rows = neo4j_conn.query_read(
        f"""
        MATCH (t:{Labels.TABLE} {{id: $table_id}})-[:{Edges.CONTAINS}]->(c:{Labels.COLUMN})
        WITH t, c ORDER BY c.ordinal_position
        WITH t, collect({{
                 id: c.id,
                 ordinal_position: c.ordinal_position,
                 column_name: c.name,
                 data_type: c.data_type,
                 description: c.description,
                 sample_values: c.sample_values
             }}) AS columns
        RETURN t.name AS table_name,
               t.schema_name AS schema_name,
               t.db_name AS db_name,
               size(columns) AS columns_count,
               columns
        """,
        {"table_id": table_id},
    )

    if not rows:
        return None

    record = dict(rows[0])
    record["columns"] = [
        {**dict(col), "sample_values": _coerce_sample_values(col.get("sample_values"))}
        for col in record["columns"]
    ]
    return record


def update_node_properties(
    node_id: str,
    properties: dict[str, Any],
) -> dict[str, Any] | None:
    """Update properties on any catalog node matched by ``id``.

    Returns ``{id, label, database_name, affected_ids, ...updated_fields}``.

    ``affected_ids`` is the list of node ids whose embedding text changed
    as a result of this PATCH — at most this node, plus its parent
    ``Table`` when a ``Column.description`` changed. Cascade rule: the
    table-level embedding text in
    :func:`server.ingestion.embeddings.query_neo4j_tables_for_embedding`
    interpolates every child column's description, so editing a column
    description also invalidates the parent table's vector. An empty list
    means no re-embed is required.

    ``database_name`` is resolved by walking up the catalog graph so the
    caller can hand both pieces directly to
    :func:`server.datasources.vector_sync.sync_node_vectors` without a
    second round trip.

    Returns ``None`` if no node with that id exists.
    """
    if not properties:
        return None

    neo4j_conn = get_neo4j_conn()

    rows = neo4j_conn.query_write(
        f"""
        MATCH (d:{Labels.DB})-[:{Edges.CONTAINS}*0..3]->(n {{id: $node_id}})
        WHERE n:{Labels.DB} OR n:{Labels.SCHEMA} OR n:{Labels.TABLE} OR n:{Labels.COLUMN}
        WITH d, n,
             labels(n)[0] AS label,
             coalesce(n.description, '') AS old_desc,
             coalesce(n.sample_values, []) AS old_samples
        SET n += $props
        WITH d, n, label, old_desc, old_samples,
             coalesce(n.description, '') AS new_desc,
             coalesce(n.sample_values, []) AS new_samples
        WITH d, n, label,
             CASE
                 WHEN (label = '{Labels.TABLE}' OR label = '{Labels.COLUMN}')
                      AND $props.description IS NOT NULL
                      AND new_desc <> old_desc
                 THEN true ELSE false
             END AS desc_changed,
             CASE
                 WHEN label = '{Labels.COLUMN}'
                      AND $props.sample_values IS NOT NULL
                      AND new_samples <> old_samples
                 THEN true ELSE false
             END AS samples_changed
        WITH d, n, label, desc_changed, samples_changed,
             (desc_changed OR samples_changed) AS self_dirty
        OPTIONAL MATCH (parent:{Labels.TABLE})-[:{Edges.CONTAINS}]->(n)
        WHERE label = '{Labels.COLUMN}' AND desc_changed
        WITH d, n, label,
             CASE WHEN self_dirty THEN [n.id] ELSE [] END +
             CASE WHEN parent IS NOT NULL THEN [parent.id] ELSE [] END AS affected_ids
        RETURN n.id AS id,
               label AS label,
               d.name AS database_name,
               properties(n) AS props,
               affected_ids AS affected_ids
        """,
        {"node_id": node_id, "props": properties},
    )

    if not rows:
        return None

    record = rows[0]
    node_props = dict(record["props"])
    return {
        "id": record["id"],
        "label": record["label"],
        "database_name": record["database_name"],
        "affected_ids": list(record["affected_ids"] or []),
        **{k: node_props.get(k) for k in properties},
    }
