# SPDX-FileCopyrightText: Copyright (c) 2026, NVIDIA CORPORATION & AFFILIATES.
# All rights reserved.
# SPDX-License-Identifier: Apache-2.0

"""Column resolution node for the rerank flow.

Maps each extracted entity to a table + column (via semantic VDB vector search
plus Neo4j) and, for ``terms`` only, resolves the exact stored DB value with a
small, dialect-agnostic per-column SQL query. Uses no LLM.

Entities are resolved in parallel since each is an independent, I/O-bound
sequence of VDB query -> Neo4j read -> single-column SQL.
"""

import logging
from concurrent.futures import ThreadPoolExecutor, as_completed
from typing import Any, Dict, Optional

from nemo_retriever.tabular_data.sql_database import SQLDatabase

from gsf.dal.attributes import fetch_attr_column_contexts
from gsf.retrieval.data_access.semantic_search import search_semantic_index
from gsf.retrieval.rerank.state import RerankState
from gsf.retrieval.text_to_sql.base import BaseAgent
from gsf.semantic.constants import LABEL_COLUMN_ATTRIBUTE

logger = logging.getLogger(__name__)

# Candidate columns fetched from the semantic VDB per entity.
CANDIDATE_COLS_K = 3
# Rows read when probing a column for the entity's stored value.
VALUE_LIMIT = 20
# Upper bound on concurrent entity resolutions.
MAX_WORKERS = 5

# Buckets whose entities get an actual DB value resolved. Only "terms" (colors,
# brands, materials) map to concrete stored values; search_for and
# numeric_concepts are column-only.
_VALUE_BUCKETS = ("terms",)
_ALL_BUCKETS = ("search_for", "terms", "numeric_concepts")


def _quote_char(dialect: str | None) -> str:
    """Identifier quote character for *dialect* (backtick for Databricks)."""
    return "`" if (dialect or "").lower() == "databricks" else '"'


def _quote_ident(identifier: str, char: str) -> str:
    """Quote a SQL identifier, escaping embedded quote chars by doubling."""
    return char + identifier.replace(char, char * 2) + char


def _qualified_table(schema: str, table: str, char: str) -> str:
    """Schema-qualified, quoted table name (schema optional)."""
    parts = [p for p in (schema, table) if p]
    return ".".join(_quote_ident(p, char) for p in parts)


def _empty_mapping(entity: str) -> Dict[str, Any]:
    return {
        "entity": entity,
        "database": None,
        "schema": None,
        "table": None,
        "table_id": None,
        "column": None,
        "col_id": None,
        "value": None,
    }


def _mapping_from_ctx(
    entity: str, ctx: Dict[str, Any], value: Optional[str]
) -> Dict[str, Any]:
    return {
        "entity": entity,
        "database": ctx.get("database_name") or None,
        "schema": ctx.get("schema_name") or None,
        "table": ctx.get("table_name") or None,
        "table_id": ctx.get("table_id"),
        "column": ctx.get("col_name") or None,
        "col_id": ctx.get("col_id"),
        "value": value,
    }


def _find_value(
    connector: SQLDatabase,
    ctx: Dict[str, Any],
    entity: str,
    char: str,
) -> Optional[str]:
    """Probe a single column for the entity's stored value (dialect-agnostic).

    Runs ``SELECT DISTINCT <col> ... WHERE LOWER(<col>) LIKE LOWER('%term%')``
    and returns the best match (exact case-insensitive if present, else first),
    or ``None`` when nothing matches.
    """
    column = ctx.get("col_name")
    table = ctx.get("table_name")
    if not column or not table:
        return None

    col = _quote_ident(column, char)
    qualified = _qualified_table(ctx.get("schema_name") or "", table, char)
    # Escape single quotes for the string literal; % / _ are left as-is so a
    # stray wildcard just widens the (already fuzzy) contains match.
    escaped = entity.replace("'", "''")
    sql = (
        f"SELECT DISTINCT {col} AS v FROM {qualified} "
        f"WHERE LOWER({col}) LIKE LOWER('%{escaped}%') "
        f"LIMIT {VALUE_LIMIT}"
    )

    df = connector.execute(sql)
    if df is None or df.empty:
        return None

    values = [v for v in df.iloc[:, 0].tolist() if v is not None]
    if not values:
        return None

    target = entity.strip().lower()
    for v in values:
        if str(v).strip().lower() == target:
            return str(v)
    return str(values[0])


