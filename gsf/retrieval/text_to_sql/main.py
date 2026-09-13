# SPDX-FileCopyrightText: Copyright (c) 2026, NVIDIA CORPORATION & AFFILIATES.
# All rights reserved.
# SPDX-License-Identifier: Apache-2.0

import logging
import time
from datetime import datetime
from typing import Generator

from langchain_core.messages import HumanMessage, SystemMessage

from gsf.retrieval.text_to_sql.text_to_sql_graph import (
    INTENT_VALIDATION_SKIPPED_AFTER,
    NODE_START_EVENT,
    _prediction_enabled,
    create_graph,
)
from gsf.retrieval.text_to_sql.agents.empty_like_result_check import (
    _is_empty_db_result,
)
from gsf.retrieval.text_to_sql.connector_routing import (
    resolve_target_database_name,
)
from gsf.retrieval.text_to_sql.decomposition import (
    MAX_SUB_QUESTIONS,
    SubAnswer,
    build_step_evidence,
    is_decomposition_enabled,
    summarize_sub_answer,
)
from gsf.retrieval.text_to_sql.node_labels import NODE_LABELS
from gsf.retrieval.text_to_sql.state import AgentState, TextToSQLPayload
from gsf.retrieval.text_to_sql.prompts import main_system_prompt_template
from gsf.retrieval.text_to_sql.agents.question_decomposition import (
    QuestionDecompositionAgent,
)
from gsf.retrieval.data_access.custom_analyses import fetch_custom_analyses
from gsf.utils.llm_invoke import get_llm_client

logger = logging.getLogger(__name__)


class AgentRunError(RuntimeError):
    """Graph failure carrying the last node and recoverable partial answer."""

    def __init__(
        self,
        message: str,
        *,
        node: str | None = None,
        partial_answer: dict | None = None,
    ) -> None:
        super().__init__(message)
        self.node = node
        self.partial_answer = partial_answer or {}


try:
    llm_client = get_llm_client()
except ValueError as e:
    logger.error("Failed to initialize LLM client: %s", e)
    llm_client = None

graph = create_graph()
app = graph.compile()

# Node name for the decomposition step. Not a graph node -- it runs before the
# graph and decides how many times the graph runs -- but it is reported as one
# so a client renders it in the same step list as everything else.
DECOMPOSITION_NODE = "question_decomposition"

decomposition_agent = QuestionDecompositionAgent(max_sub_questions=MAX_SUB_QUESTIONS)

# Whether the combined precheck sits between validation and execution.
# Read off the graph that was actually built rather than re-reading
# the probe flags: ``create_graph`` evaluates them once at import, so a later
# change would leave the two disagreeing about which node is the last gate
# before execution. See ``_sql_about_to_run``.
_COMBINED_PRECHECK_IN_GRAPH = "precheck_combined" in graph.nodes


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

    # ``prediction=True`` only means something when the KumoRFM branch was built
    # into the graph at startup; without KUMO_RFM_API_KEY the classify node does
    # not exist, so honouring the override is impossible. Fail loudly rather than
    # silently answering with SQL.
    prediction_override = payload.get("prediction")
    if prediction_override is True and not _prediction_enabled():
        raise ValueError(
            "prediction=true was requested but the prediction flow is not "
            "configured on this deployment (KUMO_RFM_API_KEY is unset)."
        )

    initial_path_state = dict(payload.get("path_state") or {})

    target_db = payload.get("target_db")
    if target_db:
        initial_path_state["target_db"] = resolve_target_database_name(
            target_db, connectors
        )
    elif len(connectors) == 1:
        connector_db = getattr(connectors[0], "database_name", None)
        if connector_db:
            initial_path_state["target_db"] = connector_db

    submitted_question = payload["question"].strip()
    processing_question = (
        payload.get("processing_question") or ""
    ).strip() or submitted_question
    # Keep the exact submitted turn separate from a standalone follow-up rewrite.
    # Question extraction may replace normalized_question later, while intent
    # validation must continue to see both representations.
    initial_path_state["processing_question"] = processing_question

    main_system_prompt = main_system_prompt_template.format(
        date=datetime.now(),
        custom_prompts=custom_prompts_text,
    )
    messages = [
        SystemMessage(content=main_system_prompt),
        HumanMessage(content=processing_question),
    ]

    state: dict = {
        "llm": llm_client,
        "initial_question": submitted_question,
        "evidence": payload.get("evidence") or "",
        "enriched_question": payload.get("enriched_question") or "",
        "connectors": connectors,
        "messages": messages,
        "path_state": initial_path_state,
        "data_retriever": data_retriever,
        "semantic_retriever": semantic_retriever,
        "decision": "",
        "domain_rules": domain_rules,
        "glossary": list(acronyms or []),
        "prediction_override": prediction_override,
    }
    return state


