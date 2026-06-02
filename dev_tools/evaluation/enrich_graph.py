# SPDX-FileCopyrightText: Copyright (c) 2026, NVIDIA CORPORATION & AFFILIATES.
# All rights reserved.
# SPDX-License-Identifier: Apache-2.0

"""GSF dev-tools helpers for custom analyses ingestion."""

from __future__ import annotations

import json
import logging
import time
from pathlib import Path
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from nemo_retriever.params import EmbedParams
    from nemo_retriever.vdb import VDB

logger = logging.getLogger(__name__)

DEFAULT_DIR = Path(__file__).resolve().parent


def add_custom_analyses(
    database_name: str,
    dialect: str,
    embed_params: "EmbedParams | None" = None,
    vdb: "VDB | None" = None,
) -> None:
    """Ingest custom analyses for *database_name* into the Neo4j graph and the VDB.

    Reads ``<this dir>/<database_name>_custom_analyses.json`` — a list of
    ``{"name", "description", "sql"}`` entries — and, for each entry:

    * parses the SQL against the schemas already in the graph (via
      :func:`parse_query_single`), which produces a :class:`Sql` node and the
      corresponding ``Sql -> Table/Column`` edges;
    * creates a :class:`CustomAnalysis` node with ``name`` and ``description``;
    * connects ``CustomAnalysis -[:HAS_SQL]-> Sql``.

    When *embed_params* and *vdb* are provided, the function then embeds
    each newly-ingested analysis (name + description + SQL) and **appends**
    the rows to the supplied vector store — so they live alongside the rows
    the main embed pipeline writes for ``Table`` and ``Column`` nodes. The
    append semantics mean the main pipeline must run *before* this function.

    Entries with no SQL, or whose SQL doesn't resolve to any known table, are
    skipped with a warning. Must be called *after* schema ingestion so the
    parser can resolve table/column references.
    """
    from nemo_retriever.tabular_data.ingestion.dal.queries_dal import add_query
    from nemo_retriever.tabular_data.ingestion.model.neo4j_node import Neo4jNode
    from nemo_retriever.tabular_data.ingestion.model.reserved_words import Labels, Props
    from nemo_retriever.tabular_data.ingestion.services.queries import (
        parse_query_single,
    )
    from nemo_retriever.tabular_data.retrieval.data_access.graph_schemas import (
        get_all_schemas_ids,
        get_schemas_by_ids,
    )

    from gsf.server.custom_analyses.dal import _embed_custom_analyses

    analyses_path = DEFAULT_DIR / f"{database_name}_custom_analyses.json"

    if not analyses_path.exists():
        logger.warning("custom analyses file not found at %s; skipping", analyses_path)
        return

    with analyses_path.open() as f:
        analyses = json.load(f)

    if not isinstance(analyses, list) or not analyses:
        logger.info("No custom analyses to ingest from %s.", analyses_path)
        return

    schemas_ids = get_all_schemas_ids()
    schemas = get_schemas_by_ids(schemas_ids)

    before = time.time()
    logger.info(
        "Starting to ingest %d custom analyses from %s.", len(analyses), analyses_path
    )

    ingested = 0
    for entry in analyses:
        name = entry.get("name", "")
        sql = (entry.get("sql") or "").strip()
        if not sql:
            logger.warning("Skipping custom analysis %r — no SQL provided.", name)
            continue

        query_obj = parse_query_single(sql=sql, dialect=dialect, schemas=schemas)
        if query_obj is None:
            logger.warning(
                "Could not resolve any tables for custom analysis %r — skipping.",
                name,
            )
            continue

        query_obj.sql_node.match_props = {"sql_full_query": sql}

        analysis_node = Neo4jNode(
            name=name,
            label=Labels.CUSTOM_ANALYSIS,
            props={
                "name": name,
                "description": entry.get("description", ""),
            },
            match_props={"name": name},
        )

        edge_props = {Props.ANALYSIS_ID: analysis_node.get_id()}
        query_obj.edges.append((analysis_node, query_obj.sql_node, edge_props))

        add_query(query_obj.get_edges())
        ingested += 1

    logger.info(
        "Ingested %d/%d custom analyses in %.2fs.",
        ingested,
        len(analyses),
        time.time() - before,
    )

    if ingested == 0:
        return

    if embed_params is None or vdb is None:
        logger.info(
            "Skipping custom-analysis embedding: embed_params/vdb not provided."
        )
        return

    _embed_custom_analyses(embed_params, vdb)
