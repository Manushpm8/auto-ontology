from __future__ import annotations

_MERGE_PROMPT = """\
You are refining a database question that already incorporates previous clarifications.

Current question (do NOT discard any formula, definition, or constraint it contains):
{current_question}

New clarification:
Q: {new_q}
A: {new_a}

Rewrite the current question to incorporate the new clarification. Rules:
- Preserve every formula, definition, and constraint already in the current question.
- If the new clarification defines or renames a metric, use that name consistently everywhere \
  in the rewritten question — including in aggregations (average, median, count) that reference it.
- If the answer provides a formula or calculation for a metric, embed it explicitly \
  in the rewritten question using SQL-friendly notation \
  (e.g. "SNQI = snrratio - 0.1 * ABS(noisefloordbm)"). Use column names from the \
  database schema where inferable; otherwise use the user's exact terms.
- Do NOT introduce external definitions or formulas beyond what the user stated in the answer above.
- Output only the rewritten question, nothing else."""


def merge_clarification(
    current_question: str,
    new_turn: dict,
    llm,
) -> str:
    """Incrementally refine current_question with one new Q&A turn."""
    prompt = _MERGE_PROMPT.format(
        current_question=current_question,
        new_q=new_turn["q"],
        new_a=new_turn["a"],
    )
    return llm.invoke(prompt).content.strip()
