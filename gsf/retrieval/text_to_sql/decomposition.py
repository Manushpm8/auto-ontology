# SPDX-FileCopyrightText: Copyright (c) 2026, NVIDIA CORPORATION & AFFILIATES.
# All rights reserved.
# SPDX-License-Identifier: Apache-2.0

"""Multi-step answering: configuration and prior-answer evidence.

A request that contains several steps is split during question extraction (see
``QuestionDecompositionAgent``) and then answered one step at a time, each step
making a full pass through the node graph. This module owns the two pieces that
sit between passes: the knobs that bound the loop, and the rendering of already
answered steps into the ``evidence`` string the next pass reads.

Evidence is the carrier on purpose. It is already the channel for authoritative
facts the agent must not second-guess -- ``sql_from_semantic`` injects it as
"## Authoritative Evidence" and the SQL prompt is instructed to apply it
verbatim -- which is exactly the standing a previously computed step needs.
"""

from __future__ import annotations

import json
import os
from dataclasses import dataclass

_TRUTHY = {"1", "true", "yes", "on"}


def _int_env(name: str, default: int) -> int:
    try:
        return int(os.environ.get(name, "").strip() or default)
    except (TypeError, ValueError):
        return default


def is_decomposition_enabled() -> bool:
    """Whether a multi-step request is split and answered step by step.

    Off by default. Splitting costs one extra LLM call on every question and a
    full graph pass per extra step, so the win on genuinely multi-step requests
    is paid for by every single-step one. Opt in via ``QUESTION_DECOMPOSITION``.
    """
    return os.environ.get("QUESTION_DECOMPOSITION", "").strip().lower() in _TRUTHY


# Upper bound on graph passes for one question. Also handed to the decomposer,
# which is told to give up and return the original question rather than emit a
# longer chain -- a request needing more steps than this is one the split is
# unlikely to get right anyway.
MAX_SUB_QUESTIONS = _int_env("QUESTION_DECOMPOSITION_MAX_STEPS", 5)

# Per-step cap on the rendered result rows carried forward. A step that returns
# thousands of rows is a listing, and a later step needs to know what it found,
# not every row of it; without a cap one such step would crowd the SQL prompt.
MAX_RESULT_CHARS = _int_env("QUESTION_DECOMPOSITION_MAX_RESULT_CHARS", 2000)


@dataclass(frozen=True)
class SubAnswer:
    """One answered step, as carried into the steps that follow it."""

    question: str
    sql: str
    result: str


def _render_result(sql_response_from_db: object) -> str:
    """Render a step's DB rows as compact JSON, capped at ``MAX_RESULT_CHARS``.

    ``SQLExecutionAgent`` stores a single-element list holding a JSON string.
    It is re-parsed here rather than passed through so the text handed to the
    next step is known-valid JSON and so truncation lands on a row boundary
    instead of mid-token.
    """
    if not sql_response_from_db:
        return "(no rows)"

    payload = sql_response_from_db
    if isinstance(payload, list) and len(payload) == 1:
        payload = payload[0]

    if isinstance(payload, str):
        try:
            payload = json.loads(payload)
        except (TypeError, ValueError):
            return payload[:MAX_RESULT_CHARS]

    if isinstance(payload, list):
        if not payload:
            return "(no rows)"
        kept: list[object] = []
        size = 0
        for row in payload:
            rendered = json.dumps(row, default=str)
            if kept and size + len(rendered) > MAX_RESULT_CHARS:
                omitted = len(payload) - len(kept)
                return (
                    f"{json.dumps(kept, default=str)}\n"
                    f"({omitted} further row(s) omitted)"
                )
            kept.append(row)
            size += len(rendered)
        return json.dumps(kept, default=str)

    return str(payload)[:MAX_RESULT_CHARS]


def summarize_sub_answer(question: str, answer: dict) -> SubAnswer:
    """Capture what a completed graph pass computed for *question*."""
    return SubAnswer(
        question=question,
        sql=str(answer.get("sql_code") or "").strip(),
        result=_render_result(answer.get("sql_response_from_db")),
    )


def build_step_evidence(base_evidence: str, answers: list[SubAnswer]) -> str:
    """Prepend *base_evidence* to the results of the steps already answered.

    The caller's own evidence keeps its position at the top so a domain formula
    it supplies still reads as the primary instruction; the computed steps are
    appended as facts rather than as instructions.
    """
    if not answers:
        return base_evidence

    blocks = [
        f"### Step {index}: {answer.question}\n"
        f"SQL used:\n{answer.sql or '(none)'}\n"
        f"Result:\n{answer.result}"
        for index, answer in enumerate(answers, 1)
    ]
    steps = "\n\n".join(blocks)
    section = f"""## Results of earlier steps

This request was split into steps and the steps below have already been answered
against this database. Their results are established facts — reuse them instead of
recomputing them, and do not contradict them. Answer only the question you are given
now; the earlier steps are context for it, not part of it.

{steps}"""

    return f"{base_evidence}\n\n{section}" if base_evidence else section


__all__ = [
    "MAX_RESULT_CHARS",
    "MAX_SUB_QUESTIONS",
    "SubAnswer",
    "build_step_evidence",
    "is_decomposition_enabled",
    "summarize_sub_answer",
]
