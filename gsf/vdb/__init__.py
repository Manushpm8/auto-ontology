# SPDX-FileCopyrightText: Copyright (c) 2024-2026, NVIDIA CORPORATION & AFFILIATES.
# All rights reserved.
# SPDX-License-Identifier: Apache-2.0

"""VDB initialization — data layer and semantic layer collections."""

from gsf.vdb.config import get_postgres_connection_string
from gsf.vdb.postgres import PostgresVDB

VDB_COLLECTION: str = "nv_ingest_tabular"
SEMANTIC_VDB_COLLECTION: str = "semantic_layer"
VDB_SCHEMA: str = "vdb"

# Backward-compatible alias
DATA_VDB_COLLECTION = VDB_COLLECTION


def get_vdb(*, database_name: str | None = None) -> PostgresVDB:
    """Build a PostgresVDB pointed at the local pgvector-enabled Postgres.

    When database_name is provided, the VDB will use it to reset old embeddings for the given database.
    """
    kwargs: dict = {
        "connection_string": get_postgres_connection_string(),
        "collection_name": VDB_COLLECTION,
        "schema_name": VDB_SCHEMA,
    }
    if database_name:
        kwargs["database_name"] = database_name
    return PostgresVDB(**kwargs)


def get_data_vdb(
    *, database_name: str | None = None, reset: bool = False
) -> PostgresVDB:
    """Backward-compatible alias for :func:`get_vdb` (tabular / data layer)."""
    return get_vdb(database_name=database_name, reset=reset)


def get_semantic_vdb(
    *, database_name: str | None = None, reset: bool = False
) -> PostgresVDB:
    """Build a PostgresVDB for the semantic layer."""
    kwargs: dict = {
        "connection_string": get_postgres_connection_string(),
        "collection_name": SEMANTIC_VDB_COLLECTION,
        "schema_name": VDB_SCHEMA,
    }
    if database_name:
        kwargs["database_name"] = database_name
    return PostgresVDB(reset=reset, **kwargs)
