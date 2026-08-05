"""Spend a second pass of reasoning on each candidate, grounded in its result.

Diagnosis this exists to address: on the questions where the whole pool is
wrong, the pool almost always already had what it needed. Of the missing gold
columns, 52 of 59 sat in a table the query had already opened; of the missing
tables, 47 of 51 were one join key from a table the query already used. The
generator is not short of information, it is picking wrong among things it can
already see. Interventions that add or reorganise context (live-database
EXPLORE, entity-to-column rendering, pruning noise down to 55% signal, forcing
diverse schema readings) all came back flat, which is what that diagnosis
predicts.

So this adds reasoning instead of context, and grounds it in the one piece of
evidence the generator never gets to see: what its own query actually returned.
A draft that selected an id where the question asked for a name, or aggregated a
column that was already a stored total, is usually obvious from its own output
and invisible from the prompt alone.

Revisions are *appended* to the pool, never substituted for the draft that
produced them. Oracle EX is a max over the pool, so appending can only raise it,
and selection continues to see the original candidates in their original order —
which keeps a revision that makes things worse from costing shipped accuracy and
keeps the measurement attributable. Set ``BIRD_VERIFY_REVISE_VOTE=1`` to let
revisions also compete in the vote once they have proven themselves.

Env:
- ``BIRD_VERIFY_REVISE``      master switch (default off)
- ``BIRD_VERIFY_REVISE_MAX``  how many candidates to revise (default 4)
- ``BIRD_VERIFY_REVISE_VOTE`` let revisions join the selection vote (default off)
- ``BIRD_VERIFY_REVISE_ROWS`` result rows shown back to the model (default 10)
- ``BIRD_VERIFY_REVISE_FAIL_ALONE`` only append revisions whose result signature
  differs from the audited draft and from the draft plurality, and is non-empty
  (default on). Makes revise slots behave like generation slot 2: wrong answers
  that merely reinforce the wrong majority are dropped.
- ``BIRD_VERIFY_REVISE_WHEN``  which drafts get an audit call (default ``smart``):
  ``always`` = today's behavior; ``broken`` = empty/error only; ``disagree`` =
  broken or disagrees with peer plurality; ``smart`` = disagree, plus **one**
  skeptical revise when all drafts share the same non-empty result (the
  unanimous-wrong case no disagreement gate can see).
- ``BIRD_WRONGNESS_GATE``     after selection, if winner is empty OR
  (binary WRONG and another cluster beats winner by ``BIRD_WRONGNESS_MARGIN``),
  ship the largest-other cluster instead of rewriting (default off)
- ``BIRD_WRONGNESS_MARGIN``   other_size - winner_size required to trust WRONG
  (default 2)
- ``BIRD_WRONGNESS_MODEL``    judge model for the binary gate (default:
  ``ENTITY_EXTRACTION_MODEL`` / ``JUDGE_MODEL_NAME`` / generator llm)
- ``BIRD_HARVEST_FORCE``      after selection, force-fix the winner with K
  prompt variants; if ≥agree share a result sig already in the pool, switch
  (default off)
- ``BIRD_HARVEST_FORCE_K``    number of force-fix variants (default 3)
- ``BIRD_HARVEST_FORCE_AGREE`` min agreeing samples to switch (default 2)
- ``BIRD_HARVEST_FORCE_MODEL`` judge model for harvest (default: wrongness /
  generator model; try ``aws/anthropic/bedrock-claude-opus-4-8``)
"""

from __future__ import annotations

import logging
import os
from typing import Any, Optional

from pydantic import BaseModel, Field

logger = logging.getLogger(__name__)

_MAX_PREVIEW_CHARS = 1200


def enabled() -> bool:
    return os.environ.get("BIRD_VERIFY_REVISE", "0").strip().lower() not in {
        "0",
        "false",
        "no",
        "off",
        "",
    }


def vote_enabled() -> bool:
    """Whether revisions compete in selection, not just in the oracle pool."""
    return os.environ.get("BIRD_VERIFY_REVISE_VOTE", "0").strip().lower() not in {
        "0",
        "false",
        "no",
        "off",
        "",
    }


def fail_alone_enabled() -> bool:
    """Drop revise twins of the draft / plurality and empty results.

    Default on: slots ≥4 historically join a wrong cluster ~65% of the time when
    slot0 is wrong; filtering same-sig / empty cuts that without costing oracle
    on v20 pools. Set ``BIRD_VERIFY_REVISE_FAIL_ALONE=0`` to restore append-all.
    """
    return os.environ.get("BIRD_VERIFY_REVISE_FAIL_ALONE", "1").strip().lower() not in {
        "0",
        "false",
        "no",
        "off",
    }


def revise_when_mode() -> str:
    """``always`` | ``broken`` | ``disagree`` | ``smart`` (default).

    Measured on v20: always-revise fixes 169 parents but breaks 439 (0.38×).
    broken/empty alone is 20/0; broken|disagree is 80/18 (~4.4×) at ~1/7 the
    calls. ``smart`` adds one skeptical pass when all drafts agree — the case
    where disagreement cannot fire but ~half of unique rescues hid.
    """
    raw = os.environ.get("BIRD_VERIFY_REVISE_WHEN", "smart").strip().lower()
    if raw in {"always", "all", "1", "true", "yes", "on"}:
        return "always"
    if raw in {"broken", "empty", "error"}:
        return "broken"
    if raw in {"disagree", "dissent"}:
        return "disagree"
    if raw in {"smart", "needed", ""}:
        return "smart"
    logger.warning("BIRD_VERIFY_REVISE_WHEN=%r unknown; using smart", raw)
    return "smart"


