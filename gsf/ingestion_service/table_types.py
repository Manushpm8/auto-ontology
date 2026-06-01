# SPDX-FileCopyrightText: Copyright (c) 2024-2026, NVIDIA CORPORATION & AFFILIATES.
# All rights reserved.
# SPDX-License-Identifier: Apache-2.0

"""Stamp table ``type`` onto Neo4j ``Table`` nodes after schema extraction."""

from __future__ import annotations

import logging

from nemo_retriever.tabular_data.ingestion.model.reserved_words import Edges, Labels
from nemo_retriever.tabular_data.neo4j import get_neo4j_conn
from nemo_retriever.tabular_data.sql_database import SQLDatabase

logger = logging.getLogger(__name__)


def apply_table_types(connector: SQLDatabase) -> None:
    """Set ``type`` on ``Table`` nodes from connector introspection.

    nemo_retriever's schema ingest writes ``name``, ``description``, etc. but
    does not persist ``table_type`` from :meth:`SQLDatabase.get_tables`. The
    retrieval layer reads ``parent.type`` (mapped to ``table_type`` in
    candidates), so we stamp it here after :class:`TabularSchemaExtractOp`.
    """
    tables_df = connector.get_tables()
    if tables_df.empty or "table_type" not in tables_df.columns:
        logger.info("No table types to apply for database %r", connector.database_name)
        return

    rows = [
        {
            "schema_name": row["table_schema"],
            "table_name": row["table_name"],
            "table_type": row["table_type"],
        }
        for row in tables_df.to_dict(orient="records")
        if row.get("table_type")
    ]
    if not rows:
        return

    updated = get_neo4j_conn().query_write(
        f"""
        UNWIND $rows AS row
        MATCH (db:{Labels.DB} {{name: $db_name}})-[:{Edges.CONTAINS}]->
              (s:{Labels.SCHEMA} {{name: row.schema_name}})-[:{Edges.CONTAINS}]->
              (t:{Labels.TABLE} {{name: row.table_name}})
        SET t.type = row.table_type
        RETURN count(t) AS updated
        """,
        {"rows": rows, "db_name": connector.database_name},
    )
    count = updated[0]["updated"] if updated else 0
    logger.info(
        "Applied table types: %d Table node(s) updated for database %r",
        count,
        connector.database_name,
    )
