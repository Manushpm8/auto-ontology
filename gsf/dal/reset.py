# SPDX-FileCopyrightText: Copyright (c) 2026, NVIDIA CORPORATION & AFFILIATES.
# All rights reserved.
# SPDX-License-Identifier: Apache-2.0

"""Per-database reset: drop a database's Neo4j subgraph and pgvector embeddings.

This is the single source of truth for wiping one database's ingested data. It
removes the Neo4j subgraph reachable from the ``Database`` node (catalog and
semantic nodes alike) and the corresponding rows in both pgvector collections.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass

from gsf.dal.connections import delete_database_subgraph, delete_semantic_subgraph
from gsf.vdb import get_data_vdb, get_semantic_vdb

logger = logging.getLogger(__name__)


@dataclass
class ResetResult:
    """Summary of what a :func:`delete_database` call removed."""

    database_name: str
    data_rows: int
    semantic_rows: int


def delete_semantic(database_name: str | None = None) -> int:
    """Delete semantic Neo4j nodes and pgvector embeddings.

    When ``database_name`` is given, removes only that database's semantic nodes
    (``Term``/``ColumnAttribute``/``SqlAttribute``) reachable from the
    ``Database`` node and its ``semantic_layer`` rows. When ``None``, removes
    semantic nodes and embeddings across every database. Data nodes are left
    intact. Returns the number of pgvector rows deleted.
    """
    delete_semantic_subgraph(database_name)

    vdb = get_semantic_vdb()
    if database_name is None:
        semantic_deleted = vdb.delete_all()
    else:
        semantic_deleted = len(vdb.delete_by_database(database_name))

    logger.info(
        "delete_semantic: removed %d semantic pgvector rows for database %s",
        semantic_deleted,
        database_name or "<all>",
    )
    return semantic_deleted


def delete_data(database_name: str | None = None) -> int:
    """Delete data Neo4j subgraph and pgvector embeddings.

    When ``database_name`` is given, removes that ``Database`` node and its
    connected subgraph in Neo4j plus its ``data_objects_layer`` rows. When
    ``None``, removes every ``Database`` subgraph and all data embeddings.
    Returns the number of pgvector rows deleted.
    """
    delete_database_subgraph(database_name)

    vdb = get_data_vdb()
    if database_name is None:
        data_deleted = vdb.delete_all()
    else:
        data_deleted = len(vdb.delete_by_database(database_name))

    logger.info(
        "delete_data: removed %d data pgvector rows for database %s",
        data_deleted,
        database_name or "<all>",
    )
    return data_deleted


def delete_database(database_name: str) -> ResetResult:
    """Delete a database's Neo4j subgraph and all its pgvector embeddings.

    Removes the ``Database`` node and its connected subgraph in Neo4j, then
    deletes every ``data_objects_layer`` and ``semantic_layer`` row tagged with
    ``database_name``. Other databases are untouched.

    Semantic nodes are removed first (while still reachable from the
    ``Database`` node) before the data subgraph deletion removes the node.
    """
    semantic_rows = delete_semantic(database_name)
    data_rows = delete_data(database_name)

    result = ResetResult(
        database_name=database_name,
        data_rows=data_rows,
        semantic_rows=semantic_rows,
    )
    logger.info(
        "delete_database: removed %d pgvector rows for database %s "
        "(%d data, %d semantic)",
        result.data_rows + result.semantic_rows,
        database_name,
        result.data_rows,
        result.semantic_rows,
    )
    return result
