from __future__ import annotations

import logging
import re
from typing import Any, Union

logger = logging.getLogger(__name__)

# NOTE: get_agent_response_with_state and TextToSQLPayload are imported lazily
# inside _run_sql_generation to avoid triggering LLM client initialisation at
# import time (which requires NVIDIA_API_KEY to be set).
from concurrent.futures import ThreadPoolExecutor

from .clarify import (
    should_clarify,
    refresh_grounded_kg,
    prune_resolved_terms,
    _STUCK_PHRASES,
    should_inject_default_sort,
    _DEFAULT_SORT_HINT,
    _format_resolved_schema_terms,
)
from .kg_coverage import expand_kg_with_children
from .output_type import (
    output_type_enabled,
    should_skip_output_type_question,
    OUTPUT_TYPE_QUESTION,
    SCALAR_HINT,
)
from .conditional_output import conditional_output_enabled, get_conditional_output_hint
from .completeness import detect_incomplete_formulas
from .evidence import (
    build_grounded_terms_hint,
    detect_and_resolve_named_columns,
    generate_evidence,
)
from .followup_merge import merge_follow_up_question
from .grounding import ground_external_knowledge
from .merge import merge_clarification
from .types import AskUserAction, InteractivePhase, SubmitSQLAction, TurnType
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
    if "Your SQL is not executable:" in message:
        # Extract the actual DB error from Bird's message and inject it for reconstruction.
        after = message.split("Your SQL is not executable:", 1)[1].strip()
        actual_error = after.split("\n")[0].strip()
        session.path_state["error"] = actual_error
        logger.info("Debug seed: execution error → %s", actual_error[:200])
    else:
        # SQL ran but results didn't match — no execution error detail available.
        # Inject targeted hints based on observed failure patterns in this benchmark.
        session.path_state["error"] = (
            "The SQL produced incorrect results. "
            "Column and field names are likely correct — focus on how tables are joined "
            "or how JSON objects are traversed, not on renaming. "
            "Do NOT modify formula coefficients, formula structure, or aggregation logic — "
            "these were confirmed during clarification. "
            "Address whichever of the following applies, or fix a different issue you identify:\n\n"
            "1. COMPOUND FILTER COMPLETENESS: Re-check the evidence and question for "
            "every condition they imply, not just the primary one. Common drops: an "
            "IS NOT NULL guard on a column feeding an aggregate or ratio, a "
            "positivity/non-zero guard on a denominator, a second threshold in a "
            "compound AND/OR, an exact value or unit filter (e.g. status = 'Completed', "
            "unit = 'year'). If the evidence lists multiple conditions, verify all of "
            "them are still present in your SQL.\n\n"
            "2. OUTPUT GRAIN AND ROW SELECTION: Check whether the question wants one "
            'row per group or a single aggregate/top-1 row, and whether "most '
            'recent"/"latest snapshot"/dedup logic is implied — an extra join or an '
            "extra GROUP BY column can silently fan out or split a group that should "
            'stay merged. If the question asks for top-N or "the one with the highest '
            "X,\" add LIMIT N. Judge from the question's phrasing whether unmatched "
            "primary-entity rows should still appear."
        )
        logger.info("Debug seed: wrong results — injecting targeted benchmark hints")
    # Preserve Phase 1's reconstruction lineage as read-only prompt context
    # before wiping "failed_attempts" below — see sql_reconstruction.py's
    # history_section, which renders "phase1_failed_attempts" alongside this
    # turn's own attempts so the debug turn's LLM can see e.g. "you already
    # tried the flattened JSONB key and were told it was wrong" instead of
    # reconstructing from scratch with no memory of Phase 1's fixes. This is
    # deliberately a *separate* key from "failed_attempts": the routers in
    # text_to_sql_graph.py (route_sql_validation, _make_soft_check_router)
    # gate on len(failed_attempts), so carrying the phase 1 list forward
    # under that same key would eat into the fresh reconstruction budget the
    # counter-reset below exists to guarantee. Content and count are tracked
    # separately on purpose.
    phase1_failed_attempts = session.path_state.get("failed_attempts")
    if phase1_failed_attempts:
        session.path_state["phase1_failed_attempts"] = phase1_failed_attempts

    session.path_state["sql_attempts"] = 0
    session.path_state["reconstruction_count"] = 0
    session.path_state["error_analysis_done"] = False
    # "failed_attempts" backs route_sql_validation's skip_intent_validation
    # check (len(failed_attempts) > 5) — it must reset here too, or a debug
    # turn inherits the attempt count from the PRIOR turn and can skip
    # intent validation almost immediately, cutting off the fresh repair
    # budget this turn is supposed to get.
    #
    # "jsonb_path_repair_attempted" is JsonbPathCheckAgent's one-shot guard
    # (see jsonb_path_check.py) — it stays True forever once a path is fixed
    # once in this phase, so without popping it here the debug turn's
    # from-scratch reconstruction (which can reintroduce the exact class of
    # bug the check already fixed, e.g. flattening a nested JSONB key back
    # out) gets zero path-check coverage instead of the one fresh pass every
    # other repair guard below already gets. "join_path_repair_attempts" is
    # the analogous counter for JoinPathCheckAgent — same reasoning.
    for key in (
        "repair_attempted",
        "value_repair_done",
        "failed_attempts",
        "jsonb_path_repair_attempted",
        "join_path_repair_attempts",
    ):
        session.path_state.pop(key, None)


