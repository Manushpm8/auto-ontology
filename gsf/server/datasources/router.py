"""API route handlers for datasources and connectors."""

from __future__ import annotations

from fastapi import APIRouter
from pydantic import BaseModel

from server.datasources import dal
from server.datasources.vector_sync import sync_node_vectors


class NodeUpdate(BaseModel):
    description: str | None = None
    sample_values: list[str] | None = None


router = APIRouter()


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _count_payload(data: object) -> dict:
    if isinstance(data, list):
        return {"data": data, "count": len(data)}
    return {"data": data, "count": 1}


# ---------------------------------------------------------------------------
# Catalog lazy tree (/api/schemas, /api/tables, /api/columns)
# ---------------------------------------------------------------------------


@router.get("/schemas/{db_id}")
def list_schemas_by_database(db_id: str) -> dict:
    """Schemas for a database."""
    result = dal.list_schemas_for_database(db_id)
    return result


@router.get("/tables/{schema_id}")
def list_tables_by_schema(
    schema_id: str,
    database_name: str | None = None,
) -> dict:
    """Tables under a schema (lazy tree)."""
    rows = dal.list_tables_for_schema(schema_id, database_name=database_name)
    return _count_payload(rows)


@router.get("/columns/{table_id}")
def list_columns_by_table(table_id: str) -> dict:
    """Columns for a table."""
    result = dal.list_columns_for_table(table_id)
    return _count_payload(result)


# ---------------------------------------------------------------------------
# Datasource routes (/api/datasources)
# ---------------------------------------------------------------------------


@router.get("/datasources/dbs")
def list_databases() -> dict:
    rows = dal.list_databases()
    return _count_payload(rows)


# ---------------------------------------------------------------------------
# Node property update (/api/nodes/{node_id})
# ---------------------------------------------------------------------------


@router.patch("/nodes/{node_id}")
def update_node(node_id: str, body: NodeUpdate) -> dict:
    """Update mutable properties of any catalog node and resync its vector.

    The frontend is expected to diff edits against the originally loaded
    values and only send fields that actually changed (an empty body is
    a no-op). The DAL therefore treats every PATCH as a real change and
    reports the ids that need re-embedding: the node itself, plus its
    parent ``Table`` when the node is a ``Column`` (the table embedding
    text concatenates child column descriptions, so it must follow). Those
    ids are handed straight to :func:`sync_node_vectors`, which re-embeds
    and merge-inserts only those rows into LanceDB — no full reindex, no
    dirty-flag scan. The response includes a ``vector_sync`` field so the
    frontend can tell whether retrieval will now reflect the edit.
    """
    props = body.model_dump(exclude_none=True)
    result = dal.update_node_properties(node_id, props)
    if result is None:
        return {"id": node_id, "vector_sync": {"status": "skipped_not_found"}}

    affected_ids = result.get("affected_ids") or []
    database_name = result.get("database_name")
    if affected_ids and database_name:
        result["vector_sync"] = sync_node_vectors(database_name, affected_ids)
    else:
        result["vector_sync"] = {"status": "skipped_no_change"}
    return result
