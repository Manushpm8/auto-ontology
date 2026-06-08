"""Orphan detection — tables with no Term after BFS."""

from __future__ import annotations

from typing import Any

from gsf.semantic import neo4j_dal


def pick_orphan_seed(
    tables_by_id: dict[str, dict[str, Any]],
) -> dict[str, Any] | None:
    """First catalog orphan table suitable as a BFS seed, or None."""
    for orphan in neo4j_dal.list_orphan_tables():
        table = tables_by_id.get(orphan["id"])
        if table:
            return table
    return None
