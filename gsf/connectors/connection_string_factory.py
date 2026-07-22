# SPDX-FileCopyrightText: Copyright (c) 2024-2026, NVIDIA CORPORATION & AFFILIATES.
# All rights reserved.
# SPDX-License-Identifier: Apache-2.0

"""Build a connector connection string from a structured connection object.

UI-managed connections are stored as a JSON ``connection`` object on the
catalog DB node or in Vault. The connectors still consume a connection string,
so this module converts the structured form into the URL the connectors expect.
"""

from __future__ import annotations

from typing import Any, Mapping
from urllib.parse import quote

DEFAULT_POSTGRES_PORT = "5432"
DEFAULT_HEAVYDB_PORT = "6274"
DEFAULT_HEAVYDB_PROTOCOL = "binary"


def _require(connection: Mapping[str, Any], key: str) -> str:
    value = str(connection.get(key) or "").strip()
    if not value:
        raise ValueError(f"Connection is missing required field: {key!r}")
    return value


def _enc(value: str) -> str:
    """Percent-encode a URL component, escaping reserved chars like ``/`` and ``@``."""
    return quote(value, safe="")


def build_connection_string(connection: Mapping[str, Any]) -> str:
    """Return a connector connection string for a structured *connection*."""
    conn_type = str(connection.get("type") or "").strip().lower()

    if conn_type in ("postgresql", "postgres"):
        host = _require(connection, "host").rstrip("/")
        user = _require(connection, "user")
        password = _require(connection, "password")
        database = _require(connection, "database")
        port = str(connection.get("port") or "").strip() or DEFAULT_POSTGRES_PORT
        return (
            f"postgresql://{_enc(user)}:{_enc(password)}@{host}:{port}/{_enc(database)}"
        )

    if conn_type == "snowflake":
        account = _require(connection, "account")
        warehouse = _require(connection, "warehouse")
        user = _require(connection, "user")
        password = _require(connection, "password")
        database = _require(connection, "database")
        url = (
            f"snowflake://{_enc(user)}:{_enc(password)}@{account}"
            f"?warehouse={_enc(warehouse)}&database={_enc(database)}"
        )
        metadata_database = str(connection.get("metadata_database") or "").strip()
        if metadata_database:
            url += f"&metadata_database={_enc(metadata_database)}"
        metadata_file = str(connection.get("metadata_file") or "").strip()
        if metadata_file:
            url += f"&metadata_file={_enc(metadata_file)}"
        return url

    if conn_type == "bigquery":
        # Logical form (SQLite-like): name + datasets=project.dataset[,...]
        # Physical shorthand: project + dataset path when name is omitted.
        name = str(connection.get("name") or connection.get("database") or "").strip()
        datasets = connection.get("datasets")
        if isinstance(datasets, str):
            dataset_list = [d.strip() for d in datasets.split(",") if d.strip()]
        elif isinstance(datasets, list):
            dataset_list = [str(d).strip() for d in datasets if str(d).strip()]
        else:
            dataset_list = []

        project = str(connection.get("project") or "").strip()
        dataset = str(connection.get("dataset") or "").strip()
        if not dataset_list and project and dataset:
            dataset_list = [f"{project}.{dataset}"]
        if not dataset_list:
            raise ValueError(
                "BigQuery connection requires 'datasets' "
                "(or 'project' + 'dataset')"
            )

        if name:
            url = f"bigquery://{_enc(name)}"
            params = [f"datasets={_enc(','.join(dataset_list))}"]
        else:
            first_project, first_dataset = dataset_list[0].split(".", 1)
            url = f"bigquery://{_enc(first_project)}/{_enc(first_dataset)}"
            params = []
            if len(dataset_list) > 1:
                params.append(f"datasets={_enc(','.join(dataset_list))}")

        billing_project = str(connection.get("billing_project") or "").strip()
        if billing_project:
            params.append(f"billing_project={_enc(billing_project)}")
        credentials = str(connection.get("credentials") or "").strip()
        if credentials:
            params.append(f"credentials={_enc(credentials)}")
        location = str(connection.get("location") or "").strip()
        if location:
            params.append(f"location={_enc(location)}")
        metadata_database = str(connection.get("metadata_database") or "").strip()
        if metadata_database:
            params.append(f"metadata_database={_enc(metadata_database)}")
        if params:
            url += "?" + "&".join(params)
        return url

    if conn_type == "databricks":
        host = _require(connection, "host").rstrip("/")
        if host.startswith(("https://", "http://")):
            host = host.split("://", 1)[1]
        http_path = _require(connection, "http_path")
        access_token = _require(connection, "password")
        catalog = _require(connection, "database")
        return (
            f"databricks://token:{_enc(access_token)}@{host}/{_enc(catalog)}"
            f"?http_path={_enc(http_path)}"
        )

    if conn_type == "heavydb":
        host = _require(connection, "host").rstrip("/")
        user = _require(connection, "user")
        password = _require(connection, "password")
        database = _require(connection, "database")
        port = str(connection.get("port") or "").strip() or DEFAULT_HEAVYDB_PORT
        protocol = (
            str(connection.get("protocol") or "").strip() or DEFAULT_HEAVYDB_PROTOCOL
        )
        return (
            f"heavydb://{_enc(user)}:{_enc(password)}@{host}:{port}/{_enc(database)}"
            f"?protocol={_enc(protocol)}"
        )

    raise ValueError(f"Unsupported connection type: {conn_type!r}")
