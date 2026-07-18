# SPDX-FileCopyrightText: Copyright (c) 2026, NVIDIA CORPORATION & AFFILIATES.
# All rights reserved.
# SPDX-License-Identifier: Apache-2.0

"""Routing node that runs after ``sql_execution`` in the rerank flow.

Decides where the graph goes next based on the execution outcome and the
remaining filter entities:

- If the query errored or returned no rows AND there are still ``terms`` or
  ``search_for_details`` to relax → clear those buckets and re-route back to
  ``sql_generation`` for a broader query (``decision = "retry"``).
- If there is nothing left to relax and no rows → go straight to the final
  ``format_response`` node (``decision = "format"``).
- If there are result rows → send them to ``rerank_sql_results`` for relevance
  ordering (``decision = "rerank"``).
"""

from typing import Any, Dict

from gsf.retrieval.rerank.state import RerankState
from gsf.retrieval.text_to_sql.base import BaseAgent

# Buckets that are relaxed (cleared) when a query comes back empty/errored so the
# next SQL generation pass can widen the search.
_RELAXABLE_BUCKETS = ("terms", "search_for_details")


def _has_relaxable_filters(path_state: Dict[str, Any]) -> bool:
    """True if any ``terms`` / ``search_for_details`` filters are still present."""
    entity_mappings = path_state.get("entity_mappings", {}) or {}
    entities = path_state.get("entities", {}) or {}
    for bucket in _RELAXABLE_BUCKETS:
        if entity_mappings.get(bucket) or entities.get(bucket):
            return True
    return False


class RouteAfterExecutionAgent(BaseAgent):
    """Set the post-execution routing ``decision`` on the state."""

    def __init__(self):
        super().__init__("route_after_execution")

    def execute(self, state: RerankState) -> Dict[str, Any]:
        """Compute the routing decision and relax filters when retrying."""
        path_state = state.get("path_state", {})
        result: Dict[str, Any] = {"path_state": path_state}

        sql_error = path_state.get("sql_error")
        sql_results = path_state.get("sql_results") or []

        if sql_results:
            self.logger.info(
                "SQL returned %d row(s); routing to rerank_sql_results",
                len(sql_results),
            )
            result["decision"] = "rerank"
            return result

        # No rows (empty result or error).
        if _has_relaxable_filters(path_state):
            self.logger.info(
                "Empty/errored result (error=%s); clearing %s and retrying "
                "sql_generation",
                bool(sql_error),
                ", ".join(_RELAXABLE_BUCKETS),
            )
            self._relax_filters(path_state)
            result["decision"] = "retry"
            return result

        self.logger.info(
            "Empty result with no filters left to relax; routing to format_response"
        )
        result["decision"] = "format"
        return result

    def _relax_filters(self, path_state: Dict[str, Any]) -> None:
        """Clear ``terms`` / ``search_for_details`` from mappings and entities."""
        entity_mappings = path_state.get("entity_mappings", {}) or {}
        entities = path_state.get("entities", {}) or {}
        for bucket in _RELAXABLE_BUCKETS:
            if bucket in entity_mappings:
                entity_mappings[bucket] = []
            if bucket in entities:
                entities[bucket] = []
        path_state["entity_mappings"] = entity_mappings
        path_state["entities"] = entities
        # Force a fresh generation/execution pass.
        path_state["sql"] = ""
        path_state["sql_error"] = None
