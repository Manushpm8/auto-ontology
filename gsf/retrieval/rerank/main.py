# SPDX-FileCopyrightText: Copyright (c) 2026, NVIDIA CORPORATION & AFFILIATES.
# All rights reserved.
# SPDX-License-Identifier: Apache-2.0

import logging

from langchain_core.messages import HumanMessage

from gsf.retrieval.rerank.rerank_graph import create_graph
from gsf.retrieval.rerank.state import RerankPayload, RerankState
from gsf.utils.llm_invoke import get_non_reasoning_llm_client

logger = logging.getLogger(__name__)

graph = create_graph()
app = graph.compile()

try:
    llm_client = get_non_reasoning_llm_client()
except (ValueError, EnvironmentError) as e:
    logger.error("Failed to initialize non-reasoning LLM client: %s", e)
    llm_client = None


def _build_state(payload: RerankPayload) -> RerankState:
    initial_path_state = dict(payload.get("path_state") or {})
    state: dict = {
        "llm": llm_client,
        "initial_question": payload["question"],
        "messages": [HumanMessage(content=payload["question"])],
        "path_state": initial_path_state,
        "decision": "",
    }
    return state


def _extract_answer(final_state: dict) -> dict:
    path_state = final_state.get("path_state", {})
    final_response = path_state.get("final_response", "")
    if isinstance(final_response, dict):
        return final_response
    return {"response": str(final_response)}


def get_agent_response(payload: RerankPayload) -> dict:
    """Run the rerank graph and return the final answer."""
    logger.info("Rerank agent started for question: %s", payload["question"])

    state = _build_state(payload)
    final_state = app.invoke(state, config={"recursion_limit": 45})

    answer = _extract_answer(final_state)
    logger.info("Final answer:\n%s", answer)
    return answer


__all__ = [
    "get_agent_response",
    "app",
    "graph",
]
