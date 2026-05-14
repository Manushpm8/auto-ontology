"""Data Access Layer — Neo4j driver, catalog graph, and datasource queries."""

from __future__ import annotations

from typing import Any

from nemo_retriever.tabular_data.ingestion.model.reserved_words import Edges, Labels
from nemo_retriever.tabular_data.neo4j import get_neo4j_conn

# Column properties that appear inside the parent Table's embedding text
# (see ``_create_table_text`` in
# ``nemo_retriever.graph.tabular_fetch_embeddings_operator``). Editing any of
# these on a Column invalidates the Table vector and triggers a cascade
# re-embed. ``sample_values`` is deliberately excluded — it only appears in
# the Column-level embedding text, not the Table-level one.
_COLUMN_FIELDS_AFFECTING_TABLE_TEXT: frozenset[str] = frozenset(
    {"name", "description", "data_type"}
)


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

    The caller (the frontend save button) is responsible for diffing the
    user's edits against the originally-loaded values and only sending
    fields that actually changed. The DAL therefore trusts the payload
    and does no old-vs-new comparison of its own — every PATCH that
    reaches this function is assumed to be a real change.

    ``affected_ids`` is the list of node ids whose embedding text needs
    to be re-computed. The rules are derived from what each level's
    embedding text actually contains (see ``_create_table_text`` /
    ``_create_column_text`` in
    ``nemo_retriever.graph.tabular_fetch_embeddings_operator``):

    * For a ``Table`` edit — ``[table.id]``. The table text uses
      ``table_name``, ``table_schema``, ``description`` plus a
      compact rendering of its columns; no column row needs to be
      touched when the table's own properties change.
    * For a ``Column`` edit — always ``[column.id]``, plus
      ``[parent_table.id]`` *only* when the edited property feeds into
      the parent table's text. That set is
      :data:`_COLUMN_FIELDS_AFFECTING_TABLE_TEXT` (``name``,
      ``description``, ``data_type``). ``sample_values`` is **not** in
      that set — it appears only inside the Column-level text, so
      editing it leaves the Table vector untouched.
    * For ``Database`` / ``Schema`` edits — empty: those labels are
      not indexed in LanceDB, so the caller skips the no-op re-embed.

    ``database_name`` is resolved by walking up the catalog graph so the
    caller can hand both pieces directly to
    :func:`server.datasources.vector_sync.sync_node_vectors` without a
    second round trip.

    Returns ``None`` if no node with that id exists or if ``properties``
    is empty.
    """
    if not properties:
        return None

    neo4j_conn = get_neo4j_conn()

    rows = neo4j_conn.query_write(
        f"""
        MATCH (d:{Labels.DB})-[:{Edges.CONTAINS}*0..3]->(n {{id: $node_id}})
        WHERE n:{Labels.DB} OR n:{Labels.SCHEMA} OR n:{Labels.TABLE} OR n:{Labels.COLUMN}
        SET n += $props
        WITH d, n, labels(n)[0] AS label
        OPTIONAL MATCH (parent:{Labels.TABLE})-[:{Edges.CONTAINS}]->(n)
        WHERE label = '{Labels.COLUMN}'
        RETURN n.id AS id,
               label AS label,
               d.name AS database_name,
               properties(n) AS props,
               parent.id AS parent_id
        """,
        {"node_id": node_id, "props": properties},
    )

    if not rows:
        return None

    record = rows[0]
    label = record["label"]
    node_props = dict(record["props"])
    parent_id = record["parent_id"]

    affected_ids: list[str] = []
    if label in (Labels.TABLE, Labels.COLUMN):
        affected_ids.append(record["id"])
    if (
        label == Labels.COLUMN
        and parent_id is not None
        and _COLUMN_FIELDS_AFFECTING_TABLE_TEXT.intersection(properties)
    ):
        affected_ids.append(parent_id)

    return {
        "id": record["id"],
        "label": label,
        "database_name": record["database_name"],
        "affected_ids": affected_ids,
        **{k: node_props.get(k) for k in properties},
    }
