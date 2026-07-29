# SPDX-FileCopyrightText: Copyright (c) 2024-2026, NVIDIA CORPORATION & AFFILIATES.
# All rights reserved.
# SPDX-License-Identifier: Apache-2.0

"""Orchestration for GSF model YAML export/import."""

from __future__ import annotations

import logging
from typing import Any

import yaml

from gsf.connectors import get_connectors
from gsf.dal import model_interchange as dal
from gsf.dal.connections import list_connections
from gsf.server.model_interchange.embed import (
    ImportEmbedBuffer,
    flush_import_embeddings,
)
from gsf.server.model_interchange.schemas import ExportRequest, GsfModelDocument

logger = logging.getLogger(__name__)


def _dialect_by_database_name() -> dict[str, str]:
    """Resolve SQL dialect strings keyed by catalog database name."""
    dialects: dict[str, str] = {}
    for connector in get_connectors():
        database_name = getattr(connector, "database_name", None)
        dialect = getattr(connector, "dialect", None)
        if database_name and dialect:
            dialects[str(database_name)] = str(dialect)

    for connection in list_connections():
        database_name = str(
            connection.get("database")
            or connection.get("database_name")
            or connection.get("name")
            or "",
        )
        if not database_name or database_name in dialects:
            continue
        dialect = connection.get("dialect") or connection.get("type") or ""
        dialects[database_name] = str(dialect)
    return dialects


def export_model(request: ExportRequest) -> str:
    """Export the scoped GSF model document as a YAML string."""
    database_ids = request.databases
    dal.validate_database_ids(database_ids)
    rows = dal.fetch_export_rows(database_ids)
    document = dal.assemble_export_document(
        rows,
        dialect_by_db_name=_dialect_by_database_name(),
        sql_column_resolver=dal.resolve_sql_column_ids,
    )
    return yaml.safe_dump(
        document.model_dump(mode="python"),
        sort_keys=False,
        default_flow_style=False,
    )


def import_model(
    yaml_text: str,
    *,
    replace: bool = True,
    embed: bool = True,
) -> dict[str, Any]:
    """Validate and apply a YAML GSF model document."""
    payload = yaml.safe_load(yaml_text)
    if not isinstance(payload, dict):
        raise ValueError("YAML document must deserialize to a mapping")
    document = GsfModelDocument.model_validate(payload)
    embed_buffer = ImportEmbedBuffer() if embed else None
    summary = dal.apply_import_model(
        document,
        replace=replace,
        embed_buffer=embed_buffer,
    )
    if embed and embed_buffer is not None:
        summary["embeddings"] = flush_import_embeddings(embed_buffer)
    return summary