def _apply_follow_up_seed(session: InteractiveSessionState, message: str) -> None:
    """Prepare session for Phase 2: new question, carry Phase 1 context for SQL gen."""
    follow_up_q = _extract_followup_question(message)

    # Clear Phase 1 SQL artifacts; keep relevant_tables as merge hints.
    # Also clear similar_questions so Phase 1 VDB-retrieved examples don't bleed in —
    # Phase 1 context is injected explicitly via the follow-up instruction block instead.
    for key in (
        "normalized_question",
        "sql_code",
        "sql_generation_result",
        "error",
        "sql_attempts",
        "reconstruction_count",
        "error_analysis_done",
        "_resume_from",
        "final_response",
        "sql_response_from_db",
        "similar_questions",
    ):
        session.path_state.pop(key, None)

    # Carry the full Phase 1 KB union into Phase 2 Evidence generation,
    # then reset so Phase 2 accumulates its own entries fresh.
    session.phase1_grounded_kg = session.cumulative_grounded_kg
    session.cumulative_grounded_kg = ""
    # Phase 2 asks a different question — don't carry Phase 1's cross-turn
    # KB/VDB disambiguation bookkeeping into it (see clarify.py).
    session._ever_kb_covered_norms = set()
    session._ever_vdb_hit_norms = {}
    session._ever_term_to_kb_entry = {}
    # Same reasoning for collision-resolution notes: unlike _collision_hit_verdicts
    # (keyed by stable column id — a schema fact, still valid across phases),
    # these are injected verbatim into Evidence and reference Phase 1's specific
    # entities/columns, which are usually irrelevant to Phase 2's question.
    session._json_shared_notes = []

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


# ── SQL generation ──────────────────────────────────────────────────────────


