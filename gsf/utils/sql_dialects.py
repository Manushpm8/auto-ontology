# SPDX-FileCopyrightText: Copyright (c) 2026, NVIDIA CORPORATION & AFFILIATES.
# All rights reserved.
# SPDX-License-Identifier: Apache-2.0

"""Shared connector-dialect to sqlglot-dialect names."""

CONNECTOR_TO_SQLGLOT_DIALECT = {
    "postgresql": "postgres",
    "postgres": "postgres",
    "sqlite": "sqlite",
    "duckdb": "duckdb",
    "snowflake": "snowflake",
    "databricks": "databricks",
    "mysql": "mysql",
    "bigquery": "bigquery",
    "heavydb": "postgres",
}


def get_sqlglot_dialect(
    dialect: str | None,
    *,
    preserve_unknown: bool = False,
) -> str | None:
    """Translate a connector dialect, optionally preserving unknown names."""
    normalized = (dialect or "").strip().lower()
    mapped = CONNECTOR_TO_SQLGLOT_DIALECT.get(normalized)
    if mapped is not None:
        return mapped
    if preserve_unknown:
        return dialect or None
    return None


__all__ = ["CONNECTOR_TO_SQLGLOT_DIALECT", "get_sqlglot_dialect"]
