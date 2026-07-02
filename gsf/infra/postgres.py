# SPDX-FileCopyrightText: Copyright (c) 2024-2026, NVIDIA CORPORATION & AFFILIATES.
# All rights reserved.
# SPDX-License-Identifier: Apache-2.0

"""Env-derived Postgres connection config, shared across the codebase.

Not VDB-specific: the server, ingestion service, and dev tools all build the
local Postgres URL from the same ``POSTGRES_*`` env vars via this helper.
"""

from __future__ import annotations

import os


def get_postgres_connection_string() -> str:
    """Build the local Postgres URL from ``POSTGRES_*`` env vars.

    ``database`` overrides ``POSTGRES_DATABASE`` when given (useful for
    multi-DB tools that target several databases on the same instance).
    """
    host = os.environ.get("POSTGRES_HOST", "localhost")
    port = os.environ.get("POSTGRES_PORT", "5432")
    user = os.environ["POSTGRES_USER"]
    password = os.environ["POSTGRES_PASSWORD"]
    db = os.environ.get("POSTGRES_DATABASE", "gsf")
    return f"postgresql://{user}:{password}@{host}:{port}/{db}"
