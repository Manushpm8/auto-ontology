# SPDX-FileCopyrightText: Copyright (c) 2026, NVIDIA CORPORATION & AFFILIATES.
# All rights reserved.
# SPDX-License-Identifier: Apache-2.0

import logging
import json
import os
import time
from datetime import datetime
from typing import Generator

from langchain_core.messages import HumanMessage, SystemMessage

from gsf.retrieval.text_to_sql.text_to_sql_graph import create_graph
from gsf.retrieval.text_to_sql.state import AgentState, TextToSQLPayload
from gsf.retrieval.text_to_sql.prompts import main_system_prompt_template
from gsf.retrieval.data_access.custom_analyses import fetch_custom_analyses
from gsf.utils.llm_invoke import get_llm_client

logger = logging.getLogger(__name__)

_ENTITY_MODEL = os.environ.get("ENTITY_EXTRACTION_MODEL")

try:
    llm_client = get_llm_client()
except ValueError as e:
    logger.error("Failed to initialize LLM client: %s", e)
    llm_client = None

entity_llm_client = None
if _ENTITY_MODEL:
    try:
        entity_llm_client = get_llm_client(model=_ENTITY_MODEL, max_tokens=2048)
        logger.info("Entity extraction will use model: %s", _ENTITY_MODEL)
    except ValueError as e:
        logger.warning("Failed to init entity LLM (%s): %s", _ENTITY_MODEL, e)


graph = create_graph()
app = graph.compile()


def _build_state(payload: TextToSQLPayload) -> AgentState:
    custom_prompts = payload.get("custom_prompts", "")
    acronyms = payload.get("acronyms", [])
    connectors = payload.get("connectors", [])
    if not connectors:
        raise ValueError(
            "TextToSQLPayload is missing required 'connectors'. "
            "Provide a non-empty list of database connectors, each with a valid 'dialect' attribute."
        )

    data_retriever = payload.get("data_retriever")
    if data_retriever is None:
        raise ValueError(
            "TextToSQLPayload is missing required 'data_retriever' (nemo_retriever.retriever.Retriever "
            "instance). Construct a Retriever once at startup and pass it in the payload."
        )
    semantic_retriever = payload.get("semantic_retriever")
    if semantic_retriever is None:
        logger.warning(
            "No 'semantic_retriever' in payload — "
            "ColumnAttribute, CustomAnalysis, and SqlAttribute searches will be skipped."
        )

    custom_prompts_text = f"{custom_prompts}\n\n" if custom_prompts else ""
    domain_rules = fetch_custom_analyses() + list(acronyms or [])

    initial_path_state = dict(payload.get("path_state") or {})

    target_db = payload.get("target_db")
    if target_db:
        initial_path_state["target_db"] = target_db
    elif len(connectors) == 1:
        connector_db = getattr(connectors[0], "database_name", None)
        if connector_db:
            initial_path_state["target_db"] = connector_db

    main_system_prompt = main_system_prompt_template.format(
        date=datetime.now(),
        custom_prompts=custom_prompts_text,
    )
    messages = [
        SystemMessage(content=main_system_prompt),
        HumanMessage(content=payload["question"]),
    ]

    state: dict = {
        "llm": llm_client,
        "initial_question": payload["question"],
        "connectors": connectors,
        "messages": messages,
        "path_state": initial_path_state,
        "data_retriever": data_retriever,
        "semantic_retriever": semantic_retriever,
        "decision": "",
        "domain_rules": domain_rules,
    }
    if entity_llm_client is not None:
        state["entity_llm"] = entity_llm_client
    return state


def _schema_debug_snapshot(path_state: dict) -> dict:
    """Compact semantic-layer context for gold-coverage diagnostics."""

    def _qname(t: dict) -> str:
        schema = (t.get("schema_name") or "").strip()
        name = (t.get("name") or "").strip()
        return f"{schema}.{name}" if schema else name

    tables = path_state.get("relevant_tables") or []
    cols = path_state.get("retrieved_column_attributes") or []
    joins = path_state.get("attribute_join_paths") or []
    sql_attrs = path_state.get("sql_attributes") or []
    primary = path_state.get("primary_attribute") or {}

    col_summaries = []
    for c in cols[:30]:
        if not isinstance(c, dict):
            continue
        meta = c.get("metadata") or {}
        if isinstance(meta, str):
            try:
                meta = json.loads(meta)
            except Exception:
                meta = {}
        if not isinstance(meta, dict):
            meta = {}
        col_summaries.append(
            {
                "id": str(c.get("id") or ""),
                "name": str(c.get("name") or meta.get("name") or c.get("text") or "")[
                    :80
                ],
                "source_column": str(
                    c.get("source_column") or meta.get("source_column") or ""
                )[:80],
                "table_name": str(
                    c.get("table_name")
                    or meta.get("table_name")
                    or meta.get("source_table")
                    or ""
                )[:80],
            }
        )

    join_summaries = []
    for jp in joins[:20]:
        if not isinstance(jp, dict):
            continue
        hops = []
        for hop in jp.get("path") or []:
            if not isinstance(hop, dict):
                continue
            hops.append(
                {
                    "source_table": str(hop.get("source_table") or ""),
                    "target_table": str(hop.get("target_table") or ""),
                    "source_column": str(hop.get("source_column") or ""),
                    "target_column": str(hop.get("target_column") or ""),
                }
            )
        join_summaries.append(
            {
                "dest_attr": str(jp.get("attr_name") or "")[:80],
                "dest_table": str(jp.get("table_name") or ""),
                "n_hops": len(hops),
                "path": hops[:8],
            }
        )

    return {
        "entities": list(path_state.get("entities") or [])[:20],
        "table_names": [_qname(t) for t in tables if isinstance(t, dict)],
        "col_attrs": col_summaries,
        "primary_attribute": {
            "attr_name": str(primary.get("attr_name") or ""),
            "col_name": str(primary.get("col_name") or ""),
            "table_name": str(primary.get("table_name") or ""),
        }
        if primary
        else {},
        "join_paths": join_summaries,
        "sql_attr_names": [
            str(x.get("name") or "")[:100]
            for x in sql_attrs
            if isinstance(x, dict) and x.get("name")
        ],
        "n_few_shot": len(path_state.get("similar_questions") or []),
    }


