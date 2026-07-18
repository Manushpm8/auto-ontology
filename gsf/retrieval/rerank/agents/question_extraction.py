# SPDX-FileCopyrightText: Copyright (c) 2026, NVIDIA CORPORATION & AFFILIATES.
# All rights reserved.
# SPDX-License-Identifier: Apache-2.0

"""Question extraction node for the rerank flow.

Placeholder implementation — returns ``"hello world"`` for now. Real extraction
logic will be added later.
"""

from typing import Any, Dict

from gsf.retrieval.text_to_sql.base import BaseAgent
from gsf.retrieval.rerank.state import RerankState


class QuestionExtractionAgent(BaseAgent):
    """Extract the question to rerank against (placeholder)."""

    def __init__(self):
        super().__init__("question_extraction")

    def execute(self, state: RerankState) -> Dict[str, Any]:
        """Return a placeholder ``hello world`` response."""
        path_state = state.get("path_state", {})
        path_state["final_response"] = "hello world"
        self.logger.info("question_extraction: hello world")
        return {"path_state": path_state}
