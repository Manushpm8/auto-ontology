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

from gsf.dal.attributes import (
    fetch_attr_column_contexts,
    fetch_semantic_fk_related_tables,
)
from gsf.dal.datasources import fetch_tables_by_ids
from gsf.retrieval.data_access.relevant_tables import dedupe_merge_relevant_tables
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
_ALL_BUCKETS = ("search_for", "search_for_details", "terms", "numeric_concepts")


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
    }


def _mapping_from_ctx(entity: str, ctx: Dict[str, Any]) -> Dict[str, Any]:
    return {
        "entity": entity,
        "database": ctx.get("database_name") or None,
        "schema": ctx.get("schema_name") or None,
        "table": ctx.get("table_name") or None,
        "table_id": ctx.get("table_id"),
        "column": ctx.get("col_name") or None,
        "col_id": ctx.get("col_id"),
    }


def _with_value(
    mapping: Dict[str, Any], bucket: str, value: Optional[str]
) -> Dict[str, Any]:
    """Attach a ``value`` only for value-buckets (terms); others are column-only."""
    if bucket in _VALUE_BUCKETS:
        mapping["value"] = value
    return mapping


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


def _attr_record(ctx: Dict[str, Any]) -> Dict[str, Any]:
    """A ColumnAttribute hit enriched with its Neo4j column/table context."""
    return {
        "attr_id": ctx.get("attr_id"),
        "attr_name": ctx.get("attr_name") or None,
        "attr_description": ctx.get("attr_description") or None,
        "score": ctx.get("score"),
        "database": ctx.get("database_name") or None,
        "schema": ctx.get("schema_name") or None,
        "table": ctx.get("table_name") or None,
        "table_id": ctx.get("table_id"),
        "column": ctx.get("col_name") or None,
        "col_id": ctx.get("col_id"),
    }


def _resolve_entity(
    retriever: object,
    connector: Optional[SQLDatabase],
    char: str,
    bucket: str,
    entity: str,
) -> Dict[str, Any]:
    """Resolve one entity to its table/column (+ value for value-buckets).

    Returns ``{"mapping": <mapping>, "column_attributes": [<attr records>]}``.
    """
    hits = search_semantic_index(
        retriever,
        entity,
        label_filter=[LABEL_COLUMN_ATTRIBUTE],
        per_label_k=CANDIDATE_COLS_K,
    )
    if not hits:
        return {
            "mapping": _with_value(_empty_mapping(entity), bucket, None),
            "column_attributes": [],
        }

    ctxs = fetch_attr_column_contexts([h["id"] for h in hits if h.get("id")])
    # Carry database_name (and the hit's id/score) from the VDB hit metadata onto
    # the Neo4j context (fetch_attr_column_contexts doesn't return the database).
    candidates = []
    for h in hits:
        ctx = ctxs.get(h.get("id"))
        if ctx and ctx.get("col_name"):
            candidates.append(
                {
                    **ctx,
                    "attr_id": h.get("id"),
                    "score": h.get("score"),
                    "database_name": h.get("database_name"),
                }
            )

    column_attributes = [_attr_record(c) for c in candidates]
    if not candidates:
        return {
            "mapping": _with_value(_empty_mapping(entity), bucket, None),
            "column_attributes": [],
        }

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
                return {
                    "mapping": _with_value(
                        _mapping_from_ctx(entity, ctx), bucket, value
                    ),
                    "column_attributes": column_attributes,
                }
        # No value matched any candidate column -> map to the best column only.
        return {
            "mapping": _with_value(
                _mapping_from_ctx(entity, candidates[0]), bucket, None
            ),
            "column_attributes": column_attributes,
        }

    # search_for / numeric_concepts: column-only mapping (no value key).
    return {
        "mapping": _mapping_from_ctx(entity, candidates[0]),
        "column_attributes": column_attributes,
    }


def _dedupe_attrs(attrs: list) -> list:
    """Deduplicate ColumnAttribute records, keeping the best (lowest) score."""
    best: dict = {}
    for a in attrs:
        key = a.get("attr_id") or (a.get("table"), a.get("column"), a.get("attr_name"))
        cur = best.get(key)
        if cur is None or (
            a.get("score") is not None
            and (cur.get("score") is None or a["score"] < cur["score"])
        ):
            best[key] = a
    return list(best.values())


def _store_results(
    path_state: dict,
    mappings: Dict[str, list],
    column_attributes: list | None = None,
) -> None:
    """Persist entity mappings plus resolved columns and full tables.

    ``resolved_columns`` are the specific columns entities mapped to. For each
    resolved table we fetch the full table from Neo4j (all columns with their
    descriptions) via :func:`fetch_tables_by_ids`, mirroring how
    ``candidate_preparation`` gathers relevant tables. ``column_attributes`` are
    the raw semantic-VDB hits we retrieved.
    """
    path_state["entity_mappings"] = mappings
    path_state["column_attributes"] = _dedupe_attrs(column_attributes or [])

    columns: dict[tuple, dict] = {}
    table_ids: list[str] = []
    db_by_table_id: dict[str, Any] = {}
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
            tid = m.get("table_id")
            if tid and tid not in db_by_table_id:
                db_by_table_id[tid] = m.get("database")
                table_ids.append(tid)

    path_state["resolved_columns"] = list(columns.values())

    # Fetch each resolved table with its full column list + descriptions from
    # Neo4j (no LLM filter). fetch_tables_by_ids omits the database name, so
    # carry it over from the entity mappings before deduping/merging.
    resolved_tables = fetch_tables_by_ids(table_ids)
    for t in resolved_tables:
        t["database_name"] = db_by_table_id.get(t.get("id"))
    path_state["resolved_tables"] = dedupe_merge_relevant_tables(resolved_tables)

    # Relative tables: tables linked to a resolved table's column via a
    # SEMANTIC_FK edge. Fetch their columns the same way as resolved tables and
    # carry the join column pair(s) that connect them to the resolved tables.
    related = fetch_semantic_fk_related_tables(table_ids)
    related_by_id = {r["id"]: r for r in related}
    relative_tables = fetch_tables_by_ids(list(related_by_id))
    for t in relative_tables:
        rel = related_by_id.get(t.get("id"), {})
        t["database_name"] = rel.get("database_name")
    relative_tables = dedupe_merge_relevant_tables(relative_tables)
    for t in relative_tables:
        t["join_paths"] = related_by_id.get(t.get("id"), {}).get("join_paths", [])
    path_state["relative_tables"] = relative_tables


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

        column_attributes: list = []
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
                    res = future.result()
                    mapping = res["mapping"]
                    column_attributes.extend(res["column_attributes"])
                except Exception:
                    self.logger.warning(
                        "Resolution failed for entity %r", entity, exc_info=True
                    )
                    mapping = _with_value(_empty_mapping(entity), bucket, None)
                mappings[bucket].append(mapping)

        _store_results(path_state, mappings, column_attributes)
        self.logger.info("Entity mappings: %s", mappings)
        return result
