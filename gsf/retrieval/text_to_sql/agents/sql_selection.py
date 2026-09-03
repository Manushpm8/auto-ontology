# SPDX-FileCopyrightText: Copyright (c) 2026, NVIDIA CORPORATION & AFFILIATES.
# All rights reserved.
# SPDX-License-Identifier: Apache-2.0

"""
SQL Selection Agent (execution vote + optional answer rerank / LLM judge).

Given the pool of SQL candidates produced by SQLFromCandidatesAgent
(``path_state["sql_candidates"]``), execute each one, cluster candidates by
their result set, then:

1. If all successful candidates agree (single cluster) → that SQL wins.
2. If clusters disagree, pick among one representative per cluster using
   ``BIRD_SQL_SELECT``:
   - ``majority`` (default safe): largest execution cluster.
   - ``rerank``: NIM QA-reranker scores *(question, answer+SQL)* passages —
     the executed *response* is the primary signal (fits rerank-qa better
     than ranking SQL text alone). When the majority cluster is large
     (``BIRD_SQL_MAJORITY_LOCK_K``, default 3), rerank may override only if
     its top logit beats the majority passage by ``BIRD_SQL_RERANK_MARGIN``.
   - ``llm_judge``: GPT structured pick on result+SQL.
   Optional ``BIRD_SLOT_VOTE_WEIGHTS=solo_acc|voter_q`` scales each candidate's
   vote by that slot's held-out reliability (CV +0.7–0.85pp vs flat majority;
   hard slot removal does not CV-hold). Override table via
   ``BIRD_SLOT_VOTE_WEIGHTS_JSON='[0.73,0.71,...]'``.
3. On rerank/judge failure → fall back to majority.
4. Optional unanimous critic (``BIRD_UNANIMOUS_CRITIC=1``): when all successful
   candidates share one result, audit for concrete bugs and propose a challenger.
   Default is shadow mode (``BIRD_UNANIMOUS_CRITIC_SHADOW=1``): log only, do not
   override the selected SQL until offline evidence proves wins > losses.

When fewer than two candidates are present (the default, ``BIRD_NCAND=1``),
this node is a pass-through with zero execution overhead.

Every flag named above is declared with its default in :mod:`gsf.flags`.
"""

import json
import logging
from typing import Any, Dict, Optional

from langchain_core.messages import HumanMessage, SystemMessage
from pydantic import BaseModel, Field

from gsf import flags
from gsf.retrieval.data_access.custom_analyses import get_custom_analyses_ids
from gsf.retrieval.text_to_sql import empty_repair, projection_order, verify_revise
from gsf.retrieval.text_to_sql.agents.sql_execution import QueryResponse, _run_sql
from gsf.retrieval.text_to_sql.base import BaseAgent
from gsf.retrieval.text_to_sql.connector_routing import resolve_connector_from_tables
from gsf.retrieval.text_to_sql.evidence_hints import extract_evidence
from gsf.retrieval.text_to_sql.state import (
    AgentState,
    get_original_question,
    get_question_for_processing,
)
from gsf.utils.llm_invoke import get_llm_client, invoke_with_structured_output

logger = logging.getLogger(__name__)


def _resolve_select_mode() -> str:
    """``majority`` | ``rerank`` | ``llm_judge``."""
    return flags.sql_select_mode()


class _SQLJudgePick(BaseModel):
    """Structured response for multi-cluster SQL selection."""

    chosen_index: int = Field(
        description="The candidate index (from the options list) that best answers the question."
    )
    reason: str = Field(
        default="",
        description="One short sentence explaining the choice.",
    )


class _UnanimousCriticVerdict(BaseModel):
    """Structured audit for a unanimous (single-cluster) SQL result."""

    has_concrete_issue: bool = Field(
        description=(
            "True only when there is a specific, evidence-backed bug "
            "(extra filter, wrong attribute, wrong grain, bad denominator, "
            "decorative DISTINCT/CASE, etc.). False when the SQL looks fine."
        )
    )
    issue_type: str = Field(
        default="none",
        description=(
            "Short label when has_concrete_issue is true, e.g. unsupported_filter, "
            "wrong_attribute, wrong_grain, bad_denominator, decorative_distinct, "
            "over_join, projection_mismatch. Use 'none' when no issue."
        ),
    )
    explanation: str = Field(
        default="",
        description="One or two sentences naming the concrete bug and the fix.",
    )
    challenger_sql: str = Field(
        default="",
        description=(
            "A complete alternative SQL that fixes the concrete issue. "
            "Empty when has_concrete_issue is false."
        ),
    )


def _unanimous_critic_enabled() -> bool:
    return flags.UNANIMOUS_CRITIC()


def _unanimous_critic_shadow() -> bool:
    """Shadow mode logs challenger outcomes but keeps the original winner."""
    return flags.UNANIMOUS_CRITIC_SHADOW()


def _unanimous_critic_min_success() -> int:
    return flags.UNANIMOUS_CRITIC_MIN_SUCCESS()


