# SPDX-FileCopyrightText: Copyright (c) 2026, NVIDIA CORPORATION & AFFILIATES.
# All rights reserved.
# SPDX-License-Identifier: Apache-2.0

"""Final node of the rerank flow.

Returns the (possibly reranked) SQL result rows as the response payload.
"""

from typing import Any, Dict

from gsf.retrieval.rerank.state import RerankState
from gsf.retrieval.text_to_sql.base import BaseAgent


class FormatResponseAgent(BaseAgent):
    """Expose ``sql_results`` as the final ``response``."""

    def __init__(self):
        super().__init__("format_response")

    def execute(self, state: RerankState) -> Dict[str, Any]:
        """Publish ``sql_results`` as ``path_state['response']``."""
        path_state = state.get("path_state", {})
        result: Dict[str, Any] = {"path_state": path_state}

        sql_results = path_state.get("sql_results") or []
        path_state["response"] = sql_results
        self.logger.info("Formatted response with %d row(s)", len(sql_results))
        return result
