# SPDX-FileCopyrightText: Copyright (c) 2024-2026, NVIDIA CORPORATION & AFFILIATES.
# All rights reserved.
# SPDX-License-Identifier: Apache-2.0

"""Tiny in-memory mock ingest for fast semantic-layer iteration.

Builds a 4-table shop schema (customer / order / orderline / products) entirely
in-memory and pushes it through the same tabular ingest pipeline that
``dev_tools.ingest_local_postgres`` uses. No remote DB, no docker dependency,
no metadata JSON files. Embeddings still go through the real NVIDIA endpoint
because the semantic-compile path needs the data-layer VDB populated.

Usage::

    PYTHONPATH=. uv run python -m dev_tools.mock_ingest

Or via the "Debug Mock Ingest" launch config in .vscode/launch.json.
"""

from __future__ import annotations

import logging
import os
from typing import Optional

import pandas as pd
from nemo_retriever.graph import Graph
from nemo_retriever.graph.tabular_fetch_embeddings_operator import (
    TabularFetchEmbeddingsOp,
)
from nemo_retriever.graph.tabular_schema_extract_operator import TabularSchemaExtractOp
from nemo_retriever.params import EmbedParams, TabularExtractParams
from nemo_retriever.tabular_data.sql_database import SQLDatabase
from nemo_retriever.text_embed.operators import _BatchEmbedActor
from nemo_retriever.vdb import IngestVdbOperator

from gsf.vdb import get_data_vdb

logger = logging.getLogger("dev_tools.mock_ingest")

MOCK_DATABASE_NAME = "mock_shop"
MOCK_SCHEMA = "public"

_NVIDIA_API_KEY = os.environ.get("NVIDIA_API_KEY", "")
_EMBED_ENDPOINT = os.environ.get(
    "EMBED_ENDPOINT", "https://integrate.api.nvidia.com/v1"
)
_EMBED_MODEL = os.environ.get("EMBED_MODEL", "nvidia/llama-nemotron-embed-1b-v2")

if not _NVIDIA_API_KEY:
    raise EnvironmentError(
        "NVIDIA_API_KEY is not set. Export it before running, e.g.:\n\n"
        "    export NVIDIA_API_KEY='nvapi-...'\n"
    )


EMBED_PARAMS = EmbedParams(
    embed_invoke_url=_EMBED_ENDPOINT,
    model_name=_EMBED_MODEL,
    api_key=_NVIDIA_API_KEY,
    embed_modality="text",
)


# (table_name, [(column_name, data_type), ...])
_TABLES: list[tuple[str, list[tuple[str, str]]]] = [
    (
        "customer",
        [
            ("id", "integer"),
            ("name", "text"),
            ("phone", "text"),
            ("email", "text"),
        ],
    ),
    (
        "order",
        [
            ("id", "integer"),
            ("customer_id", "integer"),
            ("date", "date"),
        ],
    ),
    (
        "orderline",
        [
            ("id", "integer"),
            ("order_id", "integer"),
            ("price_paid", "numeric"),
            ("product_id", "integer"),
        ],
    ),
    (
        "products",
        [
            ("id", "integer"),
            ("name", "text"),
            ("price", "numeric"),
            ("sinceDate", "date"),
        ],
    ),
]

# (source_table, source_column, target_table, target_column)
_FKS: list[tuple[str, str, str, str]] = [
    ("order", "customer_id", "customer", "id"),
    ("orderline", "order_id", "order", "id"),
    ("orderline", "product_id", "products", "id"),
]


class MockDatabase(SQLDatabase):
    """In-memory schema source: 4 hand-built tables, no real backend."""

    def __init__(self, connection_string: str = "") -> None:
        self._database_name = MOCK_DATABASE_NAME

    @property
    def dialect(self) -> str:
        return "postgres"

    @property
    def database_name(self) -> str:
        return self._database_name

    def execute(self, sql: str, parameters: Optional[list] = None) -> pd.DataFrame:
        return pd.DataFrame()

    def get_tables(self) -> pd.DataFrame:
        rows = [
            {
                "table_schema": MOCK_SCHEMA,
                "table_name": name,
                "table_type": "base table",
            }
            for name, _ in _TABLES
        ]
        return pd.DataFrame(rows)

    def get_columns(self) -> pd.DataFrame:
        rows: list[dict[str, object]] = []
        for table_name, columns in _TABLES:
            for ordinal, (col_name, data_type) in enumerate(columns, start=1):
                rows.append(
                    {
                        "table_schema": MOCK_SCHEMA,
                        "table_name": table_name,
                        "column_name": col_name,
                        "data_type": data_type,
                        "is_nullable": "YES" if col_name != "id" else "NO",
                        "ordinal_position": ordinal,
                    }
                )
        return pd.DataFrame(rows)

    def get_pks(self) -> pd.DataFrame:
        rows = [
            {
                "table_schema": MOCK_SCHEMA,
                "table_name": table_name,
                "column_name": "id",
                "ordinal_position": 1,
            }
            for table_name, _ in _TABLES
        ]
        return pd.DataFrame(rows)

    def get_fks(self) -> pd.DataFrame:
        rows = [
            {
                "table_schema": MOCK_SCHEMA,
                "table_name": src_table,
                "column_name": src_col,
                "referenced_schema": MOCK_SCHEMA,
                "referenced_table": tgt_table,
                "referenced_column": tgt_col,
            }
            for src_table, src_col, tgt_table, tgt_col in _FKS
        ]
        return pd.DataFrame(rows)

    def get_views(self) -> pd.DataFrame:
        return pd.DataFrame(columns=["table_schema", "table_name", "view_definition"])

    def get_queries(self, hours: int = 24) -> pd.DataFrame:
        return pd.DataFrame(columns=["end_time", "query_text"])

    def close(self) -> None:
        pass


def run_mock_ingest() -> None:
    """Push the mock 4-table schema through the real ingest + embed pipeline."""
    connector = MockDatabase()
    tabular_params = TabularExtractParams(connector=connector)
    database_name = connector.database_name

    logger.info("Extracting schema for %r (mock)", database_name)
    extract_graph = Graph() >> TabularSchemaExtractOp(tabular_params=tabular_params)
    extract_results = extract_graph.execute(None)
    schema_data = extract_results[0] if extract_results else None
    if not (isinstance(schema_data, tuple) and len(schema_data) == 2):
        raise RuntimeError(
            "TabularSchemaExtractOp did not return (tables_df, columns_df); "
            f"got {type(schema_data).__name__}."
        )

    logger.info("Embedding %r schema rows", database_name)
    embed_graph = (
        Graph()
        >> TabularFetchEmbeddingsOp(database_name=database_name)
        >> _BatchEmbedActor(params=EMBED_PARAMS)
    )
    results = embed_graph.execute(schema_data)
    result_df = results[0] if results else None

    vdb = get_data_vdb(database_name=database_name, reset=True)

    if result_df is not None and not result_df.empty:
        ingest_op = IngestVdbOperator(vdb=vdb)
        ingest_op(result_df.to_dict(orient="records"))
        logger.info("Mock ingest wrote %d rows to pgvector", len(result_df))
    else:
        logger.warning("Mock ingest produced 0 rows")


if __name__ == "__main__":
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s %(levelname)s %(name)s: %(message)s",
    )
    run_mock_ingest()