def ship_enabled() -> bool:
    """Whether a WRONG audit of the *selected* query replaces what we ship.

    Distinct from ``vote_enabled``: that lets revisions compete in the vote;
    this rewrites the winner in place after selection. Pool selection failed
    because the model cannot pick among many results; rewriting one query from
    its own result is the skill verify/revise already demonstrated for oracle.
    """
    return os.environ.get("BIRD_VERIFY_REVISE_SHIP", "0").strip().lower() not in {
        "0",
        "false",
        "no",
        "off",
        "",
    }


def wrongness_gate_enabled() -> bool:
    """Fail-closed binary wrongness → largest-other switch (no free rewrite).

    Measured offline on v20: ``empty | (WRONG ∧ margin≥2) → LO`` lifts slot0
    71.06% → 73.08% at ~97% switch precision. Prefer a *different* judge model
    than the generator (see ``wrongness_model``).
    """
    return os.environ.get("BIRD_WRONGNESS_GATE", "0").strip().lower() not in {
        "0",
        "false",
        "no",
        "off",
        "",
    }


def wrongness_margin() -> int:
    try:
        return max(0, int(os.environ.get("BIRD_WRONGNESS_MARGIN", "2")))
    except (TypeError, ValueError):
        return 2


def wrongness_model() -> str | None:
    """Model id for the binary gate, or None to reuse the generator client."""
    for key in (
        "BIRD_WRONGNESS_MODEL",
        "ENTITY_EXTRACTION_MODEL",
        "JUDGE_MODEL_NAME",
    ):
        val = (os.environ.get(key) or "").strip()
        if val:
            return val
    return None


def harvest_force_model() -> str | None:
    """Model for force-fix harvest; falls back to wrongness/generator model."""
    val = (os.environ.get("BIRD_HARVEST_FORCE_MODEL") or "").strip()
    if val:
        return val
    return wrongness_model()


def harvest_force_enabled() -> bool:
    """Multi-prompt force-fix of the winner → switch to agreeing pool sig.

    Offline (revise-consensus + force_fix stacked) is the path toward 78%.
    Default off until a full-dev offline score clears the bar; enable with
    ``BIRD_HARVEST_FORCE=1``.
    """
    return os.environ.get("BIRD_HARVEST_FORCE", "0").strip().lower() not in {
        "0",
        "false",
        "no",
        "off",
        "",
    }


def harvest_force_k() -> int:
    try:
        return max(1, min(5, int(os.environ.get("BIRD_HARVEST_FORCE_K", "3"))))
    except (TypeError, ValueError):
        return 3


def harvest_force_agree() -> int:
    try:
        return max(2, int(os.environ.get("BIRD_HARVEST_FORCE_AGREE", "2")))
    except (TypeError, ValueError):
        return 2


def _max_candidates() -> int:
    try:
        return max(0, int(os.environ.get("BIRD_VERIFY_REVISE_MAX", "4")))
    except (TypeError, ValueError):
        return 4


def _max_rows() -> int:
    try:
        return max(1, int(os.environ.get("BIRD_VERIFY_REVISE_ROWS", "10")))
    except (TypeError, ValueError):
        return 10


class RevisionModel(BaseModel):
    """Structured verdict on one draft query."""

    diagnosis: str = Field(
        ...,
        description=(
            "Work through whether the result actually answers the question. "
            "Name what the question asked for, what this result contains, and "
            "where they diverge. Take as much room as you need."
        ),
    )
    verdict: str = Field(
        ...,
        description=(
            "Exactly one of: CORRECT if the result answers the question as "
            "asked, or WRONG if it does not."
        ),
    )
    sql: str = Field(
        ...,
        description=(
            "If WRONG, the corrected SQL query. If CORRECT, repeat the draft "
            "query unchanged. Raw SQL only, no markdown fence."
        ),
    )
    alternative: str = Field(
        default="",
        description=(
            "A query for the strongest OTHER defensible reading of the question "
            "— a different column, grain, join or filter that a careful person "
            "could argue for. It must differ from the query above. Give one even "
            "when you are confident, and leave it empty only if the question "
            "genuinely admits no second reading. Raw SQL only, no markdown fence."
        ),
    )


class WrongnessGateModel(BaseModel):
    """Binary audit only — never rewrites SQL."""

    verdict: str = Field(..., description="Exactly CORRECT or WRONG.")
    confidence: str = Field(..., description="Exactly HIGH, MED, or LOW.")
    reason: str = Field(..., description="One short sentence.")


