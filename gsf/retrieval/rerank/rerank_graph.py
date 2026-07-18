# SPDX-FileCopyrightText: Copyright (c) 2026, NVIDIA CORPORATION & AFFILIATES.
# All rights reserved.
# SPDX-License-Identifier: Apache-2.0

import logging

from langchain_core.runnables import RunnableLambda
from langgraph.graph import END, StateGraph

from gsf.retrieval.rerank.agents.column_resolution import ColumnResolutionAgent
from gsf.retrieval.rerank.agents.question_extraction import QuestionExtractionAgent
from gsf.retrieval.rerank.agents.sql_execution import SqlExecutionAgent
from gsf.retrieval.rerank.agents.sql_generation import SqlGenerationAgent
from gsf.retrieval.rerank.state import RerankState
from gsf.retrieval.text_to_sql.base import agent_wrapper

logger = logging.getLogger(__name__)


def create_graph():
    """Build the rerank graph.

    Flow: ``question_extraction`` -> ``column_resolution`` -> ``sql_generation``
    -> ``sql_execution`` -> END. More nodes will be added step by step.
    """
    question_extraction_agent = QuestionExtractionAgent()
    column_resolution_agent = ColumnResolutionAgent()
    sql_generation_agent = SqlGenerationAgent()
    sql_execution_agent = SqlExecutionAgent()

    graph = StateGraph(RerankState)

    graph.add_node(
        "question_extraction",
        RunnableLambda(agent_wrapper(question_extraction_agent)),
    )
    graph.add_node(
        "column_resolution",
        RunnableLambda(agent_wrapper(column_resolution_agent)),
    )
    graph.add_node(
        "sql_generation",
        RunnableLambda(agent_wrapper(sql_generation_agent)),
    )
    graph.add_node(
        "sql_execution",
        RunnableLambda(agent_wrapper(sql_execution_agent)),
    )

    graph.set_entry_point("question_extraction")
    graph.add_edge("question_extraction", "column_resolution")
    graph.add_edge("column_resolution", "sql_generation")
    graph.add_edge("sql_generation", "sql_execution")
    graph.add_edge("sql_execution", END)

    return graph


__all__ = [
    "RerankState",
    "create_graph",
]
