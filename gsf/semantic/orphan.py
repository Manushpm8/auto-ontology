"""Orphan detection — tables with no Term after BFS."""

from __future__ import annotations

from typing import Any

from gsf.semantic import neo4j_dal
from gsf.semantic.loaders import fetch_table_by_id


def pick_orphan_seed() -> dict[str, Any] | None:
    """First catalog orphan table suitable as a BFS seed, or None."""
    for orphan in neo4j_dal.list_orphan_tables():
        table = fetch_table_by_id(orphan["id"])
        if table:
            return table
    return None