_WRONGNESS_SYSTEM = (
    "You audit whether an already-executed SQL result answers a database "
    "question. You do NOT rewrite SQL. You do NOT compare alternatives.\n\n"
    "Default to CORRECT. Only say WRONG when you are sure the result cannot "
    "be the answer — e.g. empty when an answer should exist, NULL-only when "
    "a concrete value is required, wrong type (a list when a single count is "
    "asked, an id when a name is asked), or the values clearly contradict the "
    "question. If unsure, say CORRECT with LOW or MED confidence.\n\n"
    "False WRONG is very costly. Prefer CORRECT when the result is plausible."
)


_SYSTEM = (
    "You are auditing a SQL query that has already been executed against the "
    "database. You can see the question and the rows the query returned.\n\n"
    "Your job is to decide whether those rows are the answer to the question, "
    "and to fix the query if they are not. The draft was written by a competent "
    "author who could see the schema but could NOT see the result, so look for "
    "the mistakes that only become visible once the output is in front of you:\n"
    "- an identifier returned where the question asked for a name, or vice versa\n"
    "- one row returned when the question asks for a list, or many when it asks "
    "for one\n"
    "- an aggregate computed over a column that already stores that total\n"
    "- a column read from the wrong table when a differently-named column in a "
    "table already joined is the one the question means\n"
    "- NULLs or zero rows that indicate a filter literal that does not match how "
    "the value is actually stored\n"
    "- the wrong row grain, so values are double counted across a join\n\n"
    "Prefer the columns and tables already present in the draft or its schema. "
    "Do not rewrite a query that is already right: if the rows answer the "
    "question, say CORRECT and repeat the query verbatim. A confident wrong "
    "rewrite is worse than leaving a correct query alone."
)

_SYSTEM_UNANIMOUS_SKEPTIC = (
    "Several independent SQL attempts all returned THE SAME result for this "
    "question. Agreement is not proof of correctness — they may all share the "
    "same bug (wrong filter, wrong grain, wrong column).\n\n"
    "Assume the shared result is SUSPECT until you show it answers the question. "
    "Check evidence/formula, grain, and column identity carefully. If anything "
    "is off, write corrected SQL that produces a DIFFERENT result. If the shared "
    "result truly answers the question, say CORRECT and repeat the draft. "
    "Prefer a different reading over a twin of the unanimous answer."
)


_ARBITER_SYSTEM = (
    "Several independent attempts were made to answer one question in SQL. Each "
    "was executed; you can see the query and the rows it returned.\n\n"
    "Where they disagree, at most one reading of the question can be right. Work "
    "out which, then write the query that answers the question — copying the best "
    "attempt verbatim if one of them is already correct, or writing a better one "
    "if none is.\n\n"
    "The disagreement itself is the evidence. If two attempts join differently and "
    "return different row counts, one of them is duplicating or dropping rows. If "
    "they project different columns, decide which the question actually asks to "
    "see. If one returns nothing and the others return rows, its filter probably "
    "does not match how the value is stored. If they all agree, they can still all "
    "be wrong in the same way, so check the result against the question rather "
    "than counting votes."
)


def arbitrate(
    *,
    question: str,
    candidates: list,
    query_responses: list,
    schema_block: str,
    llm: Any,
    run_sql,
    connector: Any,
) -> list:
    """One cross-candidate pass: read the whole pool and its results, answer once.

    The per-candidate audit sees a single draft and can only repair it locally.
    This sees the drafts side by side, so it can use their disagreement — the
    same signal the selector consumes, but with the freedom to write a query none
    of them produced instead of ranking what is already there.
    """
    if not candidates:
        return []

    from gsf.utils.llm_invoke import safe_invoke_with_structured_output
    from langchain_core.messages import HumanMessage, SystemMessage

    max_rows = _max_rows()
    blocks = []
    for i, cand in enumerate(candidates):
        sql = getattr(cand, "sql_code", "") or ""
        if not sql.strip():
            continue
        qr = query_responses[i] if i < len(query_responses) else None
        res = _preview(qr, max_rows) if qr is not None else "(not executed)"
        blocks.append(f"--- attempt {i} ---\n{sql}\nreturned: {res}")
    if not blocks:
        return []

    human = (
        f"{question}\n\n"
        f"Database schema:\n{schema_block}\n\n" + "\n\n".join(blocks) + "\n\n"
        "Which reading of the question is right, and what is the correct query?"
    )
    try:
        rev = safe_invoke_with_structured_output(
            llm,
            [SystemMessage(content=_ARBITER_SYSTEM), HumanMessage(content=human)],
            RevisionModel,
        )
    except Exception as exc:
        logger.debug("verify_revise: arbiter failed (%s)", exc)
        return []
    if rev is None:
        return []

    existing = {
        (getattr(c, "sql_code", "") or "").strip().rstrip(";").strip().upper()
        for c in candidates
    }
    out: list = []
    # Arbiter sees the full pool; plurality is over original drafts when possible.
    n_drafts = min(len(candidates), _max_candidates())
    plurality_sig = _draft_plurality_sig(query_responses, n_drafts)
    # Compare against the first draft's result as the anchor twin check.
    draft0_qr = query_responses[0] if query_responses else None
    for label in ("sql", "alternative"):
        new_sql = _clean(getattr(rev, label, "") or "")
        if not new_sql or new_sql.upper() in existing:
            continue
        try:
            probe = run_sql(new_sql, connector)
        except Exception:
            continue
        if getattr(probe, "error", None):
            continue
        ok, reason = _should_append_revision(
            probe=probe, draft_qr=draft0_qr, plurality_sig=plurality_sig
        )
        if not ok:
            logger.debug("verify_revise: arbiter %s dropped (%s)", label, reason)
            continue
        try:
            out.append(
                candidates[0].model_copy(
                    update={
                        "sql_code": new_sql,
                        "thought": f"Arbiter ({label}): "
                        f"{getattr(rev, 'diagnosis', '')[:400]}",
                    }
                )
            )
        except Exception:
            continue
        existing.add(new_sql.upper())
    if out:
        logger.info("verify_revise: arbiter produced %d new quer(y/ies)", len(out))
    return out


