from __future__ import annotations

import logging
import re
from typing import Any, Union

logger = logging.getLogger(__name__)

# NOTE: get_agent_response_with_state and TextToSQLPayload are imported lazily
# inside _run_sql_generation to avoid triggering LLM client initialisation at
# import time (which requires NVIDIA_API_KEY to be set).
from concurrent.futures import ThreadPoolExecutor

from .clarify import should_clarify, refresh_grounded_kg, prune_resolved_terms, expand_kg_with_children, _STUCK_PHRASES
from gsf.retrieval.data_access.semantic_search import search_semantic_index
from gsf.semantic.constants import LABEL_COLUMN_ATTRIBUTE
from .completeness import detect_incomplete_formulas
from .grounding import ground_external_knowledge
from .merge import merge_clarification
from .types import AskUserAction, InteractivePhase, SubmitSQLAction, TurnType
from .state import InteractiveSessionState
from gsf.utils.llm_invoke import safe_invoke_text


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
    if "Your SQL is not executable:" in message:
        # Extract the actual DB error from Bird's message and inject it for reconstruction.
        after = message.split("Your SQL is not executable:", 1)[1].strip()
        actual_error = after.split("\n")[0].strip()
        session.path_state["error"] = actual_error
        logger.info("Debug seed: execution error → %s", actual_error[:200])
    else:
        # SQL ran but results didn't match — no execution error detail available.
        # Set a minimal placeholder so sql_reconstruction passes validation and runs.
        session.path_state["error"] = "SQL returned incorrect results."
        logger.info("Debug seed: wrong results (no execution error to inject)")
    session.path_state["sql_attempts"] = 0
    session.path_state["reconstruction_count"] = 0
    session.path_state["error_analysis_done"] = False
    for key in ("repair_attempted", "value_repair_done"):
        session.path_state.pop(key, None)


def _apply_follow_up_seed(session: InteractiveSessionState, message: str) -> None:
    """Prepare session for Phase 2: new question, carry Phase 1 context for SQL gen."""
    follow_up_q = _extract_followup_question(message)

    # Clear Phase 1 SQL artifacts; keep relevant_tables as merge hints.
    # Also clear similar_questions so Phase 1 VDB-retrieved examples don't bleed in —
    # Phase 1 context is injected explicitly via the follow-up instruction block instead.
    for key in (
        "normalized_question", "sql_code", "sql_generation_result",
        "error", "sql_attempts", "reconstruction_count",
        "error_analysis_done", "_resume_from",
        "final_response", "sql_response_from_db",
        "similar_questions",
    ):
        session.path_state.pop(key, None)

    # Carry the full Phase 1 KB union into Phase 2 Evidence generation,
    # then reset so Phase 2 accumulates its own entries fresh.
    session.phase1_grounded_kg = session.cumulative_grounded_kg
    session.cumulative_grounded_kg = ""

    # Reset clarify state for Phase 2; clarification questions are not allowed.
    session.working_question = follow_up_q if follow_up_q else session.working_question
    session.original_question = session.working_question
    session.clarify_history = []
    session._pending_question = None
    session.max_clarify_turns = 0
    logger.info(
        "[%s] Phase 2 Query: \033[1;35m%s\033[0m",
        session.task_id,
        session.working_question,
    )


# ── Cross-phase entity resolution ───────────────────────────────────────────

_CROSS_PHASE_PROMPT = """\
Phase 2 question: {p2_question}

Terms from Phase 2 that could not be resolved from the database schema or \
external knowledge:
{unresolved_list}

Phase 1 question (for context):
{p1_question}

Phase 1 SQL:
{p1_sql}

For each unresolved Phase 2 term that refers to a concept or formula defined \
in Phase 1, write one SQL-friendly line:
  TermInPhase2 = <formula or definition derived from Phase 1>
Include threshold conditions if relevant (e.g. Low/Medium/High cutoffs). \
Output NONE if none of the unresolved terms map to Phase 1 concepts."""