def _state_for_step(
    base_state: dict,
    *,
    question: str,
    evidence: str,
    initial_question: str | None = None,
) -> dict:
    """Derive a state that answers one step of a decomposed request.

    Working memory is not carried between steps. ``path_state`` is rebuilt from
    the pristine snapshot taken before the first pass rather than handed on from
    the previous one, because almost everything a pass leaves behind —
    ``failed_attempts``, ``sql_code``, ``relevant_tables``, ``final_response`` —
    describes the question that pass answered and would mislead the next one.
    What does carry forward travels through *evidence*, which is deliberate and
    reviewable (see ``build_step_evidence``).

    *initial_question* replaces what ``get_original_question`` reports, and the
    two callers want opposite things from it. A genuine step passes its own
    question, because intent validation would otherwise weigh one step's SQL
    against the whole multi-part request and reject a correct partial answer.
    A one-step plan keeps the user's own words.
    """
    path_state = dict(base_state["path_state"])
    path_state["processing_question"] = question

    state = dict(base_state)
    state["path_state"] = path_state
    state["evidence"] = evidence
    if initial_question is not None:
        state["initial_question"] = initial_question
    # Index 0 is the system prompt built in ``_build_state``; the human turn is
    # replaced so the graph sees this step, not the request it came from.
    state["messages"] = [base_state["messages"][0], HumanMessage(content=question)]
    state["decision"] = ""
    return state


def _whole_question(state: dict) -> str:
    """The request as it will be asked when it is answered in a single pass."""
    return state.get("path_state", {}).get("processing_question") or state.get(
        "initial_question", ""
    )


def _plan_steps(state: dict) -> list[str]:
    """Split the question into single-step questions, or return it unchanged.

    Never raises and never returns empty: decomposition is an optimization, and
    a request that cannot be split is simply answered in one pass, exactly as it
    was before this existed.
    """
    try:
        result = decomposition_agent.execute(state)
    except Exception:  # noqa: BLE001 — planning must not take the run down
        logger.exception("Question decomposition failed; answering in one pass")
        return [_whole_question(state)]
    sub_questions = (result.get("path_state") or {}).get("sub_questions") or []
    planned = [str(s) for s in sub_questions] or [_whole_question(state)]
    if len(planned) <= 1:
        return [_whole_question(state)]
    return planned


def _planning_thought(sub_questions: list[str], whole_question: str) -> str | None:
    """What the planner did, or ``None`` when it left the question alone."""
    if len(sub_questions) > 1:
        steps = "\n".join(f"{i}. {q}" for i, q in enumerate(sub_questions, 1))
        return f"Answering in {len(sub_questions)} steps:\n{steps}"
    if sub_questions and sub_questions[0] != whole_question:
        return f"Read the question as: {sub_questions[0]}"
    return None


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
        return final_response
    return {"response": str(final_response)}


def _sql_about_to_run(node_name: str, node_output: dict, node_path_state: dict) -> str:
    """The SQL this node just cleared for execution, or ``""``.

    Deliberately not emitted at generation time: both validations routinely
    send a query back for reconstruction, so a draft is frequently not what
    runs. The consequence is that a run which never clears its final gate
    (``unconstructable`` after 8 attempts) shows no SQL at all.

    Which node *is* the final gate is decided by ``create_graph`` and is not
    visible here: the proactive value check, when built in, sits after intent
    validation and can still bounce a query to reconstruction, and past
    ``INTENT_VALIDATION_SKIPPED_AFTER`` reconstructions intent validation is
    skipped entirely.
    """
    decision = (node_output or {}).get("decision") or ""

    if _COMBINED_PRECHECK_IN_GRAPH:
        cleared = node_name == "precheck_combined" and decision == "valid_sql"
    else:
        cleared = decision == "intent_valid" or (
            node_name == "validate_sql_query"
            and decision == "valid_sql"
            and len(node_path_state.get("failed_attempts") or [])
            > INTENT_VALIDATION_SKIPPED_AFTER
        )
    if not cleared:
        return ""

    # ``sql_code`` is what ``SQLExecutionAgent`` runs; the generation result
    # only covers intent validation's early return when there is no SQL.
    sql = (node_path_state.get("sql_code") or "").strip()
    if sql:
        return sql
    generated = node_path_state.get("sql_generation_result")
    return (getattr(generated, "sql_code", "") or "").strip()