def _format_few_shot_for_critic(examples: list, limit: int = 4) -> str:
    blocks: list[str] = []
    for i, ex in enumerate(examples[:limit]):
        if isinstance(ex, (list, tuple)) and len(ex) >= 2:
            q, sql = ex[0], ex[1]
        elif isinstance(ex, dict):
            q, sql = ex.get("question", ""), ex.get("sql", "")
        else:
            continue
        blocks.append(f"### Demo {i}\nQuestion: {q}\nSQL:\n{sql}")
    return "\n\n".join(blocks) if blocks else "(none)"


def _run_unanimous_critic(
    *,
    question: str,
    evidence: str | None,
    winner,
    winner_qr: QueryResponse,
    winner_sig: Optional[tuple],
    relevant_tables: list,
    trained_questions: list,
    connector,
    n_success: int,
) -> tuple[Any | None, dict]:
    """Audit a unanimous winner and optionally return a challenger candidate.

    Returns ``(challenger_or_None, debug_stats)``. Challenger is only non-None
    when the critic produced a different successful execution result *and*
    shadow mode is off.
    """
    stats: dict[str, Any] = {
        "triggered": True,
        "n_success": n_success,
        "shadow": _unanimous_critic_shadow(),
        "has_concrete_issue": False,
        "issue_type": "",
        "explanation": "",
        "challenger_sql": "",
        "challenger_exec_ok": False,
        "challenger_error": "",
        "result_differs": False,
        "would_override": False,
        "applied": False,
    }
    winner_sql = (getattr(winner, "sql_code", "") or "").strip()
    table_names = []
    for t in relevant_tables or []:
        if isinstance(t, dict):
            name = f"{t.get('schema_name', '')}.{t.get('name', '')}".strip(".")
            table_names.append(name or str(t.get("name") or ""))
        else:
            table_names.append(str(t))

    system = (
        "You are a strict SQL auditor for Text-to-SQL. All sampled candidates "
        "returned THE SAME result, so they may share one correlated bug. "
        "Only flag has_concrete_issue=true when you can name a specific bug "
        "relative to the question and evidence. Common bugs: filters not required "
        "by question/evidence; City vs County / wrong attribute; wrong GROUP BY "
        "grain; wrong percentage denominator; decorative DISTINCT; code→prose CASE "
        "when raw values were asked; unnecessary joins; extra projected columns. "
        "If the SQL looks correct, set has_concrete_issue=false and leave "
        "challenger_sql empty. When you do propose a challenger, write complete "
        "executable SQL only (no comments)."
    )
    human = (
        f"Question:\n{question}\n\n"
        f"Evidence (authoritative if present):\n{evidence or '(none)'}\n\n"
        f"Available tables:\n{', '.join(table_names) or '(unknown)'}\n\n"
        f"Similar train demos:\n{_format_few_shot_for_critic(trained_questions)}\n\n"
        f"Unanimous SQL:\n{winner_sql}\n\n"
        f"Execution result preview:\n{_result_preview(winner_qr)}\n\n"
        "Audit the SQL. If and only if there is a concrete issue, propose "
        "challenger_sql that fixes it."
    )
    try:
        llm = get_llm_client(temperature=0.0, max_tokens=4096)
        verdict = invoke_with_structured_output(
            llm,
            [SystemMessage(content=system), HumanMessage(content=human)],
            _UnanimousCriticVerdict,
        )
    except Exception as exc:
        stats["explanation"] = f"critic_error:{type(exc).__name__}"
        return None, stats

    if verdict is None:
        stats["explanation"] = "critic_error:None"
        return None, stats

    stats["has_concrete_issue"] = bool(verdict.has_concrete_issue)
    stats["issue_type"] = (verdict.issue_type or "")[:80]
    stats["explanation"] = (verdict.explanation or "")[:300]
    challenger_sql = (verdict.challenger_sql or "").strip()
    stats["challenger_sql"] = challenger_sql[:400]

    if not verdict.has_concrete_issue or not challenger_sql:
        return None, stats
    if challenger_sql == winner_sql:
        stats["explanation"] = (stats["explanation"] + " | challenger_identical").strip(
            " |"
        )
        return None, stats

    challenger_qr = _run_sql(challenger_sql, connector)
    challenger_sig = _result_signature(challenger_qr)
    stats["challenger_exec_ok"] = challenger_sig is not None
    if challenger_qr.error:
        stats["challenger_error"] = str(challenger_qr.error)[:200]
    if challenger_sig is None:
        return None, stats

    differs = challenger_sig != winner_sig
    stats["result_differs"] = differs
    stats["would_override"] = differs
    if not differs:
        return None, stats

    if stats["shadow"]:
        return None, stats

    # Live override: build a lightweight copy of the winner with new SQL.
    try:
        challenger = winner.model_copy(
            update={
                "sql_code": challenger_sql,
                "thought": f"Unanimous critic fix ({stats['issue_type']}): "
                f"{stats['explanation']}",
                "response": getattr(winner, "response", "") or "Critic-revised SQL.",
            }
        )
    except Exception:
        challenger = winner
        try:
            challenger.sql_code = challenger_sql  # type: ignore[attr-defined]
        except Exception:
            return None, stats
    stats["applied"] = True
    return challenger, stats


