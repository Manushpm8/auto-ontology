# SPDX-FileCopyrightText: Copyright (c) 2026, NVIDIA CORPORATION & AFFILIATES.
# All rights reserved.
# SPDX-License-Identifier: Apache-2.0

"""Neo4j foreign-key and join edge reads.

Only the Cypher query function lives here. The non-graph helpers
(_apply_foreign_key_hints, get_relevant_fks_from_candidates_tables,
get_relevant_tables_with_fks) remain in
gsf/retrieval/data_access/foreign_keys.py.
"""

from __future__ import annotations

import json
import logging

from nemo_retriever.tabular_data.ingestion.model.reserved_words import Edges, Labels
from nemo_retriever.tabular_data.neo4j import get_neo4j_conn

logger = logging.getLogger(__name__)


def get_relevant_fks(tables_ids: list) -> list[dict]:
    """Expand up to 3 FK/join hops and return all FK pairs among connected tables.

    Uses the ingest graph labels/edges (``Table``/``Column``/``FOREIGN_KEY``/
    ``CONTAINS``/``JOIN``). Legacy lowercase ``:table``/``:fk``/``:schema``
    labels never match the current Neo4j graph and always returned [].
    """
    if not tables_ids:
        return []

    query = f"""
    WITH $tables_ids AS current_ids

    OPTIONAL MATCH (t0:{Labels.TABLE})-[:{Edges.CONTAINS}]->(:{Labels.COLUMN})
          -[:{Edges.FOREIGN_KEY}]-(:{Labels.COLUMN})<-[:{Edges.CONTAINS}]-(t1:{Labels.TABLE})
    WHERE t0.id IN current_ids
    WITH current_ids, collect(DISTINCT t1.id) AS new_ids_1
    WITH current_ids + new_ids_1 AS level_1_ids

    OPTIONAL MATCH (t1:{Labels.TABLE})-[:{Edges.CONTAINS}]->(:{Labels.COLUMN})
          -[:{Edges.FOREIGN_KEY}]-(:{Labels.COLUMN})<-[:{Edges.CONTAINS}]-(t2:{Labels.TABLE})
    WHERE t1.id IN level_1_ids
    WITH level_1_ids, collect(DISTINCT t2.id) AS new_ids_2
    WITH level_1_ids + new_ids_2 AS level_2_ids

    OPTIONAL MATCH (t2:{Labels.TABLE})-[:{Edges.CONTAINS}]->(:{Labels.COLUMN})
          -[:{Edges.FOREIGN_KEY}]-(:{Labels.COLUMN})<-[:{Edges.CONTAINS}]-(t3:{Labels.TABLE})
    WHERE t2.id IN level_2_ids
    WITH level_2_ids, collect(DISTINCT t3.id) AS new_ids_3
    WITH level_2_ids + new_ids_3 AS all_table_ids

    MATCH (t1:{Labels.TABLE})-[:{Edges.CONTAINS}]->(col1:{Labels.COLUMN})
          -[:{Edges.FOREIGN_KEY}]-(col2:{Labels.COLUMN})<-[:{Edges.CONTAINS}]-(t2:{Labels.TABLE})
    WHERE t1.id IN all_table_ids AND t2.id IN all_table_ids
      AND t1.id < t2.id
    OPTIONAL MATCH (t1)<-[:{Edges.CONTAINS}]-(s1:{Labels.SCHEMA})
    OPTIONAL MATCH (t2)<-[:{Edges.CONTAINS}]-(s2:{Labels.SCHEMA})

    RETURN collect(DISTINCT {{
        table1: CASE WHEN s1.name IS NULL THEN t1.name ELSE s1.name + '.' + t1.name END,
        column1: col1.name,
        column1_datatype: coalesce(col1.data_type, 'None'),
        table2: CASE WHEN s2.name IS NULL THEN t2.name ELSE s2.name + '.' + t2.name END,
        column2: col2.name,
        column2_datatype: coalesce(col2.data_type, 'None')
    }}) AS list_of_foreign_keys
    """
    results = get_neo4j_conn().query_read(query, {"tables_ids": tables_ids})
    result_fks = results[0]["list_of_foreign_keys"] if results else []

    query_joins = f"""
    WITH $tables_ids AS seed_ids

    OPTIONAL MATCH (t0:{Labels.TABLE})-[:{Edges.JOIN}]-(t1:{Labels.TABLE})
    WHERE t0.id IN seed_ids
    WITH seed_ids, collect(DISTINCT t1.id) AS new_ids_1
    WITH seed_ids + new_ids_1 AS level_1_ids

    OPTIONAL MATCH (t1:{Labels.TABLE})-[:{Edges.JOIN}]-(t2:{Labels.TABLE})
    WHERE t1.id IN level_1_ids
    WITH level_1_ids, collect(DISTINCT t2.id) AS new_ids_2
    WITH level_1_ids + new_ids_2 AS level_2_ids

    OPTIONAL MATCH (t2:{Labels.TABLE})-[:{Edges.JOIN}]-(t3:{Labels.TABLE})
    WHERE t2.id IN level_2_ids
    WITH level_2_ids, collect(DISTINCT t3.id) AS new_ids_3
    WITH level_2_ids + new_ids_3 AS all_table_ids

    OPTIONAL MATCH (t1:{Labels.TABLE})-[rel:{Edges.JOIN}]-(t2:{Labels.TABLE})
    WHERE t1.id IN all_table_ids AND t2.id IN all_table_ids
      AND t1.id < t2.id
      AND rel.join IS NOT NULL

    WITH t1, t2, rel
    WHERE t1 IS NOT NULL AND rel IS NOT NULL

    WITH t1, t2, rel,
         trim(apoc.text.split(rel.join, '<=|>=|=|<|>')[0]) AS left_side,
         trim(apoc.text.split(rel.join, '<=|>=|=|<|>')[1]) AS right_side

    WITH t1, t2, rel, left_side, right_side,
         trim(split(left_side, '.')[0]) AS left_schema,
         trim(split(left_side, '.')[1]) AS left_table,
         trim(split(left_side, '.')[2]) AS left_column,
         trim(split(right_side, '.')[0]) AS right_schema,
         trim(split(right_side, '.')[1]) AS right_table,
         trim(split(right_side, '.')[2]) AS right_column
    WHERE left_schema IS NOT NULL AND left_table IS NOT NULL AND left_column IS NOT NULL
      AND right_schema IS NOT NULL AND right_table IS NOT NULL AND right_column IS NOT NULL

    OPTIONAL MATCH (s1:{Labels.SCHEMA} {{name: left_schema}})-[:{Edges.CONTAINS}]->
        (tbl1:{Labels.TABLE} {{name: left_table}})-[:{Edges.CONTAINS}]->
        (col1:{Labels.COLUMN} {{name: left_column}})

    OPTIONAL MATCH (s2:{Labels.SCHEMA} {{name: right_schema}})-[:{Edges.CONTAINS}]->
        (tbl2:{Labels.TABLE} {{name: right_table}})-[:{Edges.CONTAINS}]->
        (col2:{Labels.COLUMN} {{name: right_column}})

    RETURN collect(DISTINCT {{
        table1: coalesce(t1.schema_name, '') + '.' + t1.name,
        column1: coalesce(col1.name, left_column),
        column1_datatype: coalesce(col1.data_type, 'None'),
        table2: coalesce(t2.schema_name, '') + '.' + t2.name,
        column2: coalesce(col2.name, right_column),
        column2_datatype: coalesce(col2.data_type, 'None')
    }}) AS list_of_foreign_keys
    """
    try:
        results = get_neo4j_conn().query_read(query_joins, {"tables_ids": tables_ids})
        result_joins = results[0]["list_of_foreign_keys"] if results else []
    except Exception:
        logger.warning("get_relevant_fks: JOIN-edge query failed", exc_info=True)
        result_joins = []

    combined = [fk for fk in (result_fks or []) + (result_joins or []) if fk]
    unique_strings = set(json.dumps(d, sort_keys=True) for d in combined)
    unique_results = [json.loads(s) for s in unique_strings]

    key_order = [
        "table1",
        "column1",
        "column1_datatype",
        "table2",
        "column2",
        "column2_datatype",
    ]
    return [{key: d[key] for key in key_order} for d in unique_results]