def _merge_node_output(final_state: dict, node_output: dict | None) -> None:
    """Fold one graph node's output into the accumulated state, in place.

    ``path_state`` is merged key-by-key (nodes only ever return the subset
    they touched); every other top-level key is overwritten outright.
    """
    if not node_output:
        return
    if "path_state" in node_output:
        final_state.setdefault("path_state", {})
        final_state["path_state"].update(node_output["path_state"])
    for key, value in node_output.items():
        if key != "path_state":
            final_state[key] = value


def _build_thoughts_summary(thoughts_log: list[dict]) -> str:
    """Concatenate the run's per-node thought entries into one summary string.

    Deterministic (no extra LLM call): one bullet per entry, labelled with the
    same human-readable name the live step events use, in the order the nodes
    actually ran (a node visited more than once — e.g. during reconstruction
    retries — contributes one bullet per visit).
    """
    lines = [
        f"- {NODE_LABELS.get(entry['node'], entry['node'])}: {entry['text']}"
        for entry in thoughts_log
        if entry.get("text")
    ]
    return "\n".join(lines)


def _extract_partial_answer(final_state: dict) -> dict:
    """Return generated SQL/response that existed before a downstream failure."""
    path_state = final_state.get("path_state", {})
    generation = path_state.get("sql_generation_result")
    sql_code = path_state.get("sql_code") or getattr(generation, "sql_code", "")
    response = getattr(generation, "response", "")
    thought = getattr(generation, "thought", "")
    if not sql_code and not response:
        return {}
    return {
        "sql_code": str(sql_code or ""),
        "response": str(response or ""),
        "thought": str(thought or ""),
    }


class _GraphPass:
    """One run of the node graph, streamed event by event.

    Holds ``last_node`` and ``final_state`` as attributes rather than returning
    them, so the caller can still build an error event out of a pass that
    raised part-way through.
    """

    def __init__(self, state: dict) -> None:
        self._state = state
        self.last_node: str | None = None
        self.final_state: dict = dict(state)

    def stream(self, tags: dict | None = None) -> Generator[dict, None, None]:
        tags = tags or {}
        # Last SQL surfaced to the client. A query can clear its final gate more
        # than once (an empty result sends it back through validation unchanged),
        # so dedupe rather than re-emitting the same query.
        streamed_sql: str | None = None

        # ``custom`` payloads stream the instant a node writes one (as it
        # begins); ``updates`` only arrive once it has returned. Reading
        # updates alone would label the screen with the previously finished
        # node, so a slow reconstruction looks like a hung validation.
        for mode, chunk in app.stream(
            self._state,
            stream_mode=["updates", "custom"],
            config={"recursion_limit": 45},
        ):
            if mode == "custom":
                if (chunk or {}).get("type") == NODE_START_EVENT:
                    started = chunk.get("node")
                    if started:
                        # A node that raises produces no update, so tracking
                        # completions alone would blame the node before it.
                        self.last_node = started
                        yield {
                            "type": "step",
                            "phase": "start",
                            "node": started,
                            "thought": None,
                            **tags,
                        }
                continue

            logger.info("--- AGENT STEP ---")
            for node_name, node_output in chunk.items():
                self.last_node = node_name
                logger.info("Node: %s", node_name)

                # A node records its own thought (if any) at the tail of
                # path_state["thoughts_log"] — see BaseAgent.record_thought.
                # Only surface it here when this node is the one that just
                # added it, so a step event never shows a stale entry left
                # over from an earlier node.
                thought = None
                node_path_state = (node_output or {}).get("path_state") or {}
                thoughts_log = node_path_state.get("thoughts_log") or []
                if thoughts_log and thoughts_log[-1].get("node") == node_name:
                    thought = thoughts_log[-1].get("text")

                # Only place a thought can be attached: the node has to finish
                # before it has one to report.
                yield {
                    "type": "step",
                    "phase": "end",
                    "node": node_name,
                    "thought": thought,
                    **tags,
                }

                # Surface the SQL once a node has cleared it for execution,
                # so it is on screen while the database runs it rather than
                # only landing with the final answer. Drafts that validation
                # is about to send back for reconstruction are deliberately
                # not shown — see ``_sql_about_to_run``.
                node_sql = _sql_about_to_run(node_name, node_output, node_path_state)
                if node_sql and node_sql != streamed_sql:
                    streamed_sql = node_sql
                    yield {"type": "sql", "node": node_name, "sql": node_sql, **tags}

                _merge_node_output(self.final_state, node_output)


