from __future__ import annotations

import logging

from gsf.utils.llm_invoke import safe_invoke_text

logger = logging.getLogger(__name__)

_COMPLETENESS_PROMPT = """\
You are managing a list of formulas and conditions that are still incompletely specified \
for SQL generation purposes.

Working question (for context on what SQL operations are needed):
{working_question}

Previously flagged gaps (may be empty):
{prior_gaps}

User's answer to the last clarification question:
Q: {last_q}
A: {last_a}

Relevant external knowledge (may contain partial or ambiguous definitions):
{relevant_kg}

Your task — produce a FRESH updated list:
1. DROP any prior gap that is now fully resolved by the user's answer above \
   (exact operators, constants, and column names given for every part of it).
2. KEEP any prior gap that the answer did not fully resolve.
3. ADD any new gap introduced by the answer or the external knowledge that would \
   prevent writing correct SQL — for example:
   - A formula whose exact operators, constants, or column combinations are still unknown
   - A threshold or classification condition expressed vaguely or in natural language \
     with no mapping to a specific column value
   - A KB condition using hedged language (e.g. "typically", "often") or referencing \
     an undefined sub-condition
   - A composite formula whose aggregation semantics are unspecified — i.e. the formula \
     combines multiple columns non-linearly and it is unclear at which level it should \
     be evaluated before grouping

Do NOT flag:
- Business context or motivation that does not affect SQL structure
- Terms fully defined by an explicit formula in the answer or KB
- Minor stylistic ambiguities a SQL generator can resolve on its own (e.g. sign \
  handling, boundary operators, or standard division-by-zero conventions)

Output one line per remaining gap, ordered from most to least critical for SQL correctness \
(e.g. a missing core formula blocks SQL entirely; a missing secondary threshold is lower priority):
  INCOMPLETE: <term> | <what is still missing>

If no gaps remain, output:
  COMPLETE"""

_COMPLETENESS_PROMPT_KB_ONLY = """\
You are scanning external knowledge entries for formulas or conditions that are \
ambiguously defined and would prevent writing correct SQL.

Working question (for context on what SQL operations are needed):
{working_question}

Relevant external knowledge:
{relevant_kg}

Identify any terms, formulas, or conditions in the external knowledge that:
- Are expressed with hedged language ("typically", "often", "approximately")
- Reference sub-conditions or sub-metrics that are not mapped to specific column values
- Cannot be directly translated to SQL without further information from the user

A gap is only significant if it would prevent writing correct SQL.

Do NOT flag:
- Terms fully defined with exact column names and operators
- Business context that does not affect SQL structure
- Minor ambiguities a SQL generator can resolve on its own

Output one line per gap found, ordered from most to least critical:
  INCOMPLETE: <term> | <what is still missing>

If everything is clearly defined for SQL generation, output:
  COMPLETE"""


def _parse_gaps(response: str) -> list[tuple[str, str]]:
    if response.upper().startswith("COMPLETE"):
        return []
    gaps: list[tuple[str, str]] = []
    seen: set[str] = set()
    for line in response.splitlines():
        if line.upper().startswith("INCOMPLETE:"):
            body = line[len("INCOMPLETE:"):].strip()
            if "|" in body:
                term, missing = body.split("|", 1)
                term = term.strip()
            else:
                term, missing = body.strip(), "exact specification missing"
            if term not in seen:
                seen.add(term)
                gaps.append((term, missing.strip()))
    return gaps


def detect_incomplete_formulas(
    working_question: str,
    last_turn: dict | None,
    relevant_kg: str,
    current_gaps: list[tuple[str, str]],
    llm,
) -> list[tuple[str, str]]:
    """Return an updated (term, what_is_missing) gap list.

    When last_turn is None (turn 0): scans only the relevant KB for ambiguous
    conditions — no user answer to evaluate.
    When last_turn is provided: evaluates the answer against prior gaps and KB,
    dropping resolved terms, keeping unresolved ones, adding new gaps.
    """
    if last_turn is None:
        prompt = _COMPLETENESS_PROMPT_KB_ONLY.format(
            working_question=working_question,
            relevant_kg=relevant_kg or "None",
        )
    else:
        prior_text = "\n".join(f"- {t}: {m}" for t, m in current_gaps) if current_gaps else "None"
        prompt = _COMPLETENESS_PROMPT.format(
            working_question=working_question,
            prior_gaps=prior_text,
            last_q=last_turn["q"],
            last_a=last_turn["a"],
            relevant_kg=relevant_kg or "None",
        )

    response = safe_invoke_text(llm, prompt).strip()
    logger.info("Completeness — raw response: %s", response[:400])
    gaps = _parse_gaps(response)
    logger.info("Completeness — gaps found: %s", gaps)
    return gaps
