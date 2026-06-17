# SPDX-FileCopyrightText: Copyright (c) 2024-2026, NVIDIA CORPORATION & AFFILIATES.
# All rights reserved.
# SPDX-License-Identifier: Apache-2.0

"""VDB initialization — data layer and semantic layer collections."""

from gsf.vdb.config import get_postgres_connection_string
from gsf.vdb.postgres import PostgresVDB

DATA_VDB_COLLECTION = "nv_ingest_tabular"
SEMANTIC_VDB_COLLECTION = "semantic_layer"

VDB_COLLECTION = DATA_VDB_COLLECTION


def get_data_vdb(*, database_name: str | None = None, reset: bool = False) -> PostgresVDB:
    """Build a PostgresVDB for the tabular data layer."""
    kwargs: dict = {
        "connection_string": get_postgres_connection_string(),
        "collection_name": DATA_VDB_COLLECTION,
    }
    if database_name:
        kwargs["database_name"] = database_name
    if reset:
        kwargs["reset"] = True
    return PostgresVDB(**kwargs)


def get_semantic_vdb(*, database_name: str | None = None, reset: bool = False) -> PostgresVDB:
    """Build a PostgresVDB for the semantic layer."""
    kwargs: dict = {
        "connection_string": get_postgres_connection_string(),
        "collection_name": SEMANTIC_VDB_COLLECTION,
    }
    if database_name:
        kwargs["database_name"] = database_name
    if reset:
        kwargs["reset"] = True
    return PostgresVDB(**kwargs)


def get_vdb(*, collection_name: str | None = None, database_name: str | None = None) -> PostgresVDB:
    """Alias for backward compatibility."""
    kwargs: dict = {
        "connection_string": get_postgres_connection_string(),
        "collection_name": collection_name or VDB_COLLECTION,
    }
    if database_name:
        kwargs["database_name"] = database_name
    return PostgresVDB(**kwargs)
