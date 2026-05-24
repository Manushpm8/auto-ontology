# SPDX-FileCopyrightText: Copyright (c) 2024-2026, NVIDIA CORPORATION & AFFILIATES.
# All rights reserved.
# SPDX-License-Identifier: Apache-2.0

"""API route handlers for datasources and connectors."""

from __future__ import annotations

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel, Field

from gsf.server.custom_analyses import dal as custom_analyses_dal
from gsf.server.datasources import dal


class NodeUpdate(BaseModel):
    description: str | None = None
    sample_values: str | None = None


class CustomAnalysisCreate(BaseModel):
    name: str = Field(..., min_length=1)
    description: str = Field(..., min_length=1)
    sql: str = Field(..., min_length=1)


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
# Custom analyses (/api/custom-analyses)
# ---------------------------------------------------------------------------


@router.get("/custom-analyses")
def list_custom_analyses() -> dict:
    """All CustomAnalysis nodes joined with their HAS_SQL neighbour."""
    rows = custom_analyses_dal.list_custom_analyses()
    return _count_payload(rows)


@router.post("/custom-analyses", status_code=201)
def create_custom_analysis(body: CustomAnalysisCreate) -> dict:
    """Create (or upsert by ``name``) a CustomAnalysis with its Sql node.

    ``name``, ``description`` and ``sql`` are all required and must be
    non-empty after trimming — a blank SQL would leave the analysis
    orphaned, and a blank name/description would produce an unidentifiable
    catalog entry.
    """
    name = body.name.strip()
    description = body.description.strip()
    sql = body.sql.strip()
    if not name:
        raise HTTPException(status_code=422, detail="name must not be blank")
    if not description:
        raise HTTPException(status_code=422, detail="description must not be blank")
    if not sql:
        raise HTTPException(status_code=422, detail="sql must not be blank")

    row = custom_analyses_dal.create_custom_analysis(
        name=name,
        description=description,
        sql=sql,
    )
    return {"data": row}


# ---------------------------------------------------------------------------
# Node property update (/api/nodes/{node_id})
# ---------------------------------------------------------------------------


@router.patch("/nodes/{node_id}")
def update_node(node_id: str, body: NodeUpdate) -> dict:
    """Update mutable properties of any catalog node."""
    props = body.model_dump(exclude_none=True)
    result = dal.update_node_properties(node_id, props)
    return result