def _schema_block_for_revision(path_state: dict) -> str:
    """Render the same schema the generator saw, for the audit pass.

    The audit has to be able to name a *different* column than the draft chose,
    which it can only do if it sees the same table and column inventory the draft
    was working from.
    """
    try:
        from gsf.retrieval.text_to_sql.agents.sql_from_semantic import (
            format_tables_for_prompt,
        )

        tables = path_state.get("relevant_tables") or []
        if not tables:
            return "(schema unavailable)"
        return format_tables_for_prompt(tables, target_db=path_state.get("target_db"))
    except Exception:
        return "(schema unavailable)"


def _result_signature(qr: QueryResponse) -> Optional[tuple]:
    """Canonicalize a query result into an order-insensitive, hashable signature.

    Row order and output aliases are ignored, while projected column order is
    preserved. Thus ``SELECT MAX(x) AS a`` and ``... AS b`` agree when their
    values agree, but ``SELECT x, y`` and ``SELECT y, x`` remain distinct.
    Returns ``None`` when the candidate errored (or produced no parseable
    payload) so it is excluded from voting; an empty result set maps to an empty
    tuple ``()`` — a valid, distinct signature.
    """
    if qr.error or not qr.result:
        return None
    try:
        rows = json.loads(qr.result[0])
    except (TypeError, ValueError):
        return None
    if not isinstance(rows, list):
        return None
    try:
        # JSON object insertion order matches the SQL projection order. Keep
        # values in that order but deliberately discard alias/key names.
        value_rows = [
            tuple(json.dumps(v, sort_keys=True, default=str) for v in r.values())
            for r in rows
        ]
        return tuple(sorted(value_rows))
    except (AttributeError, TypeError):
        # Non-dict rows: fall back to a stable representation.
        return (json.dumps(rows, sort_keys=True, default=str),)


def _result_preview(qr: QueryResponse, max_chars: int = 600) -> str:
    """Short result snippet for judge / rerank passages."""
    if qr.error:
        return f"ERROR: {qr.error[:200]}"
    if not qr.result:
        return "(no result)"
    raw = qr.result[0] if isinstance(qr.result, list) else str(qr.result)
    text = raw if isinstance(raw, str) else json.dumps(raw, default=str)
    if len(text) > max_chars:
        return text[:max_chars] + "…"
    return text


def _cluster_reps(clusters: dict[tuple, list[int]]) -> list[int]:
    """One index per cluster; prefer cand0 when present, else lowest index."""
    reps: list[int] = []
    for idxs in clusters.values():
        reps.append(0 if 0 in idxs else min(idxs))
    return sorted(set(reps))


def _nonempty_first_enabled() -> bool:
    """Whether a non-empty result outranks a larger empty cluster.

    Env ``BIRD_NONEMPTY_FIRST`` (default ``0`` = current behavior). Empty
    results all canonicalize to the same signature ``()``, so candidates that
    return nothing cluster together even when they fail for unrelated reasons.
    Under plain size-first voting that hands them an artificial majority.
    """
    return flags.NONEMPTY_FIRST()


# Slot reliability priors from v20 pools (full-set fit). Used when
# BIRD_SLOT_VOTE_WEIGHTS is on and no JSON override is provided. Tail indices
# past the table fall back to the last entry (weak revision slots).
_SOLO_ACC_WEIGHTS: tuple[float, ...] = (
    0.729,
    0.708,
    0.483,
    0.720,
    0.497,
    0.501,
    0.497,
    0.451,
    0.447,
    0.420,
    0.391,
    0.484,
    0.417,
    0.500,
)
_VOTER_Q_WEIGHTS: tuple[float, ...] = (
    0.742,
    0.733,
    0.670,
    0.733,
    0.553,
    0.549,
    0.554,
    0.507,
    0.493,
    0.453,
    0.438,
    0.517,
    0.417,
    0.500,
)


def _slot_vote_weight_mode() -> str:
    """``off`` | ``solo_acc`` | ``voter_q``. Env ``BIRD_SLOT_VOTE_WEIGHTS``."""
    return flags.SLOT_VOTE_WEIGHTS()


def _slot_vote_weights() -> list[float] | None:
    """Per-index vote weights, or None for flat majority.

    ``BIRD_SLOT_VOTE_WEIGHTS_JSON`` (JSON array of floats) overrides the built-in
    prior whenever the feature is enabled.
    """
    mode = _slot_vote_weight_mode()
    if mode == "off":
        return None
    override = flags.SLOT_VOTE_WEIGHTS_JSON()
    if override:
        return override
    return list(_SOLO_ACC_WEIGHTS if mode == "solo_acc" else _VOTER_Q_WEIGHTS)


def _weight_for_index(weights: list[float] | None, idx: int) -> float:
    if not weights:
        return 1.0
    if idx < len(weights):
        return float(weights[idx])
    return float(weights[-1])


