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
    (src)-[:{Edges.CONTAINS}|{Edges.FOREIGN_KEY}|{Edges.JOIN}*..14]-(tgt)
)
WITH p,
     [n IN nodes(p) WHERE n:{Labels.TABLE} | n.name] AS path_nodes,
     [i IN range(0, size(relationships(p)) - 1)
      WHERE type(relationships(p)[i]) IN ['{Edges.FOREIGN_KEY}', '{Edges.JOIN}'] |
      {{
        via: type(relationships(p)[i]),
        src_column: startNode(relationships(p)[i]).name,
        tgt_column: endNode(relationships(p)[i]).name
      }}
     ] AS join_conditions
WHERE size(path_nodes) >= 2
RETURN path_nodes, join_conditions
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
    path_nodes: list[str] = rows[0]["path_nodes"]
    join_conditions: list[dict[str, Any]] = rows[0].get("join_conditions") or []

    last = len(path_nodes) - 1
    steps: list[dict[str, Any]] = [
        {"node": path_nodes[0], "type": "Table", "role": "source"}
    ]
    for i, table_name in enumerate(path_nodes[1:]):
        step: dict[str, Any] = {
            "node": table_name,
            "type": "Table",
            "role": "target" if i == last - 1 else "intermediate",
        }
        if i < len(join_conditions):
            cond = join_conditions[i]
            step["via"] = cond["via"]
            step["src_column"] = cond["src_column"]
            step["tgt_column"] = cond["tgt_column"]
        steps.append(step)
    return steps


def path_to_json(steps: list[dict[str, Any]] | None) -> str:
    return json.dumps(steps or [])