def _preview(qr: Any, max_rows: int) -> str:
    """Render an executed result for the audit prompt."""
    err = getattr(qr, "error", None)
    if err:
        return f"The query FAILED to execute: {str(err)[:300]}"
    result = getattr(qr, "result", None)
    if not result:
        return "(the query returned no result payload)"
    raw = result[0] if isinstance(result, list) else str(result)
    text = raw if isinstance(raw, str) else str(raw)
    import json

    try:
        rows = json.loads(text)
    except (TypeError, ValueError):
        return text[:_MAX_PREVIEW_CHARS]
    if not isinstance(rows, list):
        return text[:_MAX_PREVIEW_CHARS]
    if not rows:
        return "0 rows. The query returned an EMPTY result."
    shown = rows[:max_rows]
    body = json.dumps(shown, default=str, indent=1)[:_MAX_PREVIEW_CHARS]
    tail = "" if len(rows) <= max_rows else f"\n… {len(rows) - max_rows} more rows"
    return f"{len(rows)} row(s):\n{body}{tail}"


def _clean(sql: str) -> str:
    s = (sql or "").strip()
    if s.startswith("```"):
        s = s.split("\n", 1)[-1] if "\n" in s else s
        s = s.rsplit("```", 1)[0]
    return s.strip().rstrip(";").strip()


def _result_signature_local(qr: Any) -> Optional[tuple]:
    """Mirror sql_selection._result_signature without importing that module."""
    import json

    if getattr(qr, "error", None) or not getattr(qr, "result", None):
        return None
    try:
        rows = json.loads(qr.result[0])
    except (TypeError, ValueError, IndexError):
        return None
    if not isinstance(rows, list):
        return None
    try:
        value_rows = [
            tuple(json.dumps(v, sort_keys=True, default=str) for v in r.values())
            for r in rows
        ]
        return tuple(sorted(value_rows))
    except (AttributeError, TypeError):
        return (json.dumps(rows, sort_keys=True, default=str),)


def _is_empty_sig(sig: Optional[tuple]) -> bool:
    return sig is None or len(sig) == 0


def _draft_plurality_sig(
    query_responses: list, n_drafts: int
) -> Optional[tuple]:
    """Most common non-empty result among the original drafts."""
    counts: dict[tuple, int] = {}
    for qr in query_responses[:n_drafts]:
        sig = _result_signature_local(qr)
        if _is_empty_sig(sig):
            continue
        counts[sig] = counts.get(sig, 0) + 1  # type: ignore[index]
    if not counts:
        return None
    return max(counts.items(), key=lambda it: (it[1], -hash(it[0]) % 10_000))[0]


def _draft_sigs(query_responses: list, n_drafts: int) -> list[Optional[tuple]]:
    return [
        _result_signature_local(query_responses[i])
        if i < len(query_responses)
        else None
        for i in range(n_drafts)
    ]


def _peer_plurality_sig(
    sigs: list[Optional[tuple]], exclude_idx: int
) -> Optional[tuple]:
    """Plurality among drafts other than *exclude_idx* (non-empty only)."""
    counts: dict[tuple, int] = {}
    for i, sig in enumerate(sigs):
        if i == exclude_idx or _is_empty_sig(sig):
            continue
        counts[sig] = counts.get(sig, 0) + 1  # type: ignore[index]
    if not counts:
        return None
    return max(counts.items(), key=lambda it: (it[1], -hash(it[0]) % 10_000))[0]


def revise_targets(
    query_responses: list, n_drafts: int, mode: str | None = None
) -> list[tuple[int, str]]:
    """Which draft indices to audit, and why.

    Returns list of ``(index, reason)`` where reason is one of
    ``broken``, ``disagree``, ``unanimous_suspect``.
    """
    mode = mode or revise_when_mode()
    n = min(n_drafts, len(query_responses) if query_responses else n_drafts)
    if n <= 0:
        return []
    if mode == "always":
        return [(i, "always") for i in range(n)]

    sigs = _draft_sigs(query_responses, n)
    non_empty = [s for s in sigs if not _is_empty_sig(s)]
    all_agree = (
        len(non_empty) == n
        and n >= 2
        and len({s for s in non_empty}) == 1
    )

    targets: list[tuple[int, str]] = []
    for i, sig in enumerate(sigs):
        if _is_empty_sig(sig):
            targets.append((i, "broken"))
            continue
        if mode in {"disagree", "smart"}:
            peer = _peer_plurality_sig(sigs, i)
            if peer is not None and sig != peer:
                targets.append((i, "disagree"))

    if mode == "smart" and all_agree and not targets:
        # One skeptical pass on the anchor — not 4 correlated audits.
        targets.append((0, "unanimous_suspect"))

    # Dedup keeping first reason
    seen: set[int] = set()
    out: list[tuple[int, str]] = []
    for i, reason in targets:
        if i in seen:
            continue
        seen.add(i)
        out.append((i, reason))
    return out