def stream_agent_response(
    payload: TextToSQLPayload,
) -> Generator[dict, None, None]:
    """Yield two ``{"type": "step", "node": ..., "phase": ...}`` events per
    graph node — ``"start"`` as it begins (so a client can label the work in
    progress) and ``"end"`` when it returns, carrying its ``thought`` — plus
    ``{"type": "sql", "node": ..., "sql": ...}`` once a query has cleared
    validation and is about to run (see ``_sql_about_to_run``), then
    ``{"type": "result", "answer": ...}`` with the final answer (its
    ``thoughts`` key summarizes every ``thought`` collected along the way).
    On error yields ``{"type": "error", "message": ...}``.

    A request the decomposer split into several steps runs the graph once per
    step and emits the same events for each, additionally tagged with
    ``step_index``, ``step_total`` and ``step_question``. Only the last step
    produces a ``result``; the earlier ones exist to feed it (see
    ``build_step_evidence``). Should those steps arrive at an empty result, the
    whole request is answered once more in a single pass, tagged ``retry``, and
    that pass supplies the ``result``. A single-step request — every request
    when ``QUESTION_DECOMPOSITION`` is off — emits exactly what it always has.
    """
    t0 = time.perf_counter()

    logger.info("Text-to-SQL agent started for question: %s", payload["question"])

    base_state = _build_state(payload)
    base_evidence = base_state.get("evidence") or ""

    whole_question = _whole_question(base_state)

    current: _GraphPass = _GraphPass(base_state)
    try:
        # Planning is skipped entirely, its events included, when the feature is
        # off. A deployment that has not opted in sees the exact event stream it
        # saw before multi-step answering existed.
        if is_decomposition_enabled():
            yield {
                "type": "step",
                "phase": "start",
                "node": DECOMPOSITION_NODE,
                "thought": None,
            }
            sub_questions = _plan_steps(base_state)
            multi_step = len(sub_questions) > 1
            yield {
                "type": "step",
                "phase": "end",
                "node": DECOMPOSITION_NODE,
                "thought": _planning_thought(sub_questions, whole_question),
            }
        else:
            sub_questions = [whole_question]
            multi_step = False

        answered: list[SubAnswer] = []
        combined_thoughts: list[dict] = []
        answer: dict = {}

        for index, sub_question in enumerate(sub_questions, 1):
            if multi_step:
                tags = {
                    "step_index": index,
                    "step_total": len(sub_questions),
                    "step_question": sub_question,
                }
                state = _state_for_step(
                    base_state,
                    question=sub_question,
                    evidence=build_step_evidence(base_evidence, answered),
                    initial_question=sub_question,
                )
                combined_thoughts.append(
                    {
                        "node": DECOMPOSITION_NODE,
                        "text": f"Step {index} of {len(sub_questions)}: {sub_question}",
                    }
                )
            elif sub_question != whole_question:
                # A one-step plan is still a restatement, and the useful part of
                # it is an implicit scope made explicit — which population an
                # "average" is taken over, say. Answer the restatement, but leave
                # ``initial_question`` alone so intent validation still measures
                # the SQL against what the user actually wrote.
                tags = {}
                state = _state_for_step(
                    base_state, question=sub_question, evidence=base_evidence
                )
                combined_thoughts.append(
                    {
                        "node": DECOMPOSITION_NODE,
                        "text": f"Read the question as: {sub_question}",
                    }
                )
            else:
                tags = {}
                state = base_state

            current = _GraphPass(state)
            yield from current.stream(tags)

            answer = _extract_answer(current.final_state)
            combined_thoughts.extend(
                current.final_state.get("path_state", {}).get("thoughts_log") or []
            )
            if multi_step:
                answered.append(summarize_sub_answer(sub_question, answer))

        # A step is resolved against its own population, which can be wider than
        # the one the whole request implies: "the county of the lowest-scoring
        # school" picks the lowest scorer over every score on file, including
        # schools that have no county recorded, and the step that then asks for
        # its county matches nothing. The graph cannot recover the constraint --
        # it never saw the unsplit request -- but an empty result is a reliable
        # symptom, and the question as asked still carries it.
        if multi_step and _is_empty_db_result(answer.get("sql_response_from_db")):
            logger.info("Decomposed answer was empty; re-answering in a single pass")
            combined_thoughts.append(
                {
                    "node": DECOMPOSITION_NODE,
                    "text": (
                        "The steps returned no rows, so one of them likely "
                        "narrowed the question too far; answering it whole "
                        "instead."
                    ),
                }
            )
            stepped_answer = answer
            try:
                current = _GraphPass(base_state)
                yield from current.stream({"retry": "single_pass"})
                answer = _extract_answer(current.final_state)
                combined_thoughts.extend(
                    current.final_state.get("path_state", {}).get("thoughts_log") or []
                )
            except Exception:  # noqa: BLE001 — a fallback must not take the run down
                logger.exception("Single-pass retry failed; keeping the stepped answer")
                answer = stepped_answer

        thoughts_summary = _build_thoughts_summary(combined_thoughts)
        if isinstance(answer, dict) and thoughts_summary:
            answer["thoughts"] = thoughts_summary
        elapsed = time.perf_counter() - t0
        logger.debug("Final answer (%.2fs):\n%s", elapsed, answer)
        yield {"type": "result", "answer": answer}

    except Exception as exc:
        logger.exception("Error during agent stream")
        yield {
            "type": "error",
            "message": f"Agent failed after {current.last_node or 'graph_start'}: {exc}",
            "node": current.last_node,
            "error_type": type(exc).__name__,
            "partial_answer": _extract_partial_answer(current.final_state),
        }


