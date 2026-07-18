# SPDX-FileCopyrightText: Copyright (c) 2026, NVIDIA CORPORATION & AFFILIATES.
# All rights reserved.
# SPDX-License-Identifier: Apache-2.0

import logging

from langchain_core.runnables import RunnableLambda
from langgraph.graph import END, StateGraph

from gsf.retrieval.rerank.agents.column_resolution import ColumnResolutionAgent
from gsf.retrieval.rerank.agents.format_response import FormatResponseAgent
from gsf.retrieval.rerank.agents.question_extraction import QuestionExtractionAgent
from gsf.retrieval.rerank.agents.rerank_sql_results import RerankSqlResultsAgent
from gsf.retrieval.rerank.agents.route_after_execution import RouteAfterExecutionAgent
from gsf.retrieval.rerank.agents.sql_execution import SqlExecutionAgent
from gsf.retrieval.rerank.agents.sql_generation import SqlGenerationAgent
from gsf.retrieval.rerank.agents.sql_relaxation import SqlRelaxationAgent
from gsf.retrieval.rerank.state import RerankState
from gsf.retrieval.text_to_sql.base import agent_wrapper

logger = logging.getLogger(__name__)


def route_after_execution(state: RerankState) -> str:
    """Return the post-execution routing decision set on the state."""
    return state.get("decision", "") or "format"


def create_graph():
    """Build the rerank graph.

    Flow: ``question_extraction`` -> ``column_resolution`` -> ``sql_generation``
    -> ``sql_execution`` -> ``route_after_execution`` which either routes to
    ``sql_relaxation`` (strip term/detail filters, then re-execute once), goes to
    ``rerank_sql_results``, or jumps straight to ``format_response`` -> END.
    """
    question_extraction_agent = QuestionExtractionAgent()
    column_resolution_agent = ColumnResolutionAgent()
    sql_generation_agent = SqlGenerationAgent()
    sql_execution_agent = SqlExecutionAgent()
    route_after_execution_agent = RouteAfterExecutionAgent()
    sql_relaxation_agent = SqlRelaxationAgent()
    rerank_sql_results_agent = RerankSqlResultsAgent()
    format_response_agent = FormatResponseAgent()

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
    graph.add_node(
        "route_after_execution",
        RunnableLambda(agent_wrapper(route_after_execution_agent)),
    )
    graph.add_node(
        "sql_relaxation",
        RunnableLambda(agent_wrapper(sql_relaxation_agent)),
    )
    graph.add_node(
        "rerank_sql_results",
        RunnableLambda(agent_wrapper(rerank_sql_results_agent)),
    )
    graph.add_node(
        "format_response",
        RunnableLambda(agent_wrapper(format_response_agent)),
    )

    graph.set_entry_point("question_extraction")
    graph.add_edge("question_extraction", "column_resolution")
    graph.add_edge("column_resolution", "sql_generation")
    graph.add_edge("sql_generation", "sql_execution")
    graph.add_edge("sql_execution", "route_after_execution")

    graph.add_conditional_edges(
        "route_after_execution",
        route_after_execution,
        {
            "retry": "sql_relaxation",
            "rerank": "rerank_sql_results",
            "format": "format_response",
        },
    )

    graph.add_edge("sql_relaxation", "sql_execution")
    graph.add_edge("rerank_sql_results", "format_response")
    graph.add_edge("format_response", END)

    return graph


__all__ = [
    "RerankState",
    "create_graph",
]