def _should_append_revision(
    *,
    probe: Any,
    draft_qr: Any,
    plurality_sig: Optional[tuple],
) -> tuple[bool, str]:
    """Fail-alone gate: non-empty and not a twin of draft or draft plurality."""
    if not fail_alone_enabled():
        if getattr(probe, "error", None):
            return False, "exec_error"
        return True, "ok"
    probe_sig = _result_signature_local(probe)
    if _is_empty_sig(probe_sig):
        return False, "empty_or_error"
    draft_sig = _result_signature_local(draft_qr) if draft_qr is not None else None
    if draft_sig is not None and probe_sig == draft_sig:
        return False, "same_as_draft"
    if plurality_sig is not None and probe_sig == plurality_sig:
        return False, "same_as_plurality"
    return True, "ok"


def _winner_is_empty(qr: Any, sig: Any) -> bool:
    """Match the offline empty gate: error / missing / zero-row result."""
    if getattr(qr, "error", None):
        return True
    if sig is None:
        return True
    try:
        return len(sig) == 0
    except TypeError:
        return False


def largest_other_cluster_idx(
    clusters: dict,
    winner_idx: int,
    signatures: list,
) -> int | None:
    """Lowest index in the largest nonempty cluster that is not the winner's."""
    w_sig = signatures[winner_idx] if 0 <= winner_idx < len(signatures) else None
    best_size = -1
    best_idxs: list[int] | None = None
    for sig, idxs in clusters.items():
        if not idxs:
            continue
        if sig == w_sig:
            continue
        try:
            if len(sig) == 0:
                continue
        except TypeError:
            continue
        size = len(idxs)
        if size > best_size or (
            size == best_size and best_idxs is not None and min(idxs) < min(best_idxs)
        ):
            best_size = size
            best_idxs = idxs
    if best_idxs is None:
        return None
    return min(best_idxs)


def cluster_switch_margin(
    clusters: dict,
    winner_idx: int,
    signatures: list,
) -> int:
    """``max_other_nonempty - winner_nonempty`` (0 if winner is empty/error)."""
    w_sig = signatures[winner_idx] if 0 <= winner_idx < len(signatures) else None
    nonempty = {
        sig: idxs
        for sig, idxs in clusters.items()
        if idxs and (len(sig) > 0 if hasattr(sig, "__len__") else True)
    }
    if w_sig is None or (hasattr(w_sig, "__len__") and len(w_sig) == 0):
        s0n = 0
    else:
        s0n = len(nonempty.get(w_sig) or [])
    omax = max((len(v) for k, v in nonempty.items() if k != w_sig), default=0)
    return omax - s0n


def audit_wrongness(
    *,
    question: str,
    sql: str,
    qr: Any,
    llm: Any,
) -> tuple[str, str, str]:
    """Binary CORRECT/WRONG audit. Fail-closed → empty verdict on errors."""
    from gsf.utils.llm_invoke import safe_invoke_with_structured_output
    from langchain_core.messages import HumanMessage, SystemMessage

    human = (
        f"Question:\n{question}\n\n"
        f"SQL (context only):\n{(sql or '').strip()}\n\n"
        f"Result:\n{_preview(qr, _max_rows())}\n\n"
        "Does this result answer the question? "
        "verdict=CORRECT|WRONG, confidence=HIGH|MED|LOW."
    )
    try:
        out = safe_invoke_with_structured_output(
            llm,
            [SystemMessage(content=_WRONGNESS_SYSTEM), HumanMessage(content=human)],
            WrongnessGateModel,
        )
    except Exception as exc:
        return "", "", f"error:{type(exc).__name__}"
    if out is None:
        return "", "", "error:None"
    verdict = str(getattr(out, "verdict", "") or "").strip().upper()
    conf = str(getattr(out, "confidence", "") or "").strip().upper()
    reason = str(getattr(out, "reason", "") or "").strip()[:240]
    return verdict, conf, reason or "ok"


