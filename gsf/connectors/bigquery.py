# SPDX-FileCopyrightText: Copyright (c) 2024-2026, NVIDIA CORPORATION & AFFILIATES.
# All rights reserved.
# SPDX-License-Identifier: Apache-2.0

"""BigQuery connector implementing the NeMo-Retriever SQLDatabase ABC."""

from __future__ import annotations

import logging
import os
from pathlib import Path
from typing import Any, Optional
from urllib.parse import unquote, urlparse

import pandas as pd
from nemo_retriever.tabular_data.ingestion.model.reserved_words import TableTypes
from nemo_retriever.tabular_data.sql_database import SQLDatabase

from gsf.connectors.url_utils import (
    metadata_database_from_query,
    parse_query,
    query_param,
)

logger = logging.getLogger(__name__)


def _split_datasets(raw: str) -> list[str]:
    """Split a comma-separated ``project.dataset`` list."""
    return [part.strip() for part in raw.split(",") if part.strip()]


def _parse_project_dataset(ref: str) -> tuple[str, str]:
    """Split a fully-qualified ``project.dataset`` reference."""
    if ref.count(".") != 1:
        raise ValueError(
            f"BigQuery dataset must be fully qualified as project.dataset, got {ref!r}"
        )
    project, dataset = ref.split(".", 1)
    if not project or not dataset:
        raise ValueError(
            f"BigQuery dataset must be fully qualified as project.dataset, got {ref!r}"
        )
    return project, dataset


def _parse_connection_string(
    connection_string: str,
) -> tuple[str, list[tuple[str, str]], str, str | None, str | None]:
    """Parse a BigQuery URL into logical name, datasets, and auth options.

    Returns
    ``(database_name, [(project, dataset), ...], billing_project, credentials_path, location)``.

    Multi-database loading matches SQLite: put one URL per logical database in
    ``CONNECTION_STRINGS``. Eval routes by ``database_name`` (same role as the
    SQLite file stem).

    Logical form (preferred for Spider2; host = eval ``db_id``)::

        bigquery://ga4?datasets=bigquery-public-data.ga4_obfuscated_sample_ecommerce&billing_project=MY_GCP
        bigquery://austin?datasets=bigquery-public-data.austin_311,bigquery-public-data.austin_crime&billing_project=MY_GCP

    Physical shorthand (``database_name`` defaults to the dataset name)::

        bigquery://bigquery-public-data/ga4_obfuscated_sample_ecommerce?billing_project=MY_GCP
    """
    parsed = urlparse(connection_string)
    if parsed.scheme.split("+", 1)[0].lower() != "bigquery":
        raise ValueError(f"Not a BigQuery URL: {connection_string}")

    host = (parsed.hostname or "").strip()
    if not host:
        raise ValueError(
            "BigQuery connection string requires a host, e.g. "
            "bigquery://ga4?datasets=bigquery-public-data.ga4_obfuscated_sample_ecommerce"
            " or bigquery://PROJECT/DATASET"
        )

    query = parse_query(connection_string)
    path_dataset = unquote(parsed.path.lstrip("/")).strip() or None
    if path_dataset and "/" in path_dataset:
        raise ValueError(
            f"Invalid BigQuery dataset path {path_dataset!r}; "
            "use bigquery://PROJECT/DATASET or ?datasets=project.dataset"
        )

    dataset_refs: list[str] = []
    raw_datasets = query_param(query, "datasets")
    if raw_datasets:
        dataset_refs.extend(_split_datasets(raw_datasets))
    raw_dataset = query_param(query, "dataset")
    if raw_dataset:
        dataset_refs.extend(_split_datasets(raw_dataset))

    if path_dataset:
        # Physical shorthand: host is the data project, path is the dataset.
        if "." in path_dataset:
            dataset_refs.append(path_dataset)
        else:
            dataset_refs.append(f"{host}.{path_dataset}")
        default_name = path_dataset.split(".")[-1]
    else:
        # Logical form: host is the routing name (SQLite-stem analogue).
        default_name = host

    # Preserve order while de-duplicating.
    seen: set[str] = set()
    unique_refs: list[str] = []
    for ref in dataset_refs:
        if ref not in seen:
            seen.add(ref)
            unique_refs.append(ref)

    if not unique_refs:
        raise ValueError(
            "BigQuery connection string requires a dataset path or "
            "?datasets=project.dataset[,project.dataset...]"
        )

    datasets = [_parse_project_dataset(ref) for ref in unique_refs]
    billing_project = query_param(query, "billing_project") or datasets[0][0]
    credentials_path = query_param(query, "credentials")
    location = query_param(query, "location")
    database_name = metadata_database_from_query(query) or default_name
    return database_name, datasets, billing_project, credentials_path, location