def _extract_answer(final_state: dict) -> dict:
    path_state = final_state.get("path_state", {})
    final_response = path_state.get("final_response")

    if final_response is None:
        messages_out = final_state.get("messages") or []
        if isinstance(messages_out, list) and messages_out:
            final_response = messages_out[-1]
        else:
            final_response = ""

    if isinstance(final_response, dict):
        answer = dict(final_response)
    else:
        answer = {"response": str(final_response)}

    # Attach semantic-layer snapshot for eval gold-coverage analysis.
    try:
        answer["schema_debug"] = _schema_debug_snapshot(path_state)
    except Exception:
        answer["schema_debug"] = {}
    # Expose the candidate pool so the eval CSV can carry it. Oracle (best-of-N)
    # is derived from these, and until now they existed only in the generator's
    # debug log — a file that gets cleared between runs. One such clear made a
    # completed 1,534-question run's oracle permanently unrecoverable.
    try:
        answer["sql_candidates"] = [
            sql
            for cand in (path_state.get("sql_candidates") or [])
            if (sql := getattr(cand, "sql_code", "") or "")
        ]
    except Exception:
        answer["sql_candidates"] = []
    return answer


def stream_agent_response(
    payload: TextToSQLPayload,
) -> Generator[dict, None, None]:
    """Yield ``{"type": "step", "node": ...}`` for each graph node,
    then ``{"type": "result", "answer": ...}`` with the final answer.
    On error yields ``{"type": "error", "message": ...}``."""
    t0 = time.perf_counter()

    logger.info("Text-to-SQL agent started for question: %s", payload["question"])

    state = _build_state(payload)
    final_state = dict(state)

    try:
        for step in app.stream(state, config={"recursion_limit": 45}):
            logger.info("--- AGENT STEP ---")
            for node_name, node_output in step.items():
                logger.info("Node: %s", node_name)
                yield {"type": "step", "node": node_name}

                if node_output:
                    if "path_state" in node_output:
                        if "path_state" not in final_state:
                            final_state["path_state"] = {}
                        final_state["path_state"].update(node_output["path_state"])
                    for key, value in node_output.items():
                        if key != "path_state":
                            final_state[key] = value

        answer = _extract_answer(final_state)
        elapsed = time.perf_counter() - t0
        log_answer = answer
        if isinstance(answer, dict) and answer.get("sql_response_from_db") is not None:
            db = str(answer["sql_response_from_db"])
            if len(db) > 1000:
                log_answer = {
                    **answer,
                    "sql_response_from_db": db[:1000] + "…",
                }
        logger.info("Final answer (%.2fs):\n%s", elapsed, log_answer)
        yield {"type": "result", "answer": answer}

    except Exception as exc:
        logger.exception("Error during agent stream")
        yield {"type": "error", "message": f"Agent failed: {exc}"}


def get_agent_response(payload: TextToSQLPayload) -> dict:
    """Non-streaming convenience wrapper around ``stream_agent_response``."""
    for event in stream_agent_response(payload):
        if event["type"] == "result":
            return event["answer"]
        if event["type"] == "error":
            raise RuntimeError(event["message"])
    return {"response": "SQL can't be constructed.", "sql_code": "", "result": None}


def run_until_node(payload: TextToSQLPayload, stop_after: str) -> dict:
    """Run the graph and return the accumulated state once ``stop_after`` produces output.

    Streams the compiled graph exactly like :func:`stream_agent_response` — building
    the state with :func:`_build_state` and merging each node's ``path_state`` — but
    stops as soon as the ``stop_after`` node has run, before the next node executes.
    This lets callers reuse the front of the pipeline (e.g. up to ``prepare_candidates``,
    to read ``relevant_tables``/``attribute_join_paths``) without paying for the rest of
    the flow. The returned dict is the full ``AgentState`` (top-level keys such as
    ``llm``/``connectors``/``semantic_retriever`` plus the merged ``path_state``).
    """
    state = _build_state(payload)
    final_state = dict(state)

    for step in app.stream(state, config={"recursion_limit": 45}):
        for node_name, node_output in step.items():
            if node_output:
                if "path_state" in node_output:
                    final_state.setdefault("path_state", {})
                    final_state["path_state"].update(node_output["path_state"])
                for key, value in node_output.items():
                    if key != "path_state":
                        final_state[key] = value
            if node_name == stop_after:
                return final_state

    return final_state


__all__ = [
    "get_agent_response",
    "stream_agent_response",
    "run_until_node",
    "app",
    "graph",
    "llm_client",
]
