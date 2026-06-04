# SPDX-FileCopyrightText: Copyright (c) 2024-2026, NVIDIA CORPORATION & AFFILIATES.
# All rights reserved.
# SPDX-License-Identifier: Apache-2.0

"""Data access for the connections settings view.

Illumex lists ``:connection`` nodes filtered by ``account_id`` (see
``get_account_connections`` in connectors). GSF will read ``:db`` nodes
from the catalog graph when wired; for now :func:`list_connections`
returns an empty list.
"""

from __future__ import annotations

from typing import Any


def list_connections() -> list[dict[str, Any]]:
    """Return connections for the settings UI (stub: empty until Neo4j query is enabled)."""
    return []