def maybe_wrongness_switch(
    *,
    question: str,
    winner_idx: int,
    candidates: list,
    query_responses: list,
    signatures: list,
    clusters: dict,
    llm: Any,
) -> tuple[int, dict]:
    """If gate fires, return largest-other index; else keep ``winner_idx``.

    Policy (offline-validated): empty winner → LO; else WRONG ∧ margin≥N → LO.
    Never invents SQL. Fail-closed on LLM/exec ambiguity.
    """
    stats: dict = {
        "triggered": True,
        "applied": False,
        "reason": "",
        "verdict": "",
        "confidence": "",
        "margin": 0,
        "from_idx": winner_idx,
        "to_idx": winner_idx,
    }


    if not wrongness_gate_enabled():
        stats["triggered"] = False
        stats["reason"] = "disabled"
        return winner_idx, stats
    if not clusters:
        stats["reason"] = "no_clusters"
        return winner_idx, stats

    lo = largest_other_cluster_idx(clusters, winner_idx, signatures)
    if lo is None:
        stats["reason"] = "no_alternative"
        return winner_idx, stats

    qr = query_responses[winner_idx] if winner_idx < len(query_responses) else None
    sig = signatures[winner_idx] if winner_idx < len(signatures) else None
    margin = cluster_switch_margin(clusters, winner_idx, signatures)
    stats["margin"] = margin

    if qr is not None and _winner_is_empty(qr, sig):
        stats["applied"] = True
        stats["reason"] = "auto_empty"
        stats["verdict"] = "WRONG"
        stats["confidence"] = "HIGH"
        stats["to_idx"] = lo
        logger.info(
            "wrongness_gate: empty winner %d → largest-other %d", winner_idx, lo
        )
        return lo, stats

    judge = llm
    model = wrongness_model()
    if model:
        try:
            from gsf.utils.llm_invoke import get_llm_client

            judge = get_llm_client(model=model, temperature=0.0, max_tokens=8192)
        except Exception as exc:
            stats["reason"] = f"judge_client:{type(exc).__name__}"
            return winner_idx, stats

    draft = ""
    if 0 <= winner_idx < len(candidates):
        draft = getattr(candidates[winner_idx], "sql_code", "") or ""
    verdict, conf, reason = audit_wrongness(
        question=question,
        sql=draft,
        qr=qr,
        llm=judge,
    )
    stats["verdict"] = verdict[:20]
    stats["confidence"] = conf[:10]
    stats["audit_reason"] = reason[:240]
    if not verdict.startswith("WRONG"):
        stats["reason"] = "kept_correct" if verdict else f"audit_{reason}"
        return winner_idx, stats

    need = wrongness_margin()
    if margin < need:
        stats["reason"] = f"wrong_but_margin_{margin}<{need}"
        return winner_idx, stats

    stats["applied"] = True
    stats["reason"] = "wrong_margin"
    stats["to_idx"] = lo
    logger.info(
        "wrongness_gate: WRONG margin=%d winner %d → largest-other %d (%s)",
        margin,
        winner_idx,
        lo,
        reason[:80],
    )
    return lo, stats


def rewrite_shipped(
    *,
    question: str,
    winner: Any,
    winner_qr: Any,
    schema_block: str,
    llm: Any,
    run_sql,
    connector: Any,
) -> tuple[Any | None, dict]:
    """Audit the selected query; return a replacement winner if verdict is WRONG.

    Fail-closed: any LLM/exec failure keeps the original winner (returns None).
    Only replaces when the model says WRONG, the new SQL differs, and it
    executes without error. CORRECT leaves shipping unchanged.
    """
    stats: dict = {
        "triggered": True,
        "verdict": "",
        "applied": False,
        "reason": "",
    }
    draft = (getattr(winner, "sql_code", "") or "").strip()
    if not draft:
        stats["reason"] = "empty_winner"
        return None, stats

    from gsf.utils.llm_invoke import safe_invoke_with_structured_output
    from langchain_core.messages import HumanMessage, SystemMessage

    human = (
        f"{question}\n\n"
        f"Database schema:\n{schema_block}\n\n"
        f"Draft query:\n{draft}\n\n"
        f"What that query returned:\n{_preview(winner_qr, _max_rows())}\n\n"
        "Does this result answer the question? If not, correct the query."
    )
    try:
        rev = safe_invoke_with_structured_output(
            llm,
            [SystemMessage(content=_SYSTEM), HumanMessage(content=human)],
            RevisionModel,
        )
    except Exception as exc:
        stats["reason"] = f"llm_error:{type(exc).__name__}"
        return None, stats
    if rev is None:
        stats["reason"] = "llm_none"
        return None, stats

    verdict = str(getattr(rev, "verdict", "") or "").strip().upper()
    stats["verdict"] = verdict[:20]
    stats["diagnosis"] = str(getattr(rev, "diagnosis", "") or "")[:300]
    if not verdict.startswith("WRONG"):
        stats["reason"] = "kept_correct"
        return None, stats

    new_sql = _clean(getattr(rev, "sql", "") or "")
    if not new_sql:
        stats["reason"] = "empty_rewrite"
        return None, stats
    if new_sql.upper() == draft.rstrip(";").strip().upper():
        stats["reason"] = "identical"
        return None, stats

    try:
        probe = run_sql(new_sql, connector)
    except Exception as exc:
        stats["reason"] = f"exec_error:{type(exc).__name__}"
        return None, stats
    if getattr(probe, "error", None):
        stats["reason"] = "exec_failed"
        stats["exec_error"] = str(probe.error)[:200]
        return None, stats

    try:
        challenger = winner.model_copy(
            update={
                "sql_code": new_sql,
                "thought": f"Ship rewrite: {stats.get('diagnosis', '')[:400]}",
            }
        )
    except Exception:
        stats["reason"] = "model_copy_failed"
        return None, stats

    stats["applied"] = True
    stats["reason"] = "replaced"
    logger.info("verify_revise: shipped query rewritten after WRONG verdict")
    return challenger, stats


