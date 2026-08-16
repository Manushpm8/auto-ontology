from __future__ import annotations

import logging
import re
from typing import Any, Union

logger = logging.getLogger(__name__)

# NOTE: get_agent_response_with_state and TextToSQLPayload are imported lazily
# inside _run_sql_generation to avoid triggering LLM client initialisation at
# import time (which requires NVIDIA_API_KEY to be set).
from concurrent.futures import ThreadPoolExecutor

from .clarify import should_clarify, refresh_grounded_kg, prune_resolved_terms, expand_kg_with_children, _STUCK_PHRASES, should_inject_default_sort, _DEFAULT_SORT_HINT, _format_resolved_schema_terms
from .output_type import output_type_enabled, should_skip_output_type_question, OUTPUT_TYPE_QUESTION, SCALAR_HINT
from .conditional_output import conditional_output_enabled, get_conditional_output_hint
from gsf.retrieval.data_access.semantic_search import search_semantic_index
from gsf.semantic.constants import LABEL_COLUMN_ATTRIBUTE
from .completeness import detect_incomplete_formulas
from .grounding import ground_external_knowledge
from .merge import merge_clarification
from .types import AskUserAction, InteractivePhase, SubmitSQLAction, TurnType
from .state import InteractiveSessionState
from gsf.utils.llm_invoke import safe_invoke_text, safe_invoke_text_nr


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
            "1. JOIN PATH (only if the current join path seems semantically wrong):"
            "You may be joining tables too directly. "
            "Check whether an intermediate table is required — "
            "a direct join may need to route through a third table. "
            "Verify the exact foreign key column names on each side.\n\n"
            "2. LIMIT / ORDER BY: If the question asks for top-N results, add LIMIT N. "
            "If an ORDER BY is present, verify it sorts by the column or expression "
            "the question actually requests.\n\n"
            "3. JSONB PATH: If accessing a JSONB column, verify the path and key name "
            "are correct — keys are typically short and abbreviated, and may be nested "
            "within intermediate objects."
        )
        logger.info("Debug seed: wrong results — injecting targeted benchmark hints")
    session.path_state["sql_attempts"] = 0
    session.path_state["reconstruction_count"] = 0
    session.path_state["error_analysis_done"] = False
    # "failed_attempts" backs route_sql_validation's skip_intent_validation
    # check (len(failed_attempts) > 5) — it must reset here too, or a debug
    # turn inherits the attempt count from the PRIOR turn and can skip
    # intent validation almost immediately, cutting off the fresh repair
    # budget this turn is supposed to get.
    for key in ("repair_attempted", "value_repair_done", "failed_attempts"):
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

_FOLLOW_UP_MERGE_PROMPT = """\
You are rewriting a follow-up database question into a clear, self-contained question \
for a SQL generator.

Follow-up question:
{p2_question}

Previous question (Phase 1):
{p1_question}
Previous SQL:
{p1_sql}
{cross_phase_section}


Task: Rewrite the follow-up question into a single, complete, standalone question \
for the SQL generator. Resolve any underspecified terms, references, or concepts \
in the follow-up using the previous question and SQL — pull in the exact column names, \
table names, formulas, thresholds, and conditions that the follow-up depends on. \
Include as much or as little of the previous SQL's structure as the follow-up requires.

Rules:
- Resolve references to prior concepts (e.g. "that category", "the same score", "those \
signals") using the previous SQL and question. If resolved mappings are provided above, \
use them as the authoritative definition for any matching terms.
- Carry forward table names, column names, formulas, tresholds and conditions that the follow-up \
references or implicitly depends on. Carry forward exact numeric values, if they exist.
- If the follow-up reuses or extends the previous query's full structure, incorporate it. \
If it only borrows part of it, incorporate only that part.
- For any concept or metric in the follow-up that does not clearly map 1:1 to a term \
in the previous SQL, do NOT assign it to a table or column — leave it unresolved so \
the SQL generator can discover it from the schema. Only carry forward table/column \
assignments for concepts explicitly present in the previous SQL.
- Output only the rewritten question, no preamble or explanation."""