def _run_sql_generation(session: InteractiveSessionState) -> str:
    """Call GSF and persist the returned path_state back to session."""
    from gsf.retrieval.text_to_sql.main import get_agent_response_with_state
    from gsf.retrieval.text_to_sql.state import TextToSQLPayload

    # For debug turns that skip clarification, run grounding now to get KB context.
    # For normal turns, cumulative_grounded_kg already has everything from clarification.
    extra_kg = ""
    if session._grounded_kg_for != session.working_question:
        expanded_kg = expand_kg_with_children(
            session.external_kg, session.external_kg_children_map
        )
        extra_kg = ground_external_knowledge(
            session.working_question, expanded_kg, _get_fast_llm()
        )

    p1_sql = session.phase1_sql or ""
    p1_question = session.phase1_question or ""

    # Build Evidence from the union of: Phase 1 carry-over + this-phase KB turns + debug extra.
    # VDB resolved hits are column descriptions, not formulas — the SQL generator
    # rediscovers schema mappings via its own VDB; they only benefit the decide-LLM prompt.
    combined_kg = "\n".join(
        filter(
            None, [session.phase1_grounded_kg, session.cumulative_grounded_kg, extra_kg]
        )
    )

    # For Phase 2, rewrite the follow-up into a self-contained question that bakes in
    # column names and conditions from Phase 1 SQL so the SQL generator doesn't have to
    # guess the relationship between the two phases. Persist as working_question so debug
    # re-runs and logging reflect the enriched question.
    # Evidence extraction uses the merged question so it is calibrated to the same
    # question the SQL generator receives, rather than the shorter raw follow-up.
    if p1_sql and p1_question:
        merged_q = merge_follow_up_question(
            p1_question, p1_sql, session.working_question
        )
        session.working_question = merged_q
        logger.info(
            "[%s] SQL gen — follow-up merged question (p1 sql %d chars)",
            session.task_id,
            len(p1_sql),
        )
    evidence_question = session.working_question

    question = session.working_question
    # For Phase 2, append the raw Phase 1 SQL as an exact reference so numeric thresholds,
    # CASE conditions, and formulas are preserved verbatim — the merged question captures
    # intent and structure, but the raw SQL is the source of truth for precise values.
    if p1_sql and p1_question:
        question = (
            f"{question}\n\n[Phase 1 SQL reference — use exact column names, "
            f"thresholds, and formulas from this SQL where applicable]\n{p1_sql}"
        )

    resolved_terms_section = build_grounded_terms_hint(session)
    evidence = generate_evidence(evidence_question, combined_kg, resolved_terms_section)
    if session._named_column_evidence:
        evidence = "\n".join(filter(None, [evidence, session._named_column_evidence]))
    if session._json_shared_notes:
        # Injected directly rather than left to evidence-gen's LLM to relay —
        # that step isn't reliable about preserving instructions passed through it.
        evidence = "\n".join(filter(None, [evidence, *session._json_shared_notes]))
        logger.info(
            "[%s] SQL gen — injected %d shared-JSON-column note(s)",
            session.task_id,
            len(session._json_shared_notes),
        )
    if session._ambiguity_resolution_notes:
        # Same reasoning as _json_shared_notes above — injected directly, not
        # left to evidence-gen's LLM to relay.
        evidence = "\n".join(
            filter(None, [evidence, *session._ambiguity_resolution_notes])
        )
        logger.info(
            "[%s] SQL gen — injected %d ambiguity-resolution note(s)",
            session.task_id,
            len(session._ambiguity_resolution_notes),
        )
    if should_inject_default_sort(session.working_question):
        evidence = "\n".join(filter(None, [evidence, _DEFAULT_SORT_HINT]))
        logger.info("[%s] SQL gen — injected default DESC sort hint", session.task_id)
    if session.scalar_hint:
        evidence = "\n".join(filter(None, [evidence, SCALAR_HINT]))
        logger.info("[%s] SQL gen — injected scalar aggregate hint", session.task_id)
    if conditional_output_enabled():
        _cond_hint = get_conditional_output_hint(session.working_question)
        if _cond_hint:
            evidence = "\n".join(filter(None, [evidence, _cond_hint]))
            logger.info(
                "[%s] SQL gen — injected conditional output hint", session.task_id
            )
    if evidence:
        question = f"{question}\n\nEvidence: {evidence}"
        logger.info("[%s] SQL gen — Evidence: %s", session.task_id, evidence)

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
        path_state={"target_db": db_name, "task_id": task_id},
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

                # ── Output-type check (runs once, before clarify sees anything) ──
                # Pure regex — no LLM call. Fires only on the very first step
                # (clarify_history is empty). If the type is ambiguous, we ask the
                # user before any other clarification question. If it's obvious,
                # we set scalar_hint (or skip silently for table/ddl) and continue.
                if output_type_enabled():
                    skip = should_skip_output_type_question(extracted)
                    logger.info(
                        "[%s] OutputType check → %s",
                        session.task_id,
                        skip if skip is not None else "None (will ask)",
                    )
                    if skip is None:
                        session._pending_question = OUTPUT_TYPE_QUESTION
                        return AskUserAction(question=OUTPUT_TYPE_QUESTION)
                    elif skip == "scalar":
                        session.scalar_hint = True
                        logger.info(
                            "[%s] OutputType: scalar hint set at session start",
                            session.task_id,
                        )
                    # "table" and "ddl" → skip silently, no hint needed

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

    # ── Per-turn output-type re-check (pure regex, no LLM) ───────────────────
    # Re-runs on the enriched working_question each turn so that intent revealed
    # naturally in dialogue (without an explicit output-type question) still sets
    # the hint. One-way only: False → True; never unsets once set.
    if output_type_enabled() and not session.scalar_hint:
        if should_skip_output_type_question(session.working_question) == "scalar":
            session.scalar_hint = True
            logger.info(
                "[%s] OutputType: scalar hint set from enriched question (turn %d)",
                session.task_id,
                len(session.clarify_history),
            )

    if turn_type != TurnType.DEBUG and under_budget:
        should_ask, question = should_clarify(session, _get_llm())
        if should_ask:
            session._pending_question = question
            return AskUserAction(question=question)
    elif turn_type != TurnType.DEBUG and not under_budget:
        # No clarification allowed (Phase 2 or budget exhausted) but still run
        # KB coverage to update cumulative_grounded_kg for Evidence generation.
        refresh_grounded_kg(session)

    # Decision to proceed (whatever the reason — turn budget exhausted, or
    # should_clarify judging PROCEED above) — resolve any still-open ambiguous
    # entities from the final working_question, so the SQL generator gets a real
    # decision instead of the vague "choose whichever fits" note. Gated on the
    # proceed decision itself, not on turn count, so it isn't tied to BIRD-Interact's
    # max_clarify_turns mechanic specifically.
    #
    # _ambiguity_resolved guards this to run once per *phase*, not once per
    # handle_turn call: a DEBUG turn (SQL rejected, "fix it") re-enters this same
    # function and falls through to _run_sql_generation again after the flag is
    # already True from the first pass, so it correctly skips re-resolving —
    # there's nothing turn-loop-specific about the guard itself, it's the same
    # "already did this, don't repeat" flag as any one-shot-per-phase step.
    if not session._ambiguity_resolved:
        session._ambiguity_resolved = True
        # Collision resolution (clarify._resolve_collisions, runs earlier in the
        # same _find_unresolvable_entities call this turn — see entity_resolution.py)
        # may have already settled some of these same entities on its own, more
        # mature logic (KB-covered exclusion, composite-column handling, LLM
        # fallback). Its outcome lands in _cached_resolved_hits. Without this
        # check, both mechanisms could independently decide the same term and,
        # if they disagree, inject contradicting notes into evidence with no
        # arbitration between them — whichever landed later in the text would
        # win by accident. Defer to collision's answer where one already exists.
        already_resolved = {
            entity for entity, *_ in session._cached_resolved_hits or []
        }
        pending = {
            e: hits
            for e, hits in session._last_ambiguous_hits.items()
            if e not in already_resolved
        }
        if pending:
            from .entity_resolution import resolve_pending_ambiguities

            session._ambiguity_resolution_notes = resolve_pending_ambiguities(
                session.working_question,
                session.cumulative_grounded_kg,
                pending,
            )
            if session._ambiguity_resolution_notes:
                logger.info(
                    "[%s] SQL gen — resolved %d pending ambiguous entit(y/ies)",
                    session.task_id,
                    len(session._ambiguity_resolution_notes),
                )

    sql = _run_sql_generation(session)
    return SubmitSQLAction(sql=sql)


def apply_user_answer(session: InteractiveSessionState, answer: str) -> None:
    """Record user answer and merge into working question."""
    if session._pending_question:
        session.clarify_history.append({"q": session._pending_question, "a": answer})
        session._pending_question = None
    answer_lower = answer.lower()
    user_could_not_answer = any(phrase in answer_lower for phrase in _STUCK_PHRASES)
    if not user_could_not_answer and session.clarify_history:
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
                None,
                _format_resolved_schema_terms(session._cached_resolved_hits or []),
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

            if session._cached_vdb_only_norms:
                remaining_vdb = prune_resolved_terms(
                    sorted(session._cached_vdb_only_norms),
                    last_turn,
                    _get_fast_llm(),
                )
                session._cached_vdb_only_norms = set(remaining_vdb)

        detect_and_resolve_named_columns(session, last_turn["a"])

        if merged:
            session.working_question = merged
        else:
            logger.warning(
                "[%s] merge_clarification returned empty; keeping previous question",
                session.task_id,
            )
        logger.info(
            "[%s] Merged question: %s", session.task_id, session.working_question
        )

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
