from __future__ import annotations

import re
from typing import Any, Union

# NOTE: get_agent_response_with_state and TextToSQLPayload are imported lazily
# inside _run_sql_generation to avoid triggering LLM client initialisation at
# import time (which requires NVIDIA_API_KEY to be set).
from .clarify import should_clarify
from .merge import merge_clarification
from .types import AskUserAction, SubmitSQLAction, TurnType
from .state import InteractiveSessionState


# ── Message classifier ──────────────────────────────────────────────────────

def _classify_message(message: str) -> TurnType:
    if "Your SQL is not executable" in message or "Your SQL is not correct" in message:
        return TurnType.DEBUG
    if "Phase 1 is complete" in message and "follow-up" in message.lower():
        return TurnType.FOLLOW_UP
    return TurnType.INITIAL


# ── Question extraction ─────────────────────────────────────────────────────

def _extract_question_from_initial(message: str) -> str:
    """Extract the user question from the Phase 1 orchestrator message."""
    match = re.search(r"User Query:\s*\n(.*?)(?:\n\n|$)", message, re.DOTALL)
    if match:
        return match.group(1).strip()
    return ""


def _extract_followup_question(message: str) -> str:
    """Extract follow-up query from Phase 2 message."""
    match = re.search(
        r"follow-up question:\s*\n\n(.*?)(?:\n\nGenerate|$)", message, re.DOTALL
    )
    if match:
        return match.group(1).strip()
    return message


# ── Seeds ───────────────────────────────────────────────────────────────────

def _apply_debug_seed(session: InteractiveSessionState, message: str) -> None:
    """Prepare path_state to resume at reconstruct_sql with Bird's error as context."""
    session.path_state["_resume_from"] = "reconstruct_sql"
    session.path_state["error"] = message
    session.path_state["sql_attempts"] = 0
    session.path_state["reconstruction_count"] = 0
    session.path_state["error_analysis_done"] = False
    for key in ("repair_attempted", "value_repair_done"):
        session.path_state.pop(key, None)


def _apply_follow_up_seed(session: InteractiveSessionState, message: str) -> None:
    """Prepare session for Phase 2: new question, soft-seed Phase 1 result."""
    follow_up_q = _extract_followup_question(message)

    # Soft-seed: expose Phase 1 Q+SQL as similar_questions [[question, sql]] format
    p1_sql = session.path_state.get("sql_code", "")
    p1_question = session.phase1_question or session.working_question
    if p1_sql and p1_question:
        session.path_state["similar_questions"] = [[p1_question, p1_sql]]

    # Clear Phase 1 SQL artifacts; keep relevant_tables as merge hints
    for key in (
        "normalized_question", "sql_code", "sql_generation_result",
        "error", "sql_attempts", "reconstruction_count",
        "error_analysis_done", "_resume_from",
        "final_response", "sql_response_from_db",
    ):
        session.path_state.pop(key, None)

    # Reset clarify state for Phase 2
    session.working_question = follow_up_q if follow_up_q else session.working_question
    session.original_question = session.working_question
    session.clarify_history = []
    session._pending_question = None


# ── SQL generation ──────────────────────────────────────────────────────────

def _run_sql_generation(session: InteractiveSessionState) -> str:
    """Call GSF and persist the returned path_state back to session."""
    from gsf.retrieval.text_to_sql.main import get_agent_response_with_state
    from gsf.retrieval.text_to_sql.state import TextToSQLPayload

    payload: TextToSQLPayload = {
        "question": session.working_question,
        "data_retriever": session.data_retriever,
        "semantic_retriever": session.semantic_retriever,
        "connectors": session.connectors,
        "path_state": dict(session.path_state),  # copy so GSF doesn't mutate in place
        "acronyms": [],
        "custom_prompts": "",
    }
    result = get_agent_response_with_state(payload)

    # Merge returned path_state back into durable session path_state
    if returned_ps := result.get("path_state"):
        session.path_state.update(returned_ps)

    # Clear resume_from after use so next call goes through full pipeline
    session.path_state.pop("_resume_from", None)

    return result.get("sql_code", "")


# ── LLM accessor ────────────────────────────────────────────────────────────

_llm = None
_fast_llm = None


def _get_llm():
    global _llm
    if _llm is None:
        from gsf.retrieval.text_to_sql.main import llm_client
        _llm = llm_client
    return _llm


def _get_fast_llm():
    global _fast_llm
    if _fast_llm is None:
        from gsf.retrieval.text_to_sql.main import non_reasoning_llm_client
        _fast_llm = non_reasoning_llm_client or _get_llm()
    return _fast_llm


# ── Public API ───────────────────────────────────────────────────────────────

def create_session(
    session_id: str,
    task_id: str,
    db_name: str,
    db_schema: str,
    external_kg: str,
    question: str,
    data_retriever: Any,
    semantic_retriever: Any,
    connectors: list,
    max_clarify_turns: int = 5,
) -> InteractiveSessionState:
    return InteractiveSessionState(
        session_id=session_id,
        task_id=task_id,
        db_name=db_name,
        db_schema=db_schema,
        external_kg=external_kg,
        original_question=question,
        working_question=question,
        max_clarify_turns=max_clarify_turns,
        data_retriever=data_retriever,
        semantic_retriever=semantic_retriever,
        connectors=connectors,
        path_state={"target_db": db_name},
    )


def step(
    session: InteractiveSessionState,
    orchestrator_message: str,
) -> Union[AskUserAction, SubmitSQLAction]:
    turn_type = _classify_message(orchestrator_message)

    if turn_type == TurnType.INITIAL:
        # Extract question from message on first turn (question not in init_session state)
        if not session.clarify_history and not session.path_state.get("sql_code"):
            extracted = _extract_question_from_initial(orchestrator_message)
            if extracted:
                session.original_question = extracted
                session.working_question = extracted

    elif turn_type == TurnType.FOLLOW_UP:
        _apply_follow_up_seed(session, orchestrator_message)

    elif turn_type == TurnType.DEBUG:
        _apply_debug_seed(session, orchestrator_message)

    # Debug turns and exhausted budgets skip clarification — go straight to SQL
    under_budget = len(session.clarify_history) < session.max_clarify_turns
    if turn_type != TurnType.DEBUG and under_budget:
        should_ask, question = should_clarify(session, _get_fast_llm())
        if should_ask:
            session._pending_question = question
            return AskUserAction(question=question)

    sql = _run_sql_generation(session)
    return SubmitSQLAction(sql=sql)


def apply_user_answer(session: InteractiveSessionState, answer: str) -> None:
    """Record user answer and merge into working question."""
    if session._pending_question:
        session.clarify_history.append({"q": session._pending_question, "a": answer})
        session._pending_question = None
    session.working_question = merge_clarification(
        session.original_question, session.clarify_history, _get_fast_llm()
    )


def apply_submit_result(session: InteractiveSessionState, result: dict) -> None:
    """Update session with Bird's submit response for future debug seeding."""
    session.latest_feedback = result.get("message", "")
    session.path_state["error"] = session.latest_feedback
    # Save Phase 1 context for follow-up soft seed (only once)
    if session.phase1_question is None:
        session.phase1_question = session.working_question
        session.phase1_sql = session.path_state.get("sql_code", "")