_FORCE_FIX_VARIANTS = (
    (
        "The draft SQL is WRONG — its result does not correctly answer the "
        "question. Diagnose the bug and write corrected SQL. Prefer the draft's "
        "tables/columns when possible. Raw SQL only in sql. Never repeat the draft."
    ),
    (
        "You must produce a DIFFERENT SQL than the draft. The shipped result is "
        "incorrect. Fix join/filter/aggregation/distinct/null/date/percent issues. "
        "Raw SQL only. Do not copy the draft."
    ),
    (
        "Write the SQL a careful analyst would ship for this question, given that "
        "the draft's result is known-wrong. Minimal change from the draft if a "
        "small fix works; otherwise rewrite. Raw SQL only."
    ),
    (
        "Identify the single most likely bug (wrong table, missing WHERE, wrong "
        "GROUP BY, extra join, percent scaling). Output only the fixed SQL."
    ),
    (
        "The question's evidence/formula is authoritative. The draft violated it. "
        "Rewrite SQL to match the evidence exactly. Raw SQL only."
    ),
)


class ForceFixModel(BaseModel):
    diagnosis: str = Field(description="Why the draft result is wrong.")
    sql: str = Field(description="Corrected SQL that answers the question.")


def _query_result_signature(qr: Any):
    """Match sql_selection._result_signature without importing that module."""
    import json

    if getattr(qr, "error", None) or not getattr(qr, "result", None):
        return None
    try:
        rows = json.loads(qr.result[0])
    except (TypeError, ValueError, IndexError):
        return None
    if not isinstance(rows, list):
        return None
    try:
        value_rows = [
            tuple(json.dumps(v, sort_keys=True, default=str) for v in r.values())
            for r in rows
        ]
        return tuple(sorted(value_rows))
    except Exception:
        return None


def maybe_harvest_force_switch(
    *,
    question: str,
    winner_idx: int,
    candidates: list,
    query_responses: list,
    signatures: list,
    llm: Any,
    run_sql=None,
    connector: Any = None,
    evidence: str = "",
) -> tuple[int, dict]:
    """Force-fix the winner with K prompt variants; switch if they agree.

    Fail-closed: only switches when ≥agree independent fixes share a result
    signature different from the winner *and* that signature already exists on
    another pool candidate (draft or revision). Never free-ships novel SQL.
    """
    stats: dict = {
        "triggered": True,
        "applied": False,
        "reason": "",
        "consensus_n": 0,
        "from_idx": winner_idx,
        "to_idx": winner_idx,
        "n_ok_samples": 0,
    }
    if not harvest_force_enabled():
        stats["triggered"] = False
        stats["reason"] = "disabled"
        return winner_idx, stats
    if winner_idx < 0 or winner_idx >= len(candidates):
        stats["reason"] = "bad_winner"
        return winner_idx, stats
    if run_sql is None or connector is None:
        stats["reason"] = "no_executor"
        return winner_idx, stats

    from collections import Counter

    from gsf.utils.llm_invoke import get_llm_client, safe_invoke_with_structured_output
    from langchain_core.messages import HumanMessage, SystemMessage

    winner = candidates[winner_idx]
    draft = getattr(winner, "sql_code", "") or ""
    qr = query_responses[winner_idx] if winner_idx < len(query_responses) else None
    win_sig = signatures[winner_idx] if winner_idx < len(signatures) else None
    result_text = _preview(qr, _max_rows()) if qr is not None else "(not executed)"
    ev_b = f"Evidence:\n{evidence}\n\n" if (evidence or "").strip() else ""
    human = (
        f"Question:\n{question}\n\n{ev_b}"
        f"WRONG draft SQL:\n{draft}\n\n"
        f"Wrong result:\n{result_text}\n\n"
        "Write corrected SQL only."
    )

    model = harvest_force_model()
    judge = llm
    if model:
        try:
            judge = get_llm_client(model=model, temperature=0.0, max_tokens=16000)
        except Exception:
            judge = llm

    k = harvest_force_k()
    variants = _FORCE_FIX_VARIANTS[:k]
    draft_key = draft.rstrip(";").strip().upper()
    votes: Counter = Counter()
    n_ok = 0
    for system in variants:
        try:
            out = safe_invoke_with_structured_output(
                judge,
                [SystemMessage(content=system), HumanMessage(content=human)],
                ForceFixModel,
            )
        except Exception as exc:
            logger.debug("harvest_force sample failed (%s)", exc)
            continue
        if out is None:
            continue
        new_sql = _clean(getattr(out, "sql", "") or "")
        if not new_sql or new_sql.upper() == draft_key:
            continue
        try:
            probe = run_sql(new_sql, connector)
        except Exception as exc:
            logger.debug("harvest_force exec failed (%s)", exc)
            continue
        if getattr(probe, "error", None):
            continue
        matched_sig = _query_result_signature(probe)
        if matched_sig is None:
            continue
        votes[matched_sig] += 1
        n_ok += 1

    stats["n_ok_samples"] = n_ok
    agree = harvest_force_agree()
    consensus = None
    consensus_n = 0
    for sig, cnt in votes.most_common():
        if cnt >= agree and sig != win_sig:
            consensus, consensus_n = sig, cnt
            break
    stats["consensus_n"] = consensus_n
    if consensus is None:
        stats["reason"] = "no_consensus"
        return winner_idx, stats

    for i, sig in enumerate(signatures):
        if sig == consensus and i != winner_idx:
            stats["applied"] = True
            stats["reason"] = "consensus_pool_switch"
            stats["to_idx"] = i
            logger.info(
                "harvest_force: winner %d → pool %d (agree=%d)",
                winner_idx,
                i,
                consensus_n,
            )
            return i, stats

    stats["reason"] = "consensus_not_in_pool"
    return winner_idx, stats


