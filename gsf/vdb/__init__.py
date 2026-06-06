# SPDX-FileCopyrightText: Copyright (c) 2024-2026, NVIDIA CORPORATION & AFFILIATES.
# All rights reserved.
# SPDX-License-Identifier: Apache-2.0

"""
VDB initialization and configuration.
"""

from gsf.vdb.config import get_postgres_connection_string
from gsf.vdb.postgres import PostgresVDB

VDB_COLLECTION: str = "nv_ingest_tabular"
SEMANTIC_VDB_COLLECTION: str = "semantic_layer"
# Legacy collection name kept for backward-compatible reads during migration.
LEGACY_SEMANTIC_VDB_COLLECTION: str = "rigor_ontology"


def get_vdb(*, database_name: str = None) -> PostgresVDB:
    """Build a PostgresVDB for the data layer (tables, columns, analyses).

    When database_name is provided, the VDB will use it to reset old embeddings
    for the given database.
    """
    return get_data_vdb(database_name=database_name)


def get_data_vdb(*, database_name: str = None) -> PostgresVDB:
    """Vector store for physical schema metadata (Table, Column, Analysis)."""
    kwargs: dict = {
        "connection_string": get_postgres_connection_string(),
        "collection_name": VDB_COLLECTION,
    }
    if database_name:
        kwargs["database_name"] = database_name
    return PostgresVDB(**kwargs)


def get_semantic_vdb(*, database_name: str = None) -> PostgresVDB:
    """Vector store for semantic entities (Term, Attribute, Metric)."""
    kwargs: dict = {
        "connection_string": get_postgres_connection_string(),
        "collection_name": SEMANTIC_VDB_COLLECTION,
    }
    if database_name:
        kwargs["database_name"] = database_name
    return PostgresVDB(**kwargs)
