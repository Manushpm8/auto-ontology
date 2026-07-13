# SPDX-FileCopyrightText: Copyright (c) 2026, NVIDIA CORPORATION & AFFILIATES.
# All rights reserved.
# SPDX-License-Identifier: Apache-2.0

"""
KumoRFM prediction agent (terminal node).

Handles questions the decision tree flagged as predictions. Delegates to the
KumoRFM pipeline (build graph from the ingested catalog → LLM writes PQL →
predict) and stores the result in ``path_state["final_response"]`` in the same
shape the SQL response uses, so the frontend renders it with no special-casing.
"""

import logging
from typing import Any, Dict

from langchain_core.messages import AIMessage

from gsf.retrieval.kumo import predict_from_question
from gsf.retrieval.text_to_sql.base import BaseAgent
from gsf.retrieval.text_to_sql.state import AgentState, get_original_question

logger = logging.getLogger(__name__)


class KumoPredictionAgent(BaseAgent):
    """Answer a prediction question with KumoRFM."""

    def __init__(self):
        super().__init__("kumo_prediction")

    def execute(self, state: AgentState) -> Dict[str, Any]:
        path_state = state.get("path_state", {})
        # Predict from the raw question (per requirement).
        question = get_original_question(state)
        connectors = state.get("connectors", []) or []

        try:
            final_response = predict_from_question(question, connectors, state["llm"])
        except Exception as exc:
            self.logger.exception("KumoRFM prediction failed")
            final_response = {
                "response": (
                    "I couldn't produce a prediction for this question. "
                    f"({type(exc).__name__}: {exc})"
                ),
                "sql_code": "",
                "sql_columns": [],
                "custom_analyses_used": [],
                "sql_response_from_db": None,
            }

        markdown = final_response.get("response", "")
        return {
            "messages": state["messages"] + [AIMessage(content=markdown)],
            "path_state": {
                **path_state,
                "formatted_response": markdown,
                "final_response": final_response,
            },
        }
