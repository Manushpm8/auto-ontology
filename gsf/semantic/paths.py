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
        src_column: CASE WHEN startNode(relationships(p)[i]):{Labels.COLUMN}
                         THEN startNode(relationships(p)[i]).name ELSE null END,
        tgt_column: CASE WHEN endNode(relationships(p)[i]):{Labels.COLUMN}
                         THEN endNode(relationships(p)[i]).name ELSE null END,
        join_columns: relationships(p)[i].join_columns
      }}
     ] AS join_conditions
WHERE size(path_nodes) >= 2
RETURN path_nodes, join_conditions
LIMIT 1
"""


def _parse_join_columns(raw: Any) -> list[dict[str, str]]:
    """Normalise join_columns edge property to [{src, tgt}] regardless of storage format."""
    if not raw:
        return []
    if isinstance(raw, str):
        try:
            raw = json.loads(raw)
        except (ValueError, TypeError):
            return []
    if isinstance(raw, list):
        return [
            {
                "src": str(item.get("src", item.get("source", ""))),
                "tgt": str(item.get("tgt", item.get("target", ""))),
            }
            for item in raw
            if isinstance(item, dict)
        ]
    return []


def compute_join_path(
    src_table_id: str,
    tgt_table_id: str,
) -> list[dict[str, Any]] | None:
    """Return a hop-centric join path or None.

    Each hop has the form::

        {"hop": N, "source": {"table": "...", "column": "..."},
                   "target": {"table": "...", "column": "..."}}

    Returns None when no graph path exists OR when any hop lacks column-level
    join conditions (caller should fall through to LLM inference).
    """
    if src_table_id == tgt_table_id:
        return []

    rows = get_neo4j_conn().query_read(
        _DATA_PATH,
        {"src_table_id": src_table_id, "tgt_table_id": tgt_table_id},
    )
    if not rows or not rows[0].get("path_nodes"):
        return None

    path_nodes: list[str] = rows[0]["path_nodes"]
    join_conditions: list[dict[str, Any]] = rows[0].get("join_conditions") or []

    hops: list[dict[str, Any]] = []
    for i, (src_name, tgt_name) in enumerate(zip(path_nodes, path_nodes[1:])):
        if i >= len(join_conditions):
            logger.debug(
                "No join condition for hop %d (%s→%s)", i + 1, src_name, tgt_name
            )
            return None  # incomplete — let LLM try

        cond = join_conditions[i]

        if cond.get("src_column") and cond.get("tgt_column"):
            # FK edge between Column nodes
            hops.append(
                {
                    "hop": i + 1,
                    "source": {"table": src_name, "column": cond["src_column"]},
                    "target": {"table": tgt_name, "column": cond["tgt_column"]},
                }
            )
        else:
            # JOIN edge between Table nodes — column info lives on the edge property
            cols = _parse_join_columns(cond.get("join_columns"))
            if not cols:
                logger.debug(
                    "JOIN edge %s→%s has no join_columns property", src_name, tgt_name
                )
                return None  # incomplete — let LLM try
            # one hop per column pair (composite keys produce multiple hops over same tables)
            for j, pair in enumerate(cols):
                if not pair.get("src") or not pair.get("tgt"):
                    return None
                hops.append(
                    {
                        "hop": len(hops) + 1,
                        "source": {"table": src_name, "column": pair["src"]},
                        "target": {"table": tgt_name, "column": pair["tgt"]},
                    }
                )

    return hops or None


def path_to_json(hops: list[dict[str, Any]] | None) -> str:
    return json.dumps(hops or [])