def _merge_follow_up_question(
    p1_question: str,
    p1_sql: str,
    cross_phase: str,
    p2_question: str,
) -> str:
    """Rewrite a raw follow-up question into a self-contained question with column
    names and conditions drawn from the Phase 1 SQL and any cross-phase resolutions."""
    cross_phase_section = (
        f"\nResolved mappings for terms in the follow-up:\n{cross_phase}\n"
        if cross_phase
        else ""
    )
    prompt = _FOLLOW_UP_MERGE_PROMPT.format(
        p1_question=p1_question,
        p1_sql=p1_sql[:800],
        cross_phase_section=cross_phase_section,
        p2_question=p2_question,
    )
    merged = safe_invoke_text_nr(prompt).strip()
    if not merged:
        logger.warning("Follow-up merge returned empty; falling back to raw follow-up question")
        return p2_question
    logger.info("Follow-up merged question: %s", merged)
    return merged

_EVIDENCE_PROMPT = """\
Working question: {question}

Relevant external knowledge (one entry per term):
{grounded_kg}
{resolved_terms_section}
Extract the formulas, calculation rules, and threshold/filter conditions that are \
directly needed to answer the working question above. For each such entry, output one \
line in SQL-friendly notation:
  TermName = <formula, threshold, or filter condition using column names and values>
Only include conditions expressible with specific column names and values — skip \
natural-language qualifiers with no clear SQL translation. \
Never invent a column-like name (Title_Case/snake_case) for a term with no confirmed \
mapping. If no term in a formula has a confirmed mapping, omit the line entirely. If \
only some terms are confirmed, keep the formula structure and substitute \
[UNRESOLVED: <term>] — using the term's exact original wording from the question — \
for each unconfirmed operand, never a name that could pass as a real column. \
Skip any entry not required by the working question. \
Entries may include a "# matched from: <terms>" annotation line listing the original \
natural-language phrases from the question that correspond to this KB entry — use these \
to connect KB entries to the working question even when the phrasing differs. \
If an entry is marked [DISAMBIGUATION], it means a KB formula and a direct schema column \
both matched the same term — include only whichever is correct given the question context. \
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


def _build_grounded_terms_hint(session: InteractiveSessionState) -> str:
    """Surface already-resolved schema mappings for terms VDB-matched to the
    current question, and flag when two different terms resolved to the same
    column, so the evidence LLM grounds formula terms in real columns instead
    of guessing table/column names — and can correctly skip a term instead of
    inventing one when nothing is confirmed.
    """
    hits = session._cached_resolved_hits or []
    if not hits:
        return ""

    # Keep the best (lowest-distance) hit per term.
    best: dict[str, tuple[str, float, str]] = {}
    for norm, hit_text, score, hit_id in hits:
        if norm not in best or score < best[norm][1]:
            best[norm] = (hit_text, score, hit_id)
    if not best:
        return ""

    # Collision check: two distinct terms resolving to the same underlying
    # attribute node is a sign the match is unreliable (e.g. two near-synonym
    # terms colliding on one column) — flag it instead of presenting both as
    # confident hits. Keyed on hit_id (the attribute node's identity), not
    # hit_text (a display label that isn't guaranteed unique or consistently
    # formatted across hits) — falls back to hit_text only when hit_id is
    # missing.
    target_to_terms: dict[str, list[str]] = {}
    for norm, (hit_text, _score, hit_id) in best.items():
        key = hit_id or hit_text
        target_to_terms.setdefault(key, []).append(norm)

    lines = []
    for norm, (hit_text, _score, hit_id) in best.items():
        key = hit_id or hit_text
        others = [t for t in target_to_terms[key] if t != norm]
        if others:
            logger.info(
                "Evidence — term collision: %r and %s share target %r",
                norm, others, hit_text,
            )
        suffix = (
            f'  [TERM COLLISION: also matched by "{", ".join(others)}" — '
            f"unreliable, do not assume this mapping is correct]"
            if others
            else ""
        )
        lines.append(f'  "{norm}" → {hit_text}{suffix}')
    return (
        "\nConfirmed schema mappings for terms VDB-matched to the question "
        "(terms not listed here have no confirmed mapping; a term marked TERM "
        "COLLISION matched the same column as another term and should not be "
        "trusted without other confirmation):\n" + "\n".join(lines) + "\n"
    )


def _generate_evidence(question: str, grounded_kg: str, resolved_terms_section: str = "") -> str:
    """Convert grounded KB text into a short Evidence string for the SQL generator."""
    if not grounded_kg:
        return ""
    prompt = _EVIDENCE_PROMPT.format(
        question=question, grounded_kg=grounded_kg, resolved_terms_section=resolved_terms_section
    )
    response = safe_invoke_text_nr(prompt).strip()
    logger.debug("SQL gen — Evidence raw response: %s", response)
    if not response or response.upper() == "NONE":
        return ""
    # Valid output is "Term = <expression>" anchored at the start of the line.
    # The old "=" in l" check passed long prose lines that contained "=" anywhere.
    _FORMULA_LINE = re.compile(r"^\s*[\w][\w\s/()-]*\s*=\s*\S")
    _CONTINUATION = re.compile(r"^\s+(AND|OR)\b", re.IGNORECASE)
    _AGG_ONLY = re.compile(r"^\s*[\w][\w\s/()-]*\s*=\s*(STDDEV|AVG|COUNT|SUM|MIN|MAX)\s*\(", re.IGNORECASE)
    # Join AND/OR continuation lines onto the preceding valid formula line before filtering,
    # so multi-condition expressions like "A = x AND y IN (...)" survive even if the LLM
    # wraps the second clause onto a new line.
    joined_lines: list[str] = []
    for line in response.splitlines():
        if _CONTINUATION.match(line) and joined_lines:
            joined_lines[-1] = joined_lines[-1].rstrip() + " " + line.strip()
        else:
            joined_lines.append(line)
    valid_lines = [
        l for l in joined_lines
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

    # Build Evidence from the union of: Phase 1 carry-over + this-phase KB turns + debug extra.
    # cross_phase is passed to the follow-up merge (below) rather than evidence, since it
    # feeds the question directly; adding it to evidence too would be redundant.
    # VDB resolved hits are column descriptions, not formulas — the SQL generator
    # rediscovers schema mappings via its own VDB; they only benefit the decide-LLM prompt.
    combined_kg = "\n".join(filter(None, [session.phase1_grounded_kg, session.cumulative_grounded_kg, extra_kg]))

    # For Phase 2, rewrite the follow-up into a self-contained question that bakes in
    # column names and conditions from Phase 1 SQL so the SQL generator doesn't have to
    # guess the relationship between the two phases. Persist as working_question so debug
    # re-runs and logging reflect the enriched question.
    # Evidence extraction uses the merged question so it is calibrated to the same
    # question the SQL generator receives, rather than the shorter raw follow-up.
    if p1_sql and p1_question:
        merged_q = _merge_follow_up_question(
            p1_question, p1_sql, cross_phase, session.working_question
        )
        session.working_question = merged_q
        logger.info("[%s] SQL gen — follow-up merged question (p1 sql %d chars)", session.task_id, len(p1_sql))
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

    resolved_terms_section = _build_grounded_terms_hint(session)
    evidence = _generate_evidence(evidence_question, combined_kg, resolved_terms_section)
    if session._named_column_evidence:
        evidence = "\n".join(filter(None, [evidence, session._named_column_evidence]))
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
            logger.info("[%s] SQL gen — injected conditional output hint", session.task_id)
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