def get_agent_response(payload: TextToSQLPayload) -> dict:
    """Non-streaming convenience wrapper around ``stream_agent_response``."""
    for event in stream_agent_response(payload):
        if event["type"] == "result":
            return event["answer"]
        if event["type"] == "error":
            raise AgentRunError(
                event["message"],
                node=event.get("node"),
                partial_answer=event.get("partial_answer"),
            )
    return {"response": "SQL can't be constructed.", "sql_code": "", "result": None}


def get_agent_response_with_state(payload: TextToSQLPayload) -> dict:
    """Like get_agent_response but also returns path_state in the result under key 'path_state'."""
    # Required by gsf/retrieval/interactive/coordinator.py to persist path_state across turns/phases.
    state = _build_state(payload)
    final_state = dict(state)

    try:
        for step in app.stream(state, config={"recursion_limit": 45}):
            for node_output in step.values():
                _merge_node_output(final_state, node_output)
    except Exception as exc:
        logger.exception("Error during agent stream in get_agent_response_with_state")
        # The stream may have already produced a valid, executed SQL query
        # (e.g. several reconstruction rounds succeeded) before a later node
        # raised — most commonly GraphRecursionError from an intent-validation
        # <-> reconstruction oscillation. Fall back to whatever SQL is already
        # sitting in path_state instead of discarding it and submitting blank
        # SQL, which is a guaranteed Phase 1 failure even when the last known
        # SQL was correct.
        interrupted_path_state = final_state.get("path_state") or {}
        fallback_sql = interrupted_path_state.get("sql_code", "") or ""
        return {
            "response": f"Agent failed: {exc}",
            "sql_code": fallback_sql,
            "path_state": interrupted_path_state,
        }

    # merge path_state: start with initial, overlay final accumulated
    merged_path_state = dict(state.get("path_state") or {})
    if "path_state" in final_state:
        merged_path_state.update(final_state["path_state"])

    answer = _extract_answer(final_state)
    if isinstance(answer, dict):
        result = dict(answer)
    else:
        result = {"response": str(answer)}
    result["path_state"] = merged_path_state
    return result


__all__ = [
    "get_agent_response",
    "get_agent_response_with_state",
    "stream_agent_response",
    "app",
    "graph",
    "llm_client",
    "AgentRunError",
]
