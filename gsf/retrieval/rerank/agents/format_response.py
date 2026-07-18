# SPDX-FileCopyrightText: Copyright (c) 2026, NVIDIA CORPORATION & AFFILIATES.
# All rights reserved.
# SPDX-License-Identifier: Apache-2.0

"""Final node of the rerank flow.

Returns only the identifiers of the (possibly reranked) result rows, in order,
as the response payload.
"""

from typing import Any, Dict, List, Optional

from gsf.retrieval.rerank.state import RerankState
from gsf.retrieval.text_to_sql.base import BaseAgent


def _extract_identifier(row: Dict[str, Any]) -> Optional[Any]:
    """Pull the item's identifier value from a result row.

    The SQL always selects the item's id first, but the column name varies
    (``id``, ``product_id`` ...). Prefer an exact ``id`` key, then any key that
    looks like an id, then fall back to the first column.
    """
    if not row:
        return None
    for key in row:
        if key.lower() == "id":
            return row[key]
    for key in row:
        if key.lower().endswith(("_id", "id")):
            return row[key]
    return next(iter(row.values()))


class FormatResponseAgent(BaseAgent):
    """Expose the ordered list of result identifiers as the final ``response``."""

    def __init__(self):
        super().__init__("format_response")

    def execute(self, state: RerankState) -> Dict[str, Any]:
        """Publish the ordered identifiers as ``path_state['response']``."""
        path_state = state.get("path_state", {})
        result: Dict[str, Any] = {"path_state": path_state}

        sql_results = path_state.get("sql_results") or []
        identifiers: List[Any] = []
        for row in sql_results:
            identifier = _extract_identifier(row)
            if identifier is not None:
                identifiers.append(identifier)

        path_state["response"] = identifiers
        self.logger.info("Formatted response with %d identifier(s)", len(identifiers))
        return result