def _cardinality_check_rewrite(
    sql: str,
    connector,
    run_sql_fn,
) -> tuple[str, dict]:
    """Strip COUNT(DISTINCT col) → COUNT(col) when cardinality confirms no duplicates.

    Executes the stripped SQL, compares its numeric result to the original.
    When they agree within 0.01 (no duplicate keys in the filtered set), the
    non-DISTINCT form is returned. Falls back to the original on any error or
    when DISTINCT is semantically meaningful.
    """
    import re

    stats: dict = {"triggered": False, "applied": False, "reason": ""}

    _CD_PAT = re.compile(r"COUNT\s*\(\s*DISTINCT\s+", re.IGNORECASE)
    if not _CD_PAT.search(sql):
        stats["reason"] = "no_count_distinct"
        return sql, stats

    stats["triggered"] = True
    stripped_sql = _CD_PAT.sub("COUNT(", sql)
    if stripped_sql == sql:
        stats["reason"] = "strip_noop"
        return sql, stats

    stripped_qr = run_sql_fn(stripped_sql, connector)
    if stripped_qr.error or not stripped_qr.result:
        stats["reason"] = f"stripped_exec_error:{(stripped_qr.error or 'no_result')[:60]}"
        return sql, stats

    orig_qr = run_sql_fn(sql, connector)
    if orig_qr.error or not orig_qr.result:
        stats["reason"] = "orig_exec_error"
        return sql, stats

    def _first_float(qr: QueryResponse) -> Optional[float]:
        try:
            rows = json.loads(qr.result[0])
            if rows and isinstance(rows[0], dict):
                val = next(iter(rows[0].values()))
                return float(val)
        except (TypeError, ValueError, KeyError, StopIteration, json.JSONDecodeError):
            pass
        return None

    orig_val = _first_float(orig_qr)
    stripped_val = _first_float(stripped_qr)
    if orig_val is None or stripped_val is None:
        stats["reason"] = "non_scalar_result"
        return sql, stats

    if abs(orig_val - stripped_val) > 0.01:
        stats["reason"] = f"distinct_matters_orig={orig_val:.2f}_stripped={stripped_val:.2f}"
        return sql, stats

    stats["applied"] = True
    stats["reason"] = f"distinct_redundant_count={orig_val:.2f}"
    return stripped_sql, stats


def _majority_winner(
    clusters: dict[tuple, list[int]],
    weights: list[float] | None = None,
) -> int:
    """Largest-cluster vote; prefer non-empty results; tie-break to lowest index.

    When *weights* is set, each candidate contributes its slot weight instead of
    1.0. Cluster score is the sum of member weights.
    """

    nonempty_first = _nonempty_first_enabled()

    def _cluster_score(idxs: list[int]) -> float:
        if weights is None:
            return float(len(idxs))
        return sum(_weight_for_index(weights, i) for i in idxs)

    def _cluster_key(item: tuple) -> tuple:
        sig, idxs = item
        nonempty = 1 if len(sig) > 0 else 0
        score = _cluster_score(idxs)
        if nonempty_first:
            return (nonempty, score, -min(idxs))
        return (score, nonempty, -min(idxs))

    _best_sig, best_idxs = max(clusters.items(), key=_cluster_key)
    winner = min(best_idxs)

    return winner


def _majority_cluster_size(clusters: dict[tuple, list[int]], majority_idx: int) -> int:
    for idxs in clusters.values():
        if majority_idx in idxs:
            return len(idxs)
    return 1


def _majority_lock_k() -> int:
    """Min majority size that blocks weak rerank overrides. ``0`` disables."""
    return flags.SQL_MAJORITY_LOCK_K()


def _rerank_override_margin() -> float:
    """Min logit gap (top − majority) required to override a locked majority."""
    return flags.SQL_RERANK_MARGIN()


def _answer_passage(sql: str, qr: QueryResponse, cluster_size: int) -> str:
    """Build a QA-style passage: executed answer first, SQL as supporting context.

    ``rerank-qa-mistral-4b`` is trained on (question, answer) pairs — putting the
    result rows first matches that objective better than ranking bare SQL.
    """
    preview = _result_preview(qr, max_chars=800)
    sql_trim = (sql or "").strip()
    if len(sql_trim) > 500:
        sql_trim = sql_trim[:500] + "…"
    return (
        f"Answer (query result, {cluster_size} agreeing candidates):\n{preview}\n\n"
        f"SQL that produced this answer:\n{sql_trim}"
    )