def _resolve_cross_phase_entities(
    p2_question: str,
    unresolved: list,
    p1_question: str,
    p1_sql: str,
) -> str:
    """Map unresolved Phase 2 entities to Phase 1 definitions (one fast LLM call)."""
    if not p1_sql or not p1_question or not unresolved:
        return ""
    unresolved_list = "\n".join(f"- {name}" for name, _ in unresolved)
    prompt = _CROSS_PHASE_PROMPT.format(
        p2_question=p2_question,
        unresolved_list=unresolved_list,
        p1_question=p1_question,
        p1_sql=p1_sql[:800],
    )
    response = safe_invoke_text(_get_llm(), prompt).strip()
    if not response or response.upper() == "NONE":
        return ""
    logger.info("Cross-phase resolution: %s", response[:200])
    return response


# ── SQL generation ──────────────────────────────────────────────────────────

_FOLLOW_UP_INSTRUCTION = """\

[Follow-up context]
This question is a follow-up on the same database. The previous question and its SQL \
are provided for reference — use them as directly relevant or as background context \
depending on what this question asks.

Previous question: {p1_question}
Previous SQL:
{p1_sql}"""

_EVIDENCE_PROMPT = """\
Working question: {question}

Relevant external knowledge (one entry per term):
{grounded_kg}

Extract the formulas, calculation rules, and threshold/filter conditions that are \
directly needed to answer the working question above. For each such entry, output one \
line in SQL-friendly notation:
  TermName = <formula, threshold, or filter condition using column names and values>
Only include conditions expressible with specific column names and values — skip \
natural-language qualifiers with no clear SQL translation. \
Skip any entry not required by the working question. \
Entries may include a "# matched from: <terms>" annotation line listing the original \
natural-language phrases from the question that correspond to this KB entry — use these \
to connect KB entries to the working question even when the phrasing differs. \
If nothing applies, output: NONE"""


# Matches a column name in parentheses: (battlifeh), (pwractmw)
_PAREN_COL_RE = re.compile(r'\(([a-zA-Z][a-zA-Z0-9_]*)\)')
# Matches explicit "column <name>" or 'column "name"' or "column 'name'"
_KEYWORD_COL_RE = re.compile(r'\bcolumns?\s+["\']?([a-zA-Z][a-zA-Z0-9_]+)["\']?', re.IGNORECASE)
# Strict score threshold for exact column name lookup (lower = closer match)
_NAMED_COL_SCORE_THRESHOLD = 0.45


def _detect_and_resolve_named_columns(session: InteractiveSessionState, answer: str) -> None:
    """Extract explicit column names from a user answer and resolve them to schema entries.

    Detects two patterns:
    - Parenthetical: "battery life in hours (battlifeh)"
    - Keyword: "stored in the column dogs" / 'column "pwractmw"'

    For each candidate, runs a VDB lookup. On a confident hit, injects a direct
    "column_name → <schema description>" line into session._named_column_evidence
    so the SQL generator knows which table the column belongs to.
    """
    if session.semantic_retriever is None:
        return

    candidates: set[str] = set()
    for m in _PAREN_COL_RE.finditer(answer):
        candidates.add(m.group(1))
    for m in _KEYWORD_COL_RE.finditer(answer):
        candidates.add(m.group(1))

    if not candidates:
        return

    already = session._named_column_evidence
    for col in candidates:
        if col in already:
            continue
        try:
            hits = search_semantic_index(
                session.semantic_retriever, col, [LABEL_COLUMN_ATTRIBUTE], 1, session.db_name
            )
        except Exception:
            continue
        if not hits:
            continue
        score = hits[0].get("score", 1.0)
        if score > _NAMED_COL_SCORE_THRESHOLD:
            continue
        hit_text = hits[0].get("text", "")
        entry = f"{col} → {hit_text}"
        logger.info("Named column resolved: %r (score=%.3f) → %s", col, score, hit_text[:120])
        session._named_column_evidence = (
            already + "\n" + entry if already else entry
        )
        already = session._named_column_evidence


