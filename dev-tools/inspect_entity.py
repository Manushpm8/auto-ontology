"""Inspect a single Table / Column entity across Neo4j and LanceDB.

Used to verify the targeted ``--mode update-meta`` flow: run it before
and after editing ``<database_name>.json`` (or hitting
``PATCH /api/nodes/{node_id}``) to confirm that the LanceDB vector for a
given table or column was actually re-embedded.

Examples (from repo root)::

    # Inspect a table
    PYTHONPATH=gsf uv run --no-sync python dev-tools/inspect_entity.py --table users

    # Inspect a column
    PYTHONPATH=gsf uv run --no-sync python dev-tools/inspect_entity.py \\
        --table users --column email

    # Or look up directly by Neo4j UUID
    PYTHONPATH=gsf uv run --no-sync python dev-tools/inspect_entity.py \\
        --id 7b3a...uuid...

The script prints a hash of the vector so it's easy to eyeball whether the
embedding actually changed between runs (full 2048-dim vectors are too
noisy to read).
"""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import sys


def _short(text: str, limit: int = 120) -> str:
    if text is None:
        return "<None>"
    text = str(text).replace("\n", " ").strip()
    return text if len(text) <= limit else text[: limit - 1] + "…"


def _vector_fingerprint(vector) -> str:
    """Return a short, stable signature for a 2048-dim float vector.

    The full vector is too noisy to read by eye. A short hash + the first
    few values is enough to spot whether the row was actually re-embedded.
    """
    if vector is None:
        return "<None>"
    try:
        as_list = list(vector)
    except TypeError:
        return f"<unreadable: {type(vector).__name__}>"
    head = ", ".join(f"{v:+.4f}" for v in as_list[:4])
    digest = hashlib.sha1(str(as_list).encode()).hexdigest()[:10]
    return f"[len={len(as_list)} sha1={digest} head=({head}, …)]"


def inspect_neo4j(
    database_name: str, *, table: str | None, column: str | None, node_id: str | None
) -> list[dict]:
    """Return matching Table/Column nodes' relevant properties from Neo4j."""
    from nemo_retriever.tabular_data.neo4j import get_neo4j_conn

    conn = get_neo4j_conn()
    if node_id:
        query = (
            "MATCH (n) WHERE n.id = $node_id "
            "RETURN labels(n)[0] AS label, n.id AS id, n.name AS name, "
            "       n.description AS description, n.sample_values AS sample_values"
        )
        params = {"node_id": node_id}
    elif column:
        query = (
            "MATCH (d:Database {name: $db_name})-[:CONTAINS]->"
            "(:Schema)-[:CONTAINS]->(t:Table {name: $table_name})"
            "-[:CONTAINS]->(c:Column {name: $column_name}) "
            "RETURN labels(c)[0] AS label, c.id AS id, c.name AS name, "
            "       c.description AS description, c.sample_values AS sample_values"
        )
        params = {"db_name": database_name, "table_name": table, "column_name": column}
    elif table:
        query = (
            "MATCH (d:Database {name: $db_name})-[:CONTAINS]->"
            "(:Schema)-[:CONTAINS]->(t:Table {name: $table_name}) "
            "RETURN labels(t)[0] AS label, t.id AS id, t.name AS name, "
            "       t.description AS description, null AS sample_values"
        )
        params = {"db_name": database_name, "table_name": table}
    else:
        raise ValueError("Pass one of --table, --column (with --table) or --id.")
    return conn.query_read(query, parameters=params) or []


def inspect_lancedb(uri: str, table_name: str, ids: list[str]) -> list[dict]:
    """Return the LanceDB rows whose top-level ``id`` column is in ``ids``."""
    import lancedb

    if not ids:
        return []

    db = lancedb.connect(uri=uri)
    try:
        table = db.open_table(table_name)
    except FileNotFoundError:
        return []

    # Build a quoted IN-list compatible with DataFusion. Single quotes inside
    # ids are escaped just in case the upstream UUIDs ever change format.
    quoted = ", ".join("'" + str(i).replace("'", "''") + "'" for i in ids)
    where = f"id IN ({quoted})"
    return table.search().where(where).limit(len(ids) * 2).to_list()


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--database",
        default=os.environ.get("POSTGRES_DATABASE")
        or os.environ.get("POSTGRES_DB", "gsf"),
    )
    parser.add_argument("--table", help="Table name (e.g. users)")
    parser.add_argument("--column", help="Column name (requires --table)")
    parser.add_argument(
        "--id", dest="node_id", help="Neo4j UUID (alternative to --table/--column)"
    )
    parser.add_argument("--lancedb-uri", default="lancedb")
    parser.add_argument("--lancedb-table", default="nv-ingest-tabular")
    args = parser.parse_args()

    if args.column and not args.table:
        parser.error("--column requires --table.")
    if not (args.table or args.column or args.node_id):
        parser.error("Pass one of --table, --column (with --table), or --id.")

    print("\n=== Neo4j ===")
    neo4j_rows = inspect_neo4j(
        database_name=args.database,
        table=args.table,
        column=args.column,
        node_id=args.node_id,
    )
    if not neo4j_rows:
        print(" (no matching node found)")
    for row in neo4j_rows:
        print(
            f" label={row['label']:<7} id={row['id']}  name={row['name']}\n"
            f"   description    = {_short(row.get('description'))}\n"
            f"   sample_values  = {_short(row.get('sample_values'))}"
        )

    print("\n=== LanceDB ===")
    ids = [row["id"] for row in neo4j_rows if row.get("id")]
    ldb_rows = inspect_lancedb(
        uri=args.lancedb_uri, table_name=args.lancedb_table, ids=ids
    )
    if not ldb_rows:
        print(f" (no rows in LanceDB table {args.lancedb_table!r} matching ids={ids})")
        return 0 if neo4j_rows else 1
    for row in ldb_rows:
        meta_raw = row.get("metadata")
        try:
            meta = (
                json.loads(meta_raw) if isinstance(meta_raw, str) and meta_raw else {}
            )
        except json.JSONDecodeError:
            meta = {"_raw": meta_raw}
        print(
            f" id={row.get('id')}  name={meta.get('name')}\n"
            f"   text     = {_short(row.get('text'))}\n"
            f"   vector   = {_vector_fingerprint(row.get('vector'))}\n"
            f"   metadata = {_short(meta_raw)}"
        )
    return 0


if __name__ == "__main__":
    sys.exit(main())