def revise_pool(
    *,
    question: str,
    candidates: list,
    query_responses: list,
    schema_block: str,
    llm: Any,
    run_sql,
    connector: Any,
) -> list:
    """Audit each draft against its own result; return revisions to append.

    Only revisions that differ from their draft and execute without error are
    returned, so a revision can never introduce a query that is worse than
    unusable. Failures are swallowed per candidate: this is an enhancement pass
    and must not be able to take down generation.
    """
    if not enabled() or not candidates:
        return []

    from gsf.utils.llm_invoke import safe_invoke_with_structured_output
    from langchain_core.messages import HumanMessage, SystemMessage

    max_rows = _max_rows()
    seen = {
        (getattr(c, "sql_code", "") or "").strip().rstrip(";").strip().upper()
        for c in candidates
    }
    out: list = []
    n_drafts = min(len(candidates), _max_candidates())
    plurality_sig = _draft_plurality_sig(query_responses, n_drafts)
    dropped = {"empty_or_error": 0, "same_as_draft": 0, "same_as_plurality": 0}
    targets = revise_targets(query_responses, n_drafts)
    mode = revise_when_mode()
    logger.info(
        "verify_revise: when=%s targeting %d/%d drafts %s",
        mode,
        len(targets),
        n_drafts,
        [(i, r) for i, r in targets],
    )

    for idx, why in targets:
        if idx >= len(candidates):
            continue
        cand = candidates[idx]
        draft = getattr(cand, "sql_code", "") or ""
        if not draft.strip():
            continue
        qr = query_responses[idx] if idx < len(query_responses) else None
        result_text = _preview(qr, max_rows) if qr is not None else "(not executed)"

        if why == "unanimous_suspect":
            system = _SYSTEM_UNANIMOUS_SKEPTIC
            human = (
                f"{question}\n\n"
                f"Database schema:\n{schema_block}\n\n"
                f"All {n_drafts} drafts returned the SAME result.\n"
                f"Representative draft:\n{draft}\n\n"
                f"Shared result:\n{result_text}\n\n"
                "Is this shared result wrong? If so, write SQL with a DIFFERENT result."
            )
        else:
            system = _SYSTEM
            human = (
                f"{question}\n\n"
                f"Database schema:\n{schema_block}\n\n"
                f"Draft query:\n{draft}\n\n"
                f"What that query returned:\n{result_text}\n\n"
                "Does this result answer the question? If not, correct the query."
            )
        try:
            rev = safe_invoke_with_structured_output(
                llm,
                [SystemMessage(content=system), HumanMessage(content=human)],
                RevisionModel,
            )
        except Exception as exc:
            logger.debug("verify_revise: candidate %d audit failed (%s)", idx, exc)
            continue
        if rev is None:
            continue

        for label in ("sql", "alternative"):
            new_sql = _clean(getattr(rev, label, "") or "")
            if not new_sql:
                continue
            key = new_sql.upper()
            if key in seen:
                continue
            try:
                probe = run_sql(new_sql, connector)
            except Exception as exc:
                logger.debug("verify_revise: revision %d did not execute (%s)", idx, exc)
                continue
            if getattr(probe, "error", None):
                continue
            ok, reason = _should_append_revision(
                probe=probe, draft_qr=qr, plurality_sig=plurality_sig
            )
            if not ok:
                dropped[reason] = dropped.get(reason, 0) + 1
                logger.debug(
                    "verify_revise: candidate %d %s dropped (%s)", idx, label, reason
                )
                continue
            try:
                revised = cand.model_copy(
                    update={
                        "sql_code": new_sql,
                        "thought": f"Verify/revise ({why}/{label}) on candidate {idx}: "
                        f"{getattr(rev, 'diagnosis', '')[:400]}",
                    }
                )
            except Exception:
                continue
            seen.add(key)
            out.append(revised)
            logger.info(
                "verify_revise: candidate %d (%s) -> %s %s added",
                idx,
                why,
                str(getattr(rev, "verdict", "")).strip().upper() or "?",
                label,
            )

    if any(dropped.values()):
        logger.info(
            "verify_revise: fail-alone dropped empty=%d same_draft=%d same_plurality=%d",
            dropped.get("empty_or_error", 0),
            dropped.get("same_as_draft", 0),
            dropped.get("same_as_plurality", 0),
        )
    return out
