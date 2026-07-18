# SPDX-FileCopyrightText: Copyright (c) 2026, NVIDIA CORPORATION & AFFILIATES.
# All rights reserved.
# SPDX-License-Identifier: Apache-2.0

"""SQL execution node for the rerank flow.

Executes the SQL produced by ``sql_generation`` against the resolved connector
and stores the result rows on the state. It does nothing else — no reranking,
no reformatting.
"""

import json
from typing import Any, Dict

from gsf.retrieval.rerank.state import RerankState
from gsf.retrieval.text_to_sql.base import BaseAgent
from gsf.retrieval.text_to_sql.connector_routing import resolve_connector_from_tables


class SqlExecutionAgent(BaseAgent):
    """Execute the generated SQL and store its result rows on the state."""

    def __init__(self):
        super().__init__("sql_execution")

    def validate_input(self, state: RerankState) -> bool:
        """Validate that generated SQL is available."""
        path_state = state.get("path_state", {})
        if not str(path_state.get("sql") or "").strip():
            self.logger.warning("No SQL found in state, skipping SQL execution")
            return False
        return True

    def execute(self, state: RerankState) -> Dict[str, Any]:
        """Run ``path_state['sql']`` and store the rows (or an error) on state."""
        path_state = state.get("path_state", {})
        result: Dict[str, Any] = {"path_state": path_state}

        sql = str(path_state.get("sql") or "").strip()
        connectors = state.get("connectors") or []
        resolved_tables = path_state.get("resolved_tables", []) or []
        relative_tables = path_state.get("relative_tables", []) or []
        connector = resolve_connector_from_tables(
            resolved_tables + relative_tables, connectors
        )

        if connector is None:
            self.logger.warning("No connector available to execute SQL")
            path_state["sql_error"] = "No connector available to execute SQL."
            path_state["sql_results"] = []
            return result

        try:
            df = connector.execute(sql)
        except Exception as e:
            self.logger.exception("SQL execution failed")
            path_state["sql_error"] = str(e)
            path_state["sql_results"] = []
            return result

        payload = (
            df.to_json(orient="records", date_format="iso", default_handler=str)
            if df is not None and len(df)
            else "[]"
        )
        records = json.loads(payload)
        path_state["sql_results"] = records
        path_state["sql_row_count"] = len(records)
        path_state["sql_error"] = None
        self.logger.info("SQL execution returned %d row(s)", len(records))
        return result