def _rerank_answer_winner(
    *,
    question: str,
    candidates: list,
    clusters: dict[tuple, list[int]],
    query_responses: list[QueryResponse],
    majority_idx: int,
) -> tuple[int, str]:
    """NIM-rerank cluster reps by (question ↔ answer+SQL); fall back to majority.

    Majority safety: when the largest execution cluster has size
    ``>= BIRD_SQL_MAJORITY_LOCK_K`` (default 3), a rerank pick that differs from
    the majority is kept only if ``top_logit - majority_logit >=
    BIRD_SQL_RERANK_MARGIN`` (default 0.5). This blocks weak 4–1 / 3–2 overrides
    like translated CASE labels beating raw stored values.
    """
    from gsf.utils.rerank import rerank_passages

    reps = _cluster_reps(clusters)

    # An empty result is never the answer to a BIRD question, so a rep that
    # returned no rows cannot win while a rep that returned some is available.
    # Without this the reranker can still score an empty passage top even once
    # the majority vote has been steered away from it.
    if _nonempty_first_enabled():
        empty_idxs = {
            idx for sig, idxs in clusters.items() if len(sig) == 0 for idx in idxs
        }
        nonempty_reps = [idx for idx in reps if idx not in empty_idxs]
        if nonempty_reps and len(nonempty_reps) < len(reps):
            reps = nonempty_reps
            if majority_idx in empty_idxs:
                majority_idx = reps[0]

    if len(reps) < 2:
        return (reps[0] if reps else majority_idx), "single_rep"

    passages = []
    for idx in reps:
        sql = getattr(candidates[idx], "sql_code", "") or ""
        cluster_size = next((len(idxs) for idxs in clusters.values() if idx in idxs), 1)
        passages.append(_answer_passage(sql, query_responses[idx], cluster_size))

    # Score all reps so we can compare top vs majority margin.
    ranked = rerank_passages(question, passages, top_n=len(passages))
    if not ranked:
        return majority_idx, "rerank_fallback_empty"

    local_i, logit = ranked[0]
    if not (0 <= local_i < len(reps)):
        return majority_idx, "rerank_fallback_bad_idx"
    chosen = reps[local_i]

    maj_size = _majority_cluster_size(clusters, majority_idx)
    lock_k = _majority_lock_k()
    margin_need = _rerank_override_margin()
    score_by_rep = {reps[li]: float(lg) for li, lg in ranked if 0 <= li < len(reps)}
    maj_logit = score_by_rep.get(majority_idx)
    top_logit = float(logit)
    margin = (top_logit - maj_logit) if maj_logit is not None else None
    lock_active = lock_k > 0 and maj_size >= lock_k and chosen != majority_idx
    locked = False
    if lock_active:
        if margin is None or margin < margin_need:
            locked = True
            chosen = majority_idx

    if locked:
        return (
            chosen,
            f"majority_lock_size={maj_size}_margin="
            f"{margin if margin is not None else 'na'}<{margin_need}",
        )
    return chosen, f"rerank_logit={logit:.4f}"


def _llm_judge_winner(
    *,
    question: str,
    candidates: list,
    clusters: dict[tuple, list[int]],
    query_responses: list[QueryResponse],
    majority_idx: int,
) -> tuple[int, str]:
    """Ask the LLM to pick among one representative per disagreeing cluster.

    Returns ``(winner_idx, reason)``. On any failure, returns
    ``(majority_idx, "fallback_majority")``.
    """
    reps = _cluster_reps(clusters)
    if len(reps) < 2:
        return majority_idx, "single_rep"

    option_blocks = []
    for idx in reps:
        sql = (getattr(candidates[idx], "sql_code", "") or "").strip()
        preview = _result_preview(query_responses[idx])
        cluster_size = next((len(idxs) for idxs in clusters.values() if idx in idxs), 1)
        option_blocks.append(
            f"### Candidate {idx} (cluster_size={cluster_size})\n"
            f"Result (answer):\n{preview}\n"
            f"SQL:\n{sql}\n"
        )

    system = (
        "You are selecting which *answer* (query result) correctly resolves a "
        "database question. Several SQLs executed successfully but returned "
        "DIFFERENT results. Prefer the candidate whose RESULT best answers the "
        "question (and evidence); use the SQL only to check filters/joins/"
        "aggregations. Do NOT prefer a candidate merely because more candidates "
        "agree. Return chosen_index as one of the listed candidate indices."
    )
    human = (
        f"Question:\n{question}\n\n"
        + "\n".join(option_blocks)
        + f"\nMajority-vote default (for reference only): candidate {majority_idx}\n"
        + "Respond with chosen_index equal to one of: "
        + ", ".join(str(i) for i in reps)
        + "."
    )
    try:
        # gpt-5.x spends a large share of completion tokens on hidden reasoning;
        # 1024 is often entirely consumed before structured fields are emitted.
        llm = get_llm_client(temperature=0.0, max_tokens=8192)
        pick = invoke_with_structured_output(
            llm,
            [SystemMessage(content=system), HumanMessage(content=human)],
            _SQLJudgePick,
        )
        if pick is None:
            return majority_idx, "judge_error:None"
        chosen = int(pick.chosen_index)
        if chosen not in reps:
            logger.warning(
                "SQL judge returned idx %s not in reps %s; falling back to majority %s",
                chosen,
                reps,
                majority_idx,
            )
            return majority_idx, f"invalid_idx_{chosen}"
        return chosen, (pick.reason or "")[:240]
    except Exception as exc:
        logger.warning("SQL judge LLM failed (%s); falling back to majority", exc)
        return majority_idx, f"judge_error:{type(exc).__name__}"


