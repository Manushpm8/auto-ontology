# SPDX-FileCopyrightText: Copyright (c) 2026, NVIDIA CORPORATION & AFFILIATES.
# All rights reserved.
# SPDX-License-Identifier: Apache-2.0

"""SQL relaxation node for the rerank flow.

When a generated query comes back empty, this node uses the (non-reasoning) LLM
to remove ONLY the ``terms`` predicates from the existing SQL — keeping the core
``search_for`` match, joins, GROUP BY, and the ORDER BY details ranking — and
hands the broadened query straight to ``sql_execution``. (``search_for_details``
are not filters; they only rank, so there is nothing to relax there.) It runs at
most once per request (guarded by ``path_state['sql_relaxed']``).
"""

from typing import Any, Dict

from langchain_core.messages import SystemMessage

from gsf.retrieval.rerank.agents.sql_generation import (
    SqlGenerationModel,
    _build_term_filters,
)
from gsf.retrieval.rerank.prompts import create_sql_relaxation_prompt
from gsf.retrieval.rerank.state import RerankState
from gsf.retrieval.text_to_sql.base import BaseAgent
from gsf.retrieval.text_to_sql.connector_routing import resolve_connector_from_tables
from gsf.utils.llm_invoke import invoke_with_structured_output


class SqlRelaxationAgent(BaseAgent):
    """Strip the ``terms`` filters from the current SQL."""

    def __init__(self):
        super().__init__("sql_relaxation")

    def validate_input(self, state: RerankState) -> bool:
        """Only run when there is SQL to relax."""
        path_state = state.get("path_state", {})
        if not str(path_state.get("sql") or "").strip():
            self.logger.warning("No SQL found in state, skipping SQL relaxation")
            return False
        return True

    def execute(self, state: RerankState) -> Dict[str, Any]:
        """Rewrite ``path_state['sql']`` without the relaxable filters."""
        path_state = state.get("path_state", {})
        result: Dict[str, Any] = {"path_state": path_state}

        # Mark relaxation as attempted so the router never loops back here again,
        # even if the rewrite below fails.
        path_state["sql_relaxed"] = True

        llm = state["llm"]
        sql = str(path_state.get("sql") or "").strip()
        entity_mappings = path_state.get("entity_mappings", {}) or {}
        resolved_tables = path_state.get("resolved_tables", []) or []
        relative_tables = path_state.get("relative_tables", []) or []

        connectors = state.get("connectors") or []
        connector = resolve_connector_from_tables(
            resolved_tables + relative_tables, connectors
        )
        dialect = getattr(connector, "dialect", None)

        term_filters = _build_term_filters(entity_mappings.get("terms") or [])

        prompt = create_sql_relaxation_prompt(
            dialect=dialect,
            sql=sql,
            term_filters=term_filters,
        )

        response = invoke_with_structured_output(
            llm,
            [SystemMessage(content=prompt)],
            SqlGenerationModel,
        )

        if response is None or not (response.sql or "").strip():
            self.logger.warning("SQL relaxation returned no SQL, keeping current SQL")
            return result

        relaxed_sql = response.sql.strip()
        path_state["sql"] = relaxed_sql
        self.logger.info("Relaxed SQL:\n%s", relaxed_sql)
        return result