def _load_credentials(credentials_path: str | None) -> Any | None:
    """Load service-account credentials from an explicit path or return None (ADC)."""
    path = credentials_path or os.environ.get("GOOGLE_APPLICATION_CREDENTIALS")
    if not path:
        return None
    resolved = Path(path).expanduser()
    if not resolved.is_file():
        raise FileNotFoundError(f"BigQuery credentials file not found: {resolved}")
    from google.oauth2 import service_account

    return service_account.Credentials.from_service_account_file(str(resolved))


class BigQueryDatabase(SQLDatabase):
    """Concrete :class:`SQLDatabase` backed by ``google-cloud-bigquery``.

    Multi-database loading matches SQLite: one connection string per logical
    database in ``CONNECTION_STRINGS``. Each connector's ``database_name`` is the
    eval / Neo4j routing key (analogous to the SQLite file stem).

    A single logical database may span multiple physical BigQuery datasets
    (common in Spider2-lite). Pass them via ``?datasets=project.ds1,project.ds2``.

    Auth uses ``?credentials=/path/key.json`` or
    ``GOOGLE_APPLICATION_CREDENTIALS``. For public datasets, set
    ``?billing_project=YOUR_GCP_PROJECT``.
    """

    def __init__(self, connection_string: str) -> None:
        (
            self._database_name,
            self._datasets,
            self._billing_project,
            credentials_path,
            self._location,
        ) = _parse_connection_string(connection_string)
        self._connection_string = connection_string
        self._credentials = _load_credentials(credentials_path)
        self._client = None
        # Open eagerly to surface auth / project errors early (same as SQLite).
        self._get_client()
        logger.debug(
            "BigQuery connected (database=%r, datasets=%s, billing=%s).",
            self._database_name,
            [f"{p}.{d}" for p, d in self._datasets],
            self._billing_project,
        )

    def _get_client(self):
        if self._client is None:
            from google.cloud import bigquery

            kwargs: dict[str, Any] = {"project": self._billing_project}
            if self._credentials is not None:
                kwargs["credentials"] = self._credentials
            if self._location:
                kwargs["location"] = self._location
            self._client = bigquery.Client(**kwargs)
        return self._client

    @property
    def dialect(self) -> str:
        return "bigquery"

    @property
    def database_name(self) -> str:
        return self._database_name

    @staticmethod
    def _information_schema_table(project: str, dataset: str, table: str) -> str:
        return f"`{project}.{dataset}.INFORMATION_SCHEMA.{table}`"

    def _concat_dataset_frames(
        self,
        build_sql: Any,
        empty_columns: list[str],
    ) -> pd.DataFrame:
        """Run *build_sql(project, dataset)* for every dataset and concatenate."""
        frames: list[pd.DataFrame] = []
        for project, dataset in self._datasets:
            try:
                frame = self.execute(build_sql(project, dataset))
            except Exception:
                logger.exception(
                    "BigQuery introspection failed for %s.%s", project, dataset
                )
                continue
            if not frame.empty:
                frames.append(frame)
        if not frames:
            return pd.DataFrame(columns=empty_columns)
        return pd.concat(frames, ignore_index=True)

    # ------------------------------------------------------------------
    # Execution
    # ------------------------------------------------------------------

    def execute(self, sql: str, parameters: Optional[list] = None) -> pd.DataFrame:
        if parameters:
            raise ValueError(
                "BigQuery connector does not support positional parameters; "
                "embed literals in the SQL or use named query parameters."
            )
        client = self._get_client()
        query_job = client.query(sql, location=self._location)
        return query_job.result().to_dataframe(create_bqstorage_client=False)

    # ------------------------------------------------------------------
    # Schema introspection
    # ------------------------------------------------------------------

    def get_tables(self) -> pd.DataFrame:
        view_type = TableTypes.VIEW
        base_table_type = TableTypes.BASE_TABLE

        def build_sql(project: str, dataset: str) -> str:
            return f"""
            SELECT
                table_schema AS table_schema,
                table_name   AS table_name,
                CASE table_type
                    WHEN 'VIEW' THEN '{view_type}'
                    ELSE '{base_table_type}'
                END AS table_type
            FROM {self._information_schema_table(project, dataset, "TABLES")}
            WHERE table_type IN ('BASE TABLE', 'VIEW')
            ORDER BY table_schema, table_name
            """

        return self._concat_dataset_frames(
            build_sql,
            ["table_schema", "table_name", "table_type"],
        )

    def get_columns(self) -> pd.DataFrame:
        def build_sql(project: str, dataset: str) -> str:
            return f"""
            SELECT
                table_schema     AS table_schema,
                table_name       AS table_name,
                column_name      AS column_name,
                data_type        AS data_type,
                is_nullable      AS is_nullable,
                ordinal_position AS ordinal_position
            FROM {self._information_schema_table(project, dataset, "COLUMNS")}
            ORDER BY table_schema, table_name, ordinal_position
            """

        return self._concat_dataset_frames(
            build_sql,
            [
                "table_schema",
                "table_name",
                "column_name",
                "data_type",
                "is_nullable",
                "ordinal_position",
            ],
        )

    def get_queries(self, hours: int = 24) -> pd.DataFrame:
        return pd.DataFrame(columns=["end_time", "query_text"])

    def get_views(self) -> pd.DataFrame:
        def build_sql(project: str, dataset: str) -> str:
            return f"""
            SELECT
                table_schema AS table_schema,
                table_name   AS table_name,
                view_definition AS view_definition
            FROM {self._information_schema_table(project, dataset, "VIEWS")}
            ORDER BY table_schema, table_name
            """

        return self._concat_dataset_frames(
            build_sql,
            ["table_schema", "table_name", "view_definition"],
        )

    def get_pks(self) -> pd.DataFrame:
        def build_sql(project: str, dataset: str) -> str:
            info = self._information_schema_table
            return f"""
            SELECT
                tc.table_schema AS table_schema,
                tc.table_name   AS table_name,
                kcu.column_name AS column_name,
                kcu.ordinal_position AS ordinal_position
            FROM {info(project, dataset, "TABLE_CONSTRAINTS")} AS tc
            JOIN {info(project, dataset, "KEY_COLUMN_USAGE")} AS kcu
              ON tc.constraint_name = kcu.constraint_name
             AND tc.table_schema = kcu.table_schema
            WHERE tc.constraint_type = 'PRIMARY KEY'
            ORDER BY tc.table_schema, tc.table_name, kcu.ordinal_position
            """

        return self._concat_dataset_frames(
            build_sql,
            [
                "table_schema",
                "table_name",
                "column_name",
                "ordinal_position",
            ],
        )

    def get_fks(self) -> pd.DataFrame:
        # BigQuery INFORMATION_SCHEMA does not expose standard FK metadata for
        # most public datasets; return an empty frame rather than failing ingest.
        return pd.DataFrame(
            columns=[
                "table_schema",
                "table_name",
                "column_name",
                "referenced_schema",
                "referenced_table",
                "referenced_column",
            ]
        )

    def ping(self) -> None:
        """Verify credentials and that at least one dataset is visible."""
        project, dataset = self._datasets[0]
        self.execute(
            f"SELECT 1 FROM {self._information_schema_table(project, dataset, 'TABLES')} "
            "LIMIT 1"
        )

    def close(self) -> None:
        if self._client is not None:
            try:
                self._client.close()
            except Exception:
                logger.exception("Failed to close BigQuery client")
            self._client = None
