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
     [n IN nodes(p) WHERE n:{Labels.TABLE} |
       {{
         name: n.name,
         schema: [(n)<-[:{Edges.CONTAINS}]-(s:{Labels.SCHEMA}) | s.name][0]
       }}
     ] AS path_nodes
WHERE size(path_nodes) >= 2
UNWIND range(0, size(relationships(p)) - 1) AS i
WITH path_nodes, i, relationships(p)[i] AS r
WHERE type(r) IN ['{Edges.FOREIGN_KEY}', '{Edges.JOIN}']
WITH path_nodes, i, r, startNode(r) AS r_start, endNode(r) AS r_end
OPTIONAL MATCH (r_start)<-[:{Edges.CONTAINS}]-(src_tbl:{Labels.TABLE})
WITH path_nodes,
     i,
     type(r) AS via,
     CASE WHEN r_start:{Labels.COLUMN} THEN r_start.name ELSE null END AS src_column,
     CASE WHEN r_end:{Labels.COLUMN} THEN r_end.name ELSE null END AS tgt_column,
     src_tbl.name AS src_col_table,
     r.join_columns AS join_columns
ORDER BY i
WITH path_nodes,
     collect({{
       via: via,
       src_column: src_column,
       tgt_column: tgt_column,
       src_col_table: src_col_table,
       join_columns: join_columns
     }}) AS join_conditions
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

    path_nodes: list[dict[str, str]] = rows[0]["path_nodes"]
    join_conditions: list[dict[str, Any]] = rows[0].get("join_conditions") or []

    hops: list[dict[str, Any]] = []
    for i, (src_node, tgt_node) in enumerate(zip(path_nodes, path_nodes[1:])):
        src_name = src_node["name"]
        tgt_name = tgt_node["name"]
        src_schema = src_node.get("schema")
        tgt_schema = tgt_node.get("schema")
        if i >= len(join_conditions):
            logger.debug(
                "No join condition for hop %d (%s→%s)", i + 1, src_name, tgt_name
            )
            return None  # incomplete — let LLM try

        cond = join_conditions[i]

        if cond.get("src_column") and cond.get("tgt_column"):
            # FK edge between Column nodes.  The edge's stored direction may be
            # the reverse of the traversal direction, so use src_col_table to
            # assign columns to the correct hop endpoints.
            fk_start_col = cond["src_column"]
            fk_end_col = cond["tgt_column"]
            fk_start_table = cond.get("src_col_table")
            if fk_start_table == src_name:
                hop_src_col, hop_tgt_col = fk_start_col, fk_end_col
            else:
                # Edge traversed in reverse — swap columns
                hop_src_col, hop_tgt_col = fk_end_col, fk_start_col
            hops.append({
                "hop": i + 1,
                "source": {"schema": src_schema, "table": src_name, "column": hop_src_col},
                "target": {"schema": tgt_schema, "table": tgt_name, "column": hop_tgt_col},
            })
        else:
            # JOIN edge between Table nodes — column info lives on the edge property
            cols = _parse_join_columns(cond.get("join_columns"))
            if not cols:
                logger.debug(
                    "JOIN edge %s→%s has no join_columns property", src_name, tgt_name
                )
                return None  # incomplete — let LLM try
            # one hop per column pair (composite keys produce multiple hops over same tables)
            for pair in cols:
                if not pair.get("src") or not pair.get("tgt"):
                    return None
                hops.append({
                    "hop": len(hops) + 1,
                    "source": {"schema": src_schema, "table": src_name, "column": pair["src"]},
                    "target": {"schema": tgt_schema, "table": tgt_name, "column": pair["tgt"]},
                })

    return hops or None


def path_to_json(hops: list[dict[str, Any]] | None) -> str:
    return json.dumps(hops or [])
