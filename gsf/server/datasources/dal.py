"""Data Access Layer — Neo4j driver, catalog graph, and datasource queries."""

from __future__ import annotations

from typing import Any

from infra.Neo4jConnection import get_driver
# ---------------------------------------------------------------------------
# Graph queries (public API for routers / services)
# ---------------------------------------------------------------------------


def list_databases() -> list[dict[str, Any]]:
    """Return Database rows with schema counts only; ``schemas`` is empty for lazy trees."""
    driver = get_driver()

    # Default routing is WRITE — same practical behavior as Session.run() on
    # bolt://; READ routing can fail on standalone instances.
    rows, _, _ = driver.execute_query(
        """
        MATCH (db:Database)-[:CONTAINS]->(s:Schema)
        RETURN db.id as id, db.name as name, db.description as description,
               count(s) as schema_count
        ORDER BY name
        """,
        database_="neo4j",
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
    driver = get_driver()

    rows, _, _ = driver.execute_query(
        """
        MATCH (db:Database {id: $db_id})-[:CONTAINS]->(s:Schema)-[:CONTAINS]->(t:Table)
        WITH s.id AS id, s.name AS schema_name, s.description AS description,
             count(t) AS tables_count
        ORDER BY schema_name
        WITH collect({id: id, schema_name: schema_name,
                      description: description,
                      tables_count: tables_count}) AS schemas
        RETURN size(schemas) AS schemas_count, schemas
        """,
        db_id=db_id,
        database_="neo4j",
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
    driver = get_driver()

    rows, _, _ = driver.execute_query(
        """
        MATCH (s:Schema {id: $schema_id})-[:CONTAINS]->(t:Table)-[:CONTAINS]->(c:Column)
        RETURN t.id AS id,
               t.name AS name,
               t.db_name AS db_name,
               t.schema_name AS schema_name, t.description AS description,
               count(c) AS columns_count
        ORDER BY name
        """,
        schema_id=schema_id,
        database_name=database_name,
        database_="neo4j",
    )

    return [dict(r) for r in rows]


def list_columns_for_table(table_id: str) -> dict[str, Any] | None:
    """Return a table dict with nested columns, or None if the table is missing.

    Returns ``table_name``, ``schema_name``, ``db_name`` (all from the Table
    node), ``columns_count``, and ``columns`` — a list of
    ``{ordinal_position, column_name, data_type}`` dicts.
    """
    driver = get_driver()

    rows, _, _ = driver.execute_query(
        """
        MATCH (t:Table {id: $table_id})-[:CONTAINS]->(c:Column)
        WITH t, c ORDER BY c.ordinal_position
        WITH t, collect({
                 id: c.id,
                 ordinal_position: c.ordinal_position,
                 column_name: c.name,
                 data_type: c.data_type,
                 description: c.description
             }) AS columns
        RETURN t.name AS table_name,
               t.schema_name AS schema_name,
               t.db_name AS db_name,
               size(columns) AS columns_count,
               columns
        """,
        table_id=table_id,
        database_="neo4j",
    )

    return dict(rows[0])


def update_node_properties(
    node_id: str,
    properties: dict[str, Any],
) -> dict[str, Any] | None:
    """Update properties on any catalog node matched by ``id``.

    Returns ``{id, ...updated_fields}``
    """
    if not properties:
        return None

    driver = get_driver()

    rows, _, _ = driver.execute_query(
        """
        MATCH (n {id: $node_id})
        WHERE n:Database OR n:Schema OR n:Table OR n:Column
        SET n += $props
        RETURN n.id AS id, properties(n) AS props
        """,
        node_id=node_id,
        props=properties,
        database_="neo4j",
    )

    node_props = dict(rows[0]["props"])
    return {"id": rows[0]["id"], **{k: node_props.get(k) for k in properties}}
