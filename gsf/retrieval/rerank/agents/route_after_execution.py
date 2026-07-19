# SPDX-FileCopyrightText: Copyright (c) 2026, NVIDIA CORPORATION & AFFILIATES.
# All rights reserved.
# SPDX-License-Identifier: Apache-2.0

"""Routing node that runs after ``sql_execution`` in the rerank flow.

Decides where the graph goes next based on the execution outcome and the
remaining filter entities:

- If there are result rows → send them to ``rerank_sql_results`` for relevance
  ordering (``decision = "rerank"``).
- If the query errored or returned no rows AND there are still ``terms`` filters
  to relax AND relaxation has not been attempted yet → route to
  ``sql_relaxation`` to strip those filters and re-execute
  (``decision = "retry"``).
- Otherwise (nothing left to relax, or relaxation already tried) → go straight
  to the final ``format_response`` node (``decision = "format"``).
"""

from typing import Any, Dict

from gsf.retrieval.rerank.state import RerankState
from gsf.retrieval.text_to_sql.base import BaseAgent

# Buckets whose predicates are stripped by ``sql_relaxation`` when a query comes
# back empty/errored so a broader query can be re-executed. Only ``terms`` filter
# (search_for_details are ranking-only, so relaxing them would not change rows).
_RELAXABLE_BUCKETS = ("terms",)


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
        """Compute the routing decision from the execution outcome."""
        path_state = state.get("path_state", {})
        result: Dict[str, Any] = {"path_state": path_state}

        sql_error = path_state.get("sql_error")
        sql_results = path_state.get("sql_results") or []
        already_relaxed = bool(path_state.get("sql_relaxed"))

        if sql_results:
            self.logger.info(
                "SQL returned %d row(s); routing to rerank_sql_results",
                len(sql_results),
            )
            result["decision"] = "rerank"
            return result

        # No rows (empty result or error). Relax filters once, if any remain.
        if not already_relaxed and _has_relaxable_filters(path_state):
            self.logger.info(
                "Empty/errored result (error=%s); routing to sql_relaxation to "
                "strip %s predicates",
                bool(sql_error),
                ", ".join(_RELAXABLE_BUCKETS),
            )
            result["decision"] = "retry"
            return result

        self.logger.info(
            "Empty result (already_relaxed=%s); routing to format_response",
            already_relaxed,
        )
        result["decision"] = "format"
        return result