class SQLSelectionAgent(BaseAgent):
    """
    Elect the best SQL from ``path_state["sql_candidates"]`` by execution vote.

    Input:
    - ``path_state["sql_candidates"]``: list of ``SQLGenerationModel`` candidates
    - ``path_state["relevant_tables"]``: used to resolve the connector
    - ``connectors``: injected DB connectors

    Output:
    - ``path_state["sql_generation_result"]``: the elected candidate
    - ``path_state["custom_analyses_used"]``: the winner's custom-analysis IDs
    - decision: "constructable" (always, unless no candidate has SQL)
    """

    def __init__(self):
        super().__init__("select_sql_candidate")

    def execute(self, state: AgentState) -> Dict[str, Any]:
        path_state = state.get("path_state", {})
        candidates = path_state.get("sql_candidates") or []

        # Pass-through: nothing to choose between (feature off, or single candidate).
        if len(candidates) < 2:
            return {"decision": "constructable", "path_state": path_state}

        connectors = state.get("connectors") or []
        relevant_tables = path_state.get("relevant_tables", [])
        connector = resolve_connector_from_tables(relevant_tables, connectors)

        # Execute each candidate and compute its result signature.
        signatures: list[Optional[tuple]] = []
        exec_errors: list[Optional[str]] = []
        query_responses: list[QueryResponse] = []
        for idx, cand in enumerate(candidates):
            sql_code = getattr(cand, "sql_code", "") or ""
            if not sql_code.strip():
                signatures.append(None)
                exec_errors.append("empty_sql")
                query_responses.append(
                    QueryResponse(result=None, sliced=False, error="empty_sql")
                )
                continue
            qr = _run_sql(sql_code, connector)
            query_responses.append(qr)
            sig = _result_signature(qr)
            signatures.append(sig)
            exec_errors.append((qr.error or None) if sig is None else None)
            self.logger.debug(
                "Candidate %d: %s",
                idx,
                "error" if sig is None else f"rows_signature_len={len(sig)}",
            )

        n_original = len(candidates)

        # Second reasoning pass, now that every draft has a result to be judged
        # against. Revisions are appended rather than substituted, so the oracle
        # pool can only grow and the vote below still ranks the original drafts
        # in their original order unless BIRD_VERIFY_REVISE_VOTE says otherwise.
        if verify_revise.enabled() and len(candidates) >= 1:
            _q = get_original_question(state) or get_question_for_processing(state)
            _schema = _schema_block_for_revision(path_state)
            revisions = verify_revise.revise_pool(
                question=_q,
                candidates=candidates,
                query_responses=query_responses,
                schema_block=_schema,
                llm=state.get("llm"),
                run_sql=_run_sql,
                connector=connector,
            )
            revisions.extend(
                verify_revise.arbitrate(
                    question=_q,
                    candidates=candidates,
                    query_responses=query_responses,
                    schema_block=_schema,
                    llm=state.get("llm"),
                    run_sql=_run_sql,
                    connector=connector,
                )
            )
            for rev in revisions:
                candidates.append(rev)
                qr = _run_sql(getattr(rev, "sql_code", "") or "", connector)
                query_responses.append(qr)
                signatures.append(_result_signature(qr))
                exec_errors.append(None)
            if revisions:
                path_state["sql_candidates"] = candidates
                self.logger.info(
                    "verify_revise: appended %d revision(s); pool now %d",
                    len(revisions),
                    len(candidates),
                )

        # Cluster successful candidates by identical result set.
        clusters: dict[tuple, list[int]] = {}
        n_voting = (
            len(signatures)
            if (not verify_revise.enabled() or verify_revise.vote_enabled())
            else min(len(signatures), n_original)
        )
        for idx, sig in enumerate(signatures[:n_voting]):
            if sig is None:
                continue
            clusters.setdefault(sig, []).append(idx)

        # Candidates that differ only in projected column order are the same
        # answer, but their signatures differ, so the vote splits and the lowest
        # index wins arbitrarily. Fold them together first, then let the order
        # named in the evidence decide which spelling ships.
        projection_columns_by_index: dict[int, Optional[tuple[str, ...]]] = {}
        projection_order_on = projection_order.enabled_for(path_state.get("target_db"))
        if projection_order_on and clusters:
            projection_columns_by_index = {
                idx: projection_order.projection_columns(
                    getattr(candidates[idx], "sql_code", "") or ""
                )
                for idxs in clusters.values()
                for idx in idxs
            }
            clusters = projection_order.merge_permuted(
                clusters, projection_columns_by_index
            )

        selection_method = "majority"
        judge_reason = ""
        majority_idx = 0
        # Resolve at execute-time (not import-time) so dotenv / launch env apply.
        mode = _resolve_select_mode()

        if not clusters:
            # Every candidate errored — keep the first (matches prior behavior).
            winner_idx = 0
            selection_method = "all_failed"
            self.logger.warning(
                "SQL vote: all %d candidates failed to execute; keeping candidate 0.",
                len(candidates),
            )
        else:
            slot_weights = _slot_vote_weights()
            majority_idx = _majority_winner(clusters, weights=slot_weights)
            if projection_order_on:
                _question = get_original_question(state) or get_question_for_processing(
                    state
                )
                _members = next(
                    (idxs for idxs in clusters.values() if majority_idx in idxs), []
                )
                _preferred = projection_order.preferred_index(
                    _members,
                    projection_columns_by_index,
                    str(
                        path_state.get("evidence") or extract_evidence(_question) or ""
                    ),
                    str(_question or ""),
                )
                if _preferred is not None and _preferred != majority_idx:
                    self.logger.info(
                        "projection_order: majority slot %d -> %d on projection order",
                        majority_idx,
                        _preferred,
                    )
                    majority_idx = _preferred
            if flags.BEST_BASE_SLOT() and clusters:
                _maj_cluster = next(
                    (idxs for idxs in clusters.values() if majority_idx in idxs),
                    [majority_idx],
                )
                _base_members = [i for i in _maj_cluster if i < n_original]
                if _base_members:
                    _best_base = min(_base_members)
                    if _best_base != majority_idx:
                        self.logger.info(
                            "best_base_slot: cluster winner %d -> base slot %d",
                            majority_idx,
                            _best_base,
                        )
                        majority_idx = _best_base
            winner_idx = majority_idx
            if slot_weights is not None:
                # Weighted vote *is* the measured policy (CV +0.7–0.85pp). Do not
                # let rerank/judge undo it unless explicitly allowed.
                selection_method = f"majority_weighted_{_slot_vote_weight_mode()}"
            allow_override = (
                slot_weights is None or flags.SLOT_VOTE_WEIGHTS_ALLOW_RERANK()
            )
            if allow_override and len(clusters) > 1 and mode in {"rerank", "llm_judge"}:
                question = get_original_question(state) or get_question_for_processing(
                    state
                )
                if mode == "rerank":
                    winner_idx, judge_reason = _rerank_answer_winner(
                        question=question,
                        candidates=candidates,
                        clusters=clusters,
                        query_responses=query_responses,
                        majority_idx=majority_idx,
                    )
                    if judge_reason.startswith("majority_lock"):
                        selection_method = "majority_lock"
                    elif judge_reason.startswith(("rerank_fallback", "single_rep")):
                        selection_method = (
                            f"majority_after_{judge_reason.split(':')[0]}"
                        )
                    else:
                        selection_method = "answer_rerank"
                else:
                    winner_idx, judge_reason = _llm_judge_winner(
                        question=question,
                        candidates=candidates,
                        clusters=clusters,
                        query_responses=query_responses,
                        majority_idx=majority_idx,
                    )
                    selection_method = (
                        "llm_judge"
                        if not judge_reason.startswith(
                            ("fallback", "judge_error", "invalid")
                        )
                        else f"majority_after_{judge_reason.split(':')[0]}"
                    )
            self.logger.info(
                "SQL select: %d candidates, cluster sizes=%s, mode=%s method=%s, "
                "majority=%d winner=%d",
                len(candidates),
                sorted((len(v) for v in clusters.values()), reverse=True),
                mode,
                selection_method,
                majority_idx,
                winner_idx,
            )

        winner = candidates[winner_idx]
        critic_stats: dict[str, Any] | None = None
        repair_stats: dict[str, Any] | None = None
        ship_stats: dict[str, Any] | None = None
        gate_stats: dict[str, Any] | None = None
        n_success = sum(1 for s in signatures if s is not None)

        # Fail-closed binary wrongness → largest-other. Prefer this over free
        # ship-rewrite: it never invents SQL and was ~92% precise offline.
        #
        # The gate clusters over the *whole* pool, revisions included, even when
        # they are barred from the vote. The vote deliberately ignores them so a
        # bad revision cannot outvote the drafts; the gate only consults them
        # after an independent judge has already called the winner wrong, and
        # the alternative it needs usually lives among those revisions. Reusing
        # the vote's clusters makes the gate unreachable: the winner is the
        # majority of the drafts, so no other draft cluster can outnumber it.
        if verify_revise.wrongness_gate_enabled() and clusters:
            question = get_original_question(state) or get_question_for_processing(
                state
            )
            gate_clusters: dict[tuple, list[int]] = {}
            for idx, sig in enumerate(signatures):
                if sig is None:
                    continue
                gate_clusters.setdefault(sig, []).append(idx)
            new_idx, gate_stats = verify_revise.maybe_wrongness_switch(
                question=question,
                winner_idx=winner_idx,
                candidates=candidates,
                query_responses=query_responses,
                signatures=signatures,
                clusters=gate_clusters,
                llm=state.get("llm"),
            )
            if gate_stats.get("applied") and new_idx != winner_idx:
                winner_idx = new_idx
                winner = candidates[winner_idx]
                selection_method = "wrongness_gate"

        # Cardinality check: strip COUNT(DISTINCT col) when no duplicate keys exist
        # in the filtered set. One SQL execution, no LLM. Offline: +0.85pp.
        cardinality_stats: dict[str, Any] | None = None
        if flags.CARDINALITY_CHECK() and connector is not None:
            winner_sql = (getattr(winner, "sql_code", "") or "").strip()
            if winner_sql:
                new_sql, cardinality_stats = _cardinality_check_rewrite(
                    winner_sql, connector, _run_sql
                )
                if cardinality_stats.get("applied") and new_sql != winner_sql:
                    try:
                        winner = winner.model_copy(update={"sql_code": new_sql})
                        selection_method = "cardinality_check"
                    except Exception:
                        cardinality_stats["applied"] = False
                        cardinality_stats["reason"] = (
                            cardinality_stats.get("reason", "") + "|model_copy_failed"
                        )

        # Force-fix harvest: K independent corrections of the winner; if they
        # agree on a result signature that already exists in the pool, switch.
        # Runs after the wrongness gate so empty/WRONG∧margin can still fire
        # cheaply first. Default off (BIRD_HARVEST_FORCE).
        harvest_stats: dict[str, Any] | None = None
        if (
            verify_revise.harvest_force_enabled()
            and not (gate_stats and gate_stats.get("applied"))
            and connector is not None
        ):
            question = get_original_question(state) or get_question_for_processing(
                state
            )
            new_idx, harvest_stats = verify_revise.maybe_harvest_force_switch(
                question=question,
                winner_idx=winner_idx,
                candidates=candidates,
                query_responses=query_responses,
                signatures=signatures,
                llm=state.get("llm"),
                run_sql=_run_sql,
                connector=connector,
                evidence=str(path_state.get("evidence") or state.get("evidence") or ""),
            )
            if harvest_stats.get("applied") and new_idx != winner_idx:
                winner_idx = new_idx
                winner = candidates[winner_idx]
                selection_method = "harvest_force"

        # Result-grounded rewrite of the *shipped* query. Pool voting failed;
        # this asks the same audit to fix the one query we return, in place.
        # Skipped when the wrongness gate already switched — free rewrite has
        # much worse precision than largest-other.
        if (
            verify_revise.enabled()
            and verify_revise.ship_enabled()
            and not (gate_stats and gate_stats.get("applied"))
            and not (harvest_stats and harvest_stats.get("applied"))
            and winner_idx < len(query_responses)
            and connector is not None
        ):
            question = get_original_question(state) or get_question_for_processing(
                state
            )
            challenger, ship_stats = verify_revise.rewrite_shipped(
                question=question,
                winner=winner,
                winner_qr=query_responses[winner_idx],
                schema_block=_schema_block_for_revision(path_state),
                llm=state.get("llm"),
                run_sql=_run_sql,
                connector=connector,
            )
            if challenger is not None:
                winner = challenger
                selection_method = "verify_revise_ship"
                # Keep signatures consistent for any later empty_repair checks.
                new_qr = _run_sql(getattr(winner, "sql_code", "") or "", connector)
                query_responses[winner_idx] = new_qr
                signatures[winner_idx] = _result_signature(new_qr)

        # An empty winner has already lost — no BIRD gold query returns zero rows
        # — so a repair that produces any rows cannot score worse. Only fires when
        # nothing better exists in the pool, which selection would have found.
        if (
            empty_repair.enabled()
            and clusters
            and all(len(sig) == 0 for sig in clusters)
            and connector is not None
        ):
            question = get_original_question(state) or get_question_for_processing(
                state
            )
            repaired_sql, repair_stats = empty_repair.repair(
                question=question,
                evidence=extract_evidence(question),
                sql=getattr(winner, "sql_code", "") or "",
                connector=connector,
                run_sql=_run_sql,
            )
            if repaired_sql:
                try:
                    winner = winner.model_copy(
                        update={
                            "sql_code": repaired_sql,
                            "thought": "Empty-result repair: "
                            f"{repair_stats.get('diagnosis', '')}",
                        }
                    )
                    selection_method = "empty_repair"
                except Exception:
                    repair_stats["applied"] = False
                    repair_stats["reason"] = "model_copy_failed"
        # UC_H1 trigger: one execution cluster with enough successes.
        if (
            _unanimous_critic_enabled()
            and len(clusters) == 1
            and n_success >= _unanimous_critic_min_success()
            and winner_idx < len(query_responses)
        ):
            question = get_original_question(state) or get_question_for_processing(
                state
            )
            evidence = extract_evidence(question)
            challenger, critic_stats = _run_unanimous_critic(
                question=question,
                evidence=evidence,
                winner=winner,
                winner_qr=query_responses[winner_idx],
                winner_sig=signatures[winner_idx],
                relevant_tables=relevant_tables,
                trained_questions=list(path_state.get("trained_questions") or []),
                connector=connector,
                n_success=n_success,
            )
            if challenger is not None:
                winner = challenger
                selection_method = "unanimous_critic"
                judge_reason = (
                    f"critic:{critic_stats.get('issue_type', '')}:"
                    f"{(critic_stats.get('explanation') or '')[:120]}"
                )
        if not (getattr(winner, "sql_code", "") or "").strip():
            return {
                "path_state": {
                    **path_state,
                    "unconstructable_explanation": "No candidate produced SQL.",
                },
                "decision": "unconstructable",
            }

        custom_analyses_used = (
            get_custom_analyses_ids(winner.custom_analyses_used)
            if getattr(winner, "custom_analyses_used", None)
            else []
        )

        return {
            "decision": "constructable",
            "path_state": {
                **path_state,
                "sql_generation_result": winner,
                "custom_analyses_used": custom_analyses_used,
                **(
                    {"unanimous_critic": critic_stats}
                    if critic_stats is not None
                    else {}
                ),
                **({"empty_repair": repair_stats} if repair_stats is not None else {}),
                **(
                    {"verify_revise_ship": ship_stats} if ship_stats is not None else {}
                ),
                **({"wrongness_gate": gate_stats} if gate_stats is not None else {}),
                **(
                    {"cardinality_check": cardinality_stats}
                    if cardinality_stats is not None
                    else {}
                ),
            },
        }
