# SPDX-FileCopyrightText: Copyright (c) 2026, NVIDIA CORPORATION & AFFILIATES.
# All rights reserved.
# SPDX-License-Identifier: Apache-2.0

"""Orchestration for ``text-to-data`` / ``text-to-pql`` / ``entity-coverage``.

Reuses the front of the text-to-SQL prediction pipeline in-process: it runs the
graph up to ``prepare_candidates`` to gather the data objects (relevant tables,
join paths, columns), then — for ``text_to_pql`` — reuses the ``prepare_prediction_graph``
node to build the KumoRFM context and generates the PQL WITHOUT running inference.

``entity_coverage`` runs a separate fast LangGraph that extracts entities,
retrieves semantic candidates, and returns a deterministic coverage grade.

The pipeline shares retriever/connector state and is not safe to run in parallel
(the same constraint that makes chat single-slot), so runs are serialized here.
"""

from __future__ import annotations

import logging
import threading
from typing import Any

from gsf.dal.datasources import fetch_columns_for_table
from gsf.retrieval.entity_coverage.main import get_coverage_response
from gsf.retrieval.entity_coverage.main import llm_client as coverage_llm_client
from gsf.retrieval.entity_coverage.state import (
    DEFAULT_MAX_DISTANCE,
    EntityCoveragePayload,
)
from gsf.retrieval.kumo import PredictionContext
from gsf.retrieval.kumo.pql_gen import generate_pql_only
from gsf.retrieval.text_to_sql.agents.prediction_graph import PredictionGraphAgent
from gsf.retrieval.text_to_sql.main import llm_client, run_until_node
from gsf.retrieval.text_to_sql.state import TextToSQLPayload
from gsf.server.chat.settings_dal import fetch_acronyms, fetch_custom_prompts
from gsf.connectors.registry import get_connectors
from gsf.utils.retriever import (
    get_data_objects_retriever,
    get_semantic_objects_retriever,
)

logger = logging.getLogger(__name__)

# The prediction node that produces the pre-PQL data objects (relevant_tables,
# attribute_join_paths). We stop the graph here — the next node (classify_prediction)
# never runs, so this endpoint always follows the prediction path regardless of how
# the classifier would have routed the question.
_STOP_NODE = "prepare_candidates"

# Text-to-SQL runs share retriever/connector state and a single LLM budget and are
# not safe to run in parallel; serialize like the chat pool's single slot.
_run_lock = threading.Lock()


class PredictionFlowError(RuntimeError):
    """The prediction flow could not produce a result for the question."""


def _build_payload(question: str) -> TextToSQLPayload:
    """Assemble the retriever/connector payload the pipeline needs (as chat does)."""
    connectors = get_connectors()
    if not connectors:
        raise PredictionFlowError("No database connection is configured.")
    return {
        "question": question,
        "data_retriever": get_data_objects_retriever(),
        "semantic_retriever": get_semantic_objects_retriever(),
        "connectors": connectors,
        "acronyms": fetch_acronyms(),
        "custom_prompts": fetch_custom_prompts(),
    }


def _build_coverage_payload(
    question: str,
    max_distance: float = DEFAULT_MAX_DISTANCE,
) -> EntityCoveragePayload:
    """Assemble the payload for the entity-coverage pipeline."""
    connectors = get_connectors()
    if not connectors:
        raise PredictionFlowError("No database connection is configured.")
    return {
        "question": question,
        "data_retriever": get_data_objects_retriever(),
        "semantic_retriever": get_semantic_objects_retriever(),
        "connectors": connectors,
        "acronyms": fetch_acronyms(),
        "custom_prompts": fetch_custom_prompts(),
        "max_distance": max_distance,
    }


def _relevant_table_columns(
    relevant_tables: list[dict[str, Any]],
) -> list[dict[str, Any]]:
    """Column list for each relevant table, looked up from the catalog by table id."""
    out: list[dict[str, Any]] = []
    for table in relevant_tables:
        table_id = table.get("id")
        if not table_id:
            continue
        detail = fetch_columns_for_table(table_id)
        if not detail:
            continue
        out.append(
            {
                "table": detail.get("table_name"),
                "schema_name": detail.get("schema_name"),
                "database_name": detail.get("database_name"),
                "columns": [
                    col.get("column_name") for col in detail.get("columns") or []
                ],
            }
        )
    return out


def _prepare(question: str) -> dict:
    """Run the shared front of the prediction flow (up to ``prepare_candidates``).

    Returns the accumulated ``AgentState``; ``state["path_state"]`` holds the
    pre-PQL data objects (``relevant_tables``, ``attribute_join_paths``).
    """
    if llm_client is None:
        raise PredictionFlowError("LLM client is not configured.")
    payload = _build_payload(question)
    return run_until_node(payload, _STOP_NODE)


def text_to_data(question: str) -> dict:
    """Return the data objects gathered before PQL creation for ``question``:
    the relevant tables, their catalog join paths, and each table's column list.

    Raises :class:`PredictionFlowError` when the flow cannot produce a result.
    """
    with _run_lock:
        state = _prepare(question)
        path_state = state.get("path_state", {})
        relevant_tables = path_state.get("relevant_tables") or []
        return {
            "question": question,
            "relevant_tables": relevant_tables,
            "join_paths": path_state.get("attribute_join_paths") or [],
            "columns": _relevant_table_columns(relevant_tables),
        }


def text_to_pql(question: str) -> dict:
    """Return the PQL generated for ``question`` (generation only, no prediction).

    Reuses the ``prepare_prediction_graph`` node to build the KumoRFM context
    (few-shot retrieval, table enrichment, graph/model), then generates the PQL
    without running a KumoRFM prediction.

    Raises :class:`PredictionFlowError` when the flow cannot produce a result.
    """
    with _run_lock:
        state = _prepare(question)

        prep = PredictionGraphAgent().execute(state)
        if prep.get("decision") != "predict_ready":
            final = (prep.get("path_state") or {}).get("final_response") or {}
            raise PredictionFlowError(
                final.get("response") or "Could not prepare a prediction graph."
            )

        context: PredictionContext = prep["path_state"]["prediction_context"]
        result = generate_pql_only(
            question,
            llm=state["llm"],
            kumo_model=context.kumo_model,
            connector=context.connector,
            graph_ddl=context.graph_ddl,
            graph_edges=context.graph_edges,
            graph_col_stypes=context.graph_col_stypes,
            examples=context.examples,
        )
        if not result.success:
            raise PredictionFlowError(result.error or "PQL could not be generated.")

        return {
            "question": question,
            "pql": result.pql,
            "entity_sql": result.entity_sql,
            "attempts": result.attempts,
        }


def entity_coverage(
    question: str,
    max_distance: float = DEFAULT_MAX_DISTANCE,
) -> dict:
    """Return ranked semantic candidates and a 0–1 entity coverage grade.

    Raises :class:`PredictionFlowError` when the flow cannot produce a result.
    """
    if coverage_llm_client is None:
        raise PredictionFlowError("LLM client is not configured.")
    with _run_lock:
        try:
            return get_coverage_response(
                _build_coverage_payload(question, max_distance=max_distance)
            )
        except (ValueError, RuntimeError) as exc:
            raise PredictionFlowError(str(exc)) from exc
