# SPDX-FileCopyrightText: Copyright (c) 2026, NVIDIA CORPORATION & AFFILIATES.
# All rights reserved.
# SPDX-License-Identifier: Apache-2.0

import logging

from langchain_core.runnables import RunnableLambda
from langgraph.graph import END, StateGraph

from gsf.retrieval.rerank.agents.question_extraction import QuestionExtractionAgent
from gsf.retrieval.rerank.state import RerankState
from gsf.retrieval.text_to_sql.base import agent_wrapper

logger = logging.getLogger(__name__)


def create_graph():
    """Build the rerank graph.

    Currently a single ``question_extraction`` node. More nodes will be added
    step by step.
    """
    question_extraction_agent = QuestionExtractionAgent()

    graph = StateGraph(RerankState)

    graph.add_node(
        "question_extraction",
        RunnableLambda(agent_wrapper(question_extraction_agent)),
    )

    graph.set_entry_point("question_extraction")
    graph.add_edge("question_extraction", END)

    return graph


__all__ = [
    "RerankState",
    "create_graph",
]
