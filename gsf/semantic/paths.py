"""Shortest-path helpers for data-layer join paths on ROLE edges."""

from __future__ import annotations

import json
import logging
from typing import Any

from nemo_retriever.tabular_data.ingestion.model.reserved_words import (
    Edges,
    Labels,
)
from nemo_retriever.tabular_data.neo4j import get_neo4j_conn

logger = logging.getLogger(__name__)

_DATA_PATH = f"""
MATCH (src:{Labels.TABLE} {{id: $src_table_id}}),
      (tgt:{Labels.TABLE} {{id: $tgt_table_id}})
MATCH p = shortestPath(
    (src)-[:{Edges.FOREIGN_KEY}|{Edges.JOIN}*..8]-(tgt)
)
RETURN [n IN nodes(p) | n.name] AS path_nodes,
       [r IN relationships(p) | type(r)] AS path_rels
LIMIT 1
"""


def compute_join_path(
    src_table_id: str,
    tgt_table_id: str,
) -> list[dict[str, Any]] | None:
    if src_table_id == tgt_table_id:
        return [{"node": src_table_id, "type": "Table"}]
    rows = get_neo4j_conn().query_read(
        _DATA_PATH,
        {
            "src_table_id": src_table_id,
            "tgt_table_id": tgt_table_id,
        },
    )
    if not rows or not rows[0].get("path_nodes"):
        return None
    nodes = rows[0]["path_nodes"]
    rels = rows[0].get("path_rels") or []
    steps: list[dict[str, Any]] = []
    for i, node in enumerate(nodes):
        step: dict[str, Any] = {"node": node, "type": "Table"}
        if i < len(rels):
            step["via"] = rels[i]
        steps.append(step)
    return steps


def path_to_json(steps: list[dict[str, Any]] | None) -> str:
    return json.dumps(steps or [])