def _generate_evidence(question: str, grounded_kg: str) -> str:
    """Convert grounded KB text into a short Evidence string for the SQL generator."""
    if not grounded_kg:
        return ""
    prompt = _EVIDENCE_PROMPT.format(question=question, grounded_kg=grounded_kg)
    response = safe_invoke_text(_get_fast_llm(), prompt).strip()
    if not response or response.upper() == "NONE":
        return ""
    # Valid output is "Term = <expression>" anchored at the start of the line.
    # The old "=" in l" check passed long prose lines that contained "=" anywhere.
    _FORMULA_LINE = re.compile(r"^\s*[\w][\w\s/()-]*\s*=\s*\S")
    _AGG_ONLY = re.compile(r"^\s*[\w][\w\s/()-]*\s*=\s*(STDDEV|AVG|COUNT|SUM|MIN|MAX)\s*\(", re.IGNORECASE)
    valid_lines = [
        l for l in response.splitlines()
        if _FORMULA_LINE.match(l) and not l.lstrip().startswith("#") and not _AGG_ONLY.match(l)
    ]
    if not valid_lines:
        logger.warning("SQL gen — Evidence generation returned prose, discarding: %s", response[:100])
        return ""
    return "\n".join(valid_lines)


def _run_sql_generation(session: InteractiveSessionState) -> str:
    """Call GSF and persist the returned path_state back to session."""
    from gsf.retrieval.text_to_sql.main import get_agent_response_with_state
    from gsf.retrieval.text_to_sql.state import TextToSQLPayload

    # For debug turns that skip clarification, run grounding now to get KB context.
    # For normal turns, cumulative_grounded_kg already has everything from clarification.
    extra_kg = ""
    if session._grounded_kg_for != session.working_question:
        expanded_kg = expand_kg_with_children(session.external_kg, session.external_kg_children_map)
        extra_kg = ground_external_knowledge(
            session.working_question, expanded_kg, _get_fast_llm()
        )

    # Cross-phase resolution: map unresolved Phase 2 entities to Phase 1 formulas.
    # Only runs when there's a Phase 1 SQL to reference and Phase 2 unresolved entities.
    p1_sql = session.phase1_sql or ""
    p1_question = session.phase1_question or ""
    cross_phase = _resolve_cross_phase_entities(
        session.working_question,
        session._cached_unresolvable or [],
        p1_question,
        p1_sql,
    )

    # Build Evidence from the union of: Phase 1 carry-over + cross-phase resolution
    # + all this-phase KB turns + debug extra.
    # VDB resolved hits are column descriptions, not formulas — the SQL generator
    # rediscovers schema mappings via its own VDB; they only benefit the decide-LLM prompt.
    combined_kg = "\n".join(filter(None, [session.phase1_grounded_kg, cross_phase, session.cumulative_grounded_kg, extra_kg]))
    question = session.working_question
    evidence = _generate_evidence(question, combined_kg)
    if session._named_column_evidence:
        evidence = "\n".join(filter(None, [evidence, session._named_column_evidence]))
    if evidence:
        question = f"{question}\n\nEvidence: {evidence}"
        logger.info("[%s] SQL gen — Evidence: %s", session.task_id, evidence[:200])

    # For Phase 2, append an explicit follow-up instruction block so the SQL generator
    # knows to extend or filter Phase 1's SQL rather than starting from scratch.
    if p1_sql and p1_question:
        question = question + _FOLLOW_UP_INSTRUCTION.format(
            p1_question=p1_question,
            p1_sql=p1_sql,
        )
        logger.info("[%s] SQL gen — follow-up instruction injected (p1 sql %d chars)", session.task_id, len(p1_sql))

    payload: TextToSQLPayload = {
        "question": question,
        "data_retriever": session.data_retriever,
        "semantic_retriever": session.semantic_retriever,
        "connectors": session.connectors,
        "path_state": dict(session.path_state),  # copy so GSF doesn't mutate in place
        "acronyms": [],
        "custom_prompts": combined_kg,  # full union as low-priority fallback
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
    external_kg_children_map: dict | None = None,
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
        external_kg_children_map=external_kg_children_map or {},
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
                logger.info(
                    "[%s] Query: \033[1;35m%s\033[0m",
                    session.task_id,
                    extracted,
                )

    elif turn_type == TurnType.FOLLOW_UP:
        if session.phase not in (
            InteractivePhase.PHASE2_CLARIFY,
            InteractivePhase.PHASE2_SUBMIT,
            InteractivePhase.PHASE2_DEBUG,
        ):
            _apply_follow_up_seed(session, orchestrator_message)
            session.phase = InteractivePhase.PHASE2_CLARIFY

    elif turn_type == TurnType.DEBUG:
        _apply_debug_seed(session, orchestrator_message)

    under_budget = len(session.clarify_history) < session.max_clarify_turns
    if turn_type != TurnType.DEBUG and under_budget:
        should_ask, question = should_clarify(session, _get_llm())
        if should_ask:
            session._pending_question = question
            return AskUserAction(question=question)
    elif turn_type != TurnType.DEBUG and not under_budget:
        # No clarification allowed (Phase 2 or budget exhausted) but still run
        # KB coverage to update cumulative_grounded_kg for Evidence generation.
        refresh_grounded_kg(session)

    sql = _run_sql_generation(session)
    return SubmitSQLAction(sql=sql)


def apply_user_answer(session: InteractiveSessionState, answer: str) -> None:
    """Record user answer and merge into working question."""
    if session._pending_question:
        session.clarify_history.append({"q": session._pending_question, "a": answer})
        session._pending_question = None
    answer_lower = answer.lower()
    user_could_not_answer = any(phrase in answer_lower for phrase in _STUCK_PHRASES)
    if not user_could_not_answer:
        last_turn = session.clarify_history[-1]
        relevant_kg = session._grounded_kg or ""

        with ThreadPoolExecutor(max_workers=3) as pool:
            merge_future = pool.submit(
                merge_clarification,
                session.working_question,
                last_turn,
                _get_fast_llm(),
                relevant_kg=relevant_kg,
            )
            completeness_future = pool.submit(
                detect_incomplete_formulas,
                session.working_question,
                last_turn,
                relevant_kg,
                list(session.incomplete_formula_terms),
                _get_llm(),
            )
            prune_future = pool.submit(
                prune_resolved_terms,
                list(session.persistent_unresolved),
                last_turn,
                _get_fast_llm(),
            )
            merged = merge_future.result()
            gaps = completeness_future.result()
            pruned = prune_future.result()
            newly_resolved = set(session.persistent_unresolved) - set(pruned)
            session.resolved_persistent.update(newly_resolved)
            session.persistent_unresolved = pruned

        _detect_and_resolve_named_columns(session, last_turn["a"])

        if merged:
            session.working_question = merged
        else:
            logger.warning("[%s] merge_clarification returned empty; keeping previous question", session.task_id)
        logger.info("[%s] Merged question: %s", session.task_id, session.working_question)

        session.incomplete_formula_terms = gaps
        logger.info("[%s] Incomplete formula terms: %s", session.task_id, gaps)


def apply_submit_result(session: InteractiveSessionState, result: dict) -> None:
    """Update session with Bird's submit response for future debug seeding."""
    session.latest_feedback = result.get("message", "")
    # Save Phase 1 artifacts only on successful completion so they don't
    # contaminate Phase 1 debug turns with follow-up instruction / cross-phase logic.
    if session.phase1_question is None and result.get("phase_completed") == 1:
        session.phase1_question = session.working_question
        session.phase1_sql = session.path_state.get("sql_code", "")