def _resolve_entity(
    retriever: object,
    connector: Optional[SQLDatabase],
    char: str,
    bucket: str,
    entity: str,
) -> Dict[str, Any]:
    """Resolve one entity to its table/column (+ value for value-buckets)."""
    hits = search_semantic_index(
        retriever,
        entity,
        label_filter=[LABEL_COLUMN_ATTRIBUTE],
        per_label_k=CANDIDATE_COLS_K,
    )
    if not hits:
        return _empty_mapping(entity)

    ctxs = fetch_attr_column_contexts([h["id"] for h in hits if h.get("id")])
    # Carry database_name from the VDB hit metadata onto the Neo4j context
    # (fetch_attr_column_contexts doesn't return the database).
    candidates = []
    for h in hits:
        ctx = ctxs.get(h.get("id"))
        if ctx and ctx.get("col_name"):
            candidates.append({**ctx, "database_name": h.get("database_name")})
    if not candidates:
        return _empty_mapping(entity)

    if bucket in _VALUE_BUCKETS and connector is not None:
        for ctx in candidates:
            try:
                value = _find_value(connector, ctx, entity, char)
            except Exception:
                logger.warning(
                    "Value probe failed for entity %r on %s.%s",
                    entity,
                    ctx.get("table_name"),
                    ctx.get("col_name"),
                    exc_info=True,
                )
                continue
            if value is not None:
                return _mapping_from_ctx(entity, ctx, value)
        # No value matched any candidate column -> map to the best column only.
        return _mapping_from_ctx(entity, candidates[0], None)

    # numeric_concepts (or no connector): column-only mapping.
    return _mapping_from_ctx(entity, candidates[0], None)


def _store_results(path_state: dict, mappings: Dict[str, list]) -> None:
    """Persist entity mappings plus deduped resolved tables/columns.

    ``resolved_columns`` / ``resolved_tables`` (each carrying database + schema)
    are consolidated views for downstream nodes.
    """
    path_state["entity_mappings"] = mappings

    columns: dict[tuple, dict] = {}
    tables: dict[tuple, dict] = {}
    for bucket_maps in mappings.values():
        for m in bucket_maps:
            if not (m.get("table") and m.get("column")):
                continue
            col_key = (
                m.get("database"),
                m.get("schema"),
                m.get("table"),
                m.get("column"),
            )
            if col_key not in columns:
                columns[col_key] = {
                    "database": m.get("database"),
                    "schema": m.get("schema"),
                    "table": m.get("table"),
                    "table_id": m.get("table_id"),
                    "column": m.get("column"),
                    "col_id": m.get("col_id"),
                }
            tbl_key = (m.get("database"), m.get("schema"), m.get("table"))
            if tbl_key not in tables:
                tables[tbl_key] = {
                    "database": m.get("database"),
                    "schema": m.get("schema"),
                    "table": m.get("table"),
                    "table_id": m.get("table_id"),
                    "columns": [],
                }
            cols = tables[tbl_key]["columns"]
            if m.get("column") not in cols:
                cols.append(m.get("column"))

    path_state["resolved_columns"] = list(columns.values())
    path_state["resolved_tables"] = list(tables.values())


class ColumnResolutionAgent(BaseAgent):
    """Resolve extracted entities to table/column and (where relevant) value."""

    def __init__(self):
        super().__init__("column_resolution")

    def execute(self, state: RerankState) -> Dict[str, Any]:
        """Map every entity to its table/column and stored value."""
        path_state = state.get("path_state", {})
        result: Dict[str, Any] = {"path_state": path_state}

        entities = path_state.get("entities") or {}
        mappings: Dict[str, list] = {bucket: [] for bucket in _ALL_BUCKETS}

        retriever = state.get("semantic_retriever")
        if retriever is None:
            self.logger.warning(
                "No semantic retriever available, skipping column resolution"
            )
            _store_results(path_state, mappings)
            return result

        connectors = state.get("connectors") or []
        connector = connectors[0] if connectors else None
        char = _quote_char(getattr(connector, "dialect", None))

        tasks = [
            (bucket, entity)
            for bucket in _ALL_BUCKETS
            for entity in (entities.get(bucket) or [])
        ]
        if not tasks:
            _store_results(path_state, mappings)
            return result

        with ThreadPoolExecutor(max_workers=min(MAX_WORKERS, len(tasks))) as executor:
            future_map = {
                executor.submit(
                    _resolve_entity, retriever, connector, char, bucket, entity
                ): (bucket, entity)
                for bucket, entity in tasks
            }
            for future in as_completed(future_map):
                bucket, entity = future_map[future]
                try:
                    mapping = future.result()
                except Exception:
                    self.logger.warning(
                        "Resolution failed for entity %r", entity, exc_info=True
                    )
                    mapping = _empty_mapping(entity)
                mappings[bucket].append(mapping)

        _store_results(path_state, mappings)
        self.logger.info("Entity mappings: %s", mappings)
        return result
