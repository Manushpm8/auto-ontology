# SPDX-FileCopyrightText: Copyright (c) 2026, NVIDIA CORPORATION & AFFILIATES.
# All rights reserved.
# SPDX-License-Identifier: Apache-2.0

"""KumoRFM prediction pipeline (GSF entry point).

Wires the ingested-catalog data into the ported text-to-PQL pipeline:
  1. Load a bounded sample of each ingested-catalog table into DataFrames.
  2. Build a KumoRFM ``LocalGraph`` (metadata + links inferred) and a DuckDB
     mirror of the same frames (so the entity-selection SQL resolves).
  3. Run :func:`gsf.retrieval.kumo.pql_gen.generate_pql` — LLM writes the PQL,
     the static lint + cheap parse validate it, an entity-selection SQL scopes
     the entities, and KumoRFM predicts, with the full repair loop.
  4. Format the result into the standard response dict.

Everything is bounded and defensive: any failure returns a graceful response
dict in the same shape as the SQL path, so the chat never hard-errors.
"""

from __future__ import annotations

import logging
import os
import threading
from typing import Any

import pandas as pd

logger = logging.getLogger(__name__)

# Bounds so building the graph on a large database stays tractable. The whole
# (capped) dataset is uploaded to the hosted KumoRFM service.
_MAX_TABLES = int(os.environ.get("KUMO_MAX_TABLES", "20"))
_MAX_ROWS_PER_TABLE = int(os.environ.get("KUMO_MAX_ROWS_PER_TABLE", "5000"))
_MAX_PREVIEW_ROWS = int(os.environ.get("KUMO_MAX_PREVIEW_ROWS", "50"))
_MAX_ENTITIES = int(os.environ.get("KUMO_MAX_ENTITIES", "2000"))

_init_lock = threading.Lock()
_initialized = False


def _ensure_init() -> None:
    """Authenticate the KumoRFM SDK once, from env vars."""
    global _initialized
    if _initialized:
        return
    with _init_lock:
        if _initialized:
            return
        api_key = os.environ.get("KUMO_RFM_API_KEY")
        if not api_key:
            raise RuntimeError("KUMO_RFM_API_KEY is not set")
        url = os.environ.get("KUMO_RFM_API_URL") or None

        import kumoai.rfm as rfm

        rfm.init(url=url, api_key=api_key)
        _initialized = True
        logger.info("KumoRFM initialized (url=%s)", url or "<default>")


def _quote(schema: str, table: str) -> str:
    """Schema-qualify a table name (double quotes work for Postgres/Snowflake)."""
    return f'"{schema}"."{table}"' if schema else f'"{table}"'


def _load_catalog_frames(connectors: list[Any]) -> dict[str, pd.DataFrame]:
    """Load a bounded sample of each ingested-catalog table into a DataFrame."""
    frames: dict[str, pd.DataFrame] = {}
    for connector in connectors:
        db = getattr(connector, "database_name", "?")
        try:
            tables = connector.get_tables()
        except Exception:
            logger.exception("kumo: get_tables failed for %s", db)
            continue
        if tables is None or tables.empty:
            continue
        for _, row in tables.iterrows():
            if len(frames) >= _MAX_TABLES:
                logger.warning(
                    "kumo: reached table cap (%d); remaining tables skipped",
                    _MAX_TABLES,
                )
                return frames
            schema = str(row.get("table_schema") or "").strip()
            table = str(row.get("table_name") or "").strip()
            if not table:
                continue
            name = table if table not in frames else f"{schema}_{table}"
            try:
                df = connector.execute(
                    f"SELECT * FROM {_quote(schema, table)} LIMIT {_MAX_ROWS_PER_TABLE}"
                )
            except Exception:
                logger.exception("kumo: failed to load rows for %s.%s", schema, table)
                continue
            if df is None or df.empty:
                continue
            frames[name] = df
    return frames


def _error_response(message: str) -> dict[str, Any]:
    return {
        "response": message,
        "sql_code": "",
        "sql_columns": [],
        "custom_analyses_used": [],
        "sql_response_from_db": None,
    }


def _format_result(result: Any) -> dict[str, Any]:
    """Shape a :class:`PqlGenerationResult` into the standard response dict."""
    if not result.success:
        message = result.error or "KumoRFM could not answer this prediction."
        final = _error_response(f"Prediction could not be completed. {message}")
        final["sql_code"] = result.pql or ""
        return final

    parts = [f"Prediction for: {result.question}"]
    if result.pql:
        parts.append(f"PQL: `{result.pql}`")
    if result.note:
        parts.append(result.note)
    parts.append(
        f"KumoRFM scored {result.num_entities} entit"
        f"{'y' if result.num_entities == 1 else 'ies'}."
        + (" (truncated preview)" if result.truncated else "")
    )
    return {
        "response": "\n\n".join(parts),
        "sql_code": result.pql or "",
        "sql_columns": result.columns,
        "custom_analyses_used": [],
        "sql_response_from_db": result.rows or None,
    }


def predict_from_question(
    question: str, connectors: list[Any], llm: Any
) -> dict[str, Any]:
    """Answer a prediction question via KumoRFM; returns a final_response dict."""
    _ensure_init()

    import kumoai.rfm as rfm

    from gsf.retrieval.kumo.kumo_model import KumoModel, build_graph_context
    from gsf.retrieval.kumo.pql_gen import generate_pql

    if not connectors:
        return _error_response("No database connection is configured.")

    frames = _load_catalog_frames(connectors)
    if not frames:
        return _error_response(
            "No catalog tables were available to build a prediction graph."
        )
    logger.info("kumo: building graph from %d table(s)", len(frames))

    graph = rfm.LocalGraph.from_data(frames, infer_metadata=True, verbose=False)
    try:
        graph.infer_links()
    except Exception:
        logger.exception("kumo: infer_links failed; proceeding without inferred links")

    graph_ddl, edges, col_stypes, time_columns = build_graph_context(graph)
    kumo_model = KumoModel(rfm.KumoRFM(graph, verbose=False))

    # Entity-selection SQL runs against the live GSF database connection (the
    # first configured connector — the source of the catalog tables).
    result = generate_pql(
        question,
        llm=llm,
        kumo_model=kumo_model,
        connector=connectors[0],
        graph_ddl=graph_ddl,
        graph_edges=edges,
        graph_col_stypes=col_stypes,
        time_columns=time_columns,
        max_entities=_MAX_ENTITIES,
        max_preview_rows=_MAX_PREVIEW_ROWS,
    )

    return _format_result(result)
