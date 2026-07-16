# SPDX-FileCopyrightText: Copyright (c) 2024-2026, NVIDIA CORPORATION & AFFILIATES.
# All rights reserved.
# SPDX-License-Identifier: Apache-2.0

"""Build a connector connection string from a structured connection object.

UI-managed connections are stored as a JSON ``connection`` object on the
catalog DB node. Passwords may instead be referenced by an allowlisted
environment variable. The connectors still consume a connection string, so
this module converts the structured form into the URL the connectors expect.
"""

from __future__ import annotations

import os
import secrets
from typing import Any, Mapping
from urllib.parse import quote

DEFAULT_POSTGRES_PORT = "5432"
DEFAULT_HEAVYDB_PORT = "6274"
DEFAULT_HEAVYDB_PROTOCOL = "binary"
PASSWORD_ENV_BY_CONNECTION_TYPE = {
    "databricks": "DATABRICKS_TOKEN",
    "snowflake": "SNOWFLAKE_PASSWORD",
}


def _require(connection: Mapping[str, Any], key: str) -> str:
    value = str(connection.get(key) or "").strip()
    if not value:
        raise ValueError(f"Connection is missing required field: {key!r}")
    return value


def _enc(value: str) -> str:
    """Percent-encode a URL component, escaping reserved chars like ``/`` and ``@``."""
    return quote(value, safe="")


def prepare_connection_for_storage(
    connection: Mapping[str, Any],
) -> dict[str, Any]:
    """Replace a UI-submitted password with its injected environment reference.

    Astra Shared Vault injects secrets into the pod environment but deliberately
    grants workloads read-only Vault access. When the conventional password
    variable for a connector is present, persist only ``password_env`` in Neo4j
    and require the UI-submitted password to match the injected value.

    Connectors without an injected password retain the existing local-development
    fallback and store their complete connection object.
    """

    prepared = dict(connection)
    prepared.pop("password_env", None)
    connection_type = str(prepared.get("type") or "").strip().lower()
    password_env = PASSWORD_ENV_BY_CONNECTION_TYPE.get(connection_type)
    injected_password = os.environ.get(password_env, "") if password_env else ""
    if not injected_password:
        return prepared

    submitted_password = _require(prepared, "password")
    if not secrets.compare_digest(submitted_password, injected_password):
        raise ValueError(
            f"Submitted {connection_type} password does not match the "
            f"{password_env} secret injected into this deployment"
        )

    prepared.pop("password", None)
    prepared["password_env"] = password_env
    return prepared


def resolve_connection_credentials(
    connection: Mapping[str, Any],
) -> dict[str, Any]:
    """Hydrate an environment-backed password for connector construction."""

    resolved = dict(connection)
    password_env = str(resolved.pop("password_env", "") or "").strip()
    if not password_env:
        return resolved

    connection_type = str(resolved.get("type") or "").strip().lower()
    expected_password_env = PASSWORD_ENV_BY_CONNECTION_TYPE.get(connection_type)
    if password_env != expected_password_env:
        raise ValueError(
            f"Password environment variable {password_env!r} is not allowed "
            f"for connection type {connection_type!r}"
        )

    password = os.environ.get(password_env, "")
    if not password:
        raise ValueError(
            f"Connection requires password environment variable {password_env!r}"
        )
    resolved["password"] = password
    return resolved


def build_connection_string(connection: Mapping[str, Any]) -> str:
    """Return a connector connection string for a structured *connection*."""
    connection = resolve_connection_credentials(connection)
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
        return (
            f"snowflake://{_enc(user)}:{_enc(password)}@{account}"
            f"?warehouse={_enc(warehouse)}&database={_enc(database)}"
        )

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
