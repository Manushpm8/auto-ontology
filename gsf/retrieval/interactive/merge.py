from __future__ import annotations

_MERGE_PROMPT = """\
You are refining a database question that already incorporates previous clarifications.

Current question (do NOT discard any formula, definition, or constraint it contains):
{current_question}

New clarification:
Q: {new_q}
A: {new_a}

External knowledge (for context only — already passed to SQL generation separately; \
use it to avoid coining pseudo-column names):
{relevant_kg}

Rewrite the current question to incorporate the new clarification. Rules:
- Preserve every formula, definition, and constraint already in the current question.
- If the new clarification defines or renames a metric, use that name consistently everywhere \
  in the rewritten question — including in aggregations (average, median, count) that reference it.
- If the answer provides a formula or calculation for a metric, embed it explicitly \
  in the rewritten question using SQL-friendly notation \
  (e.g. "composite_score = TechSigProb * (1 - NatSrcProb) * SigUnique * (0.5 + AnomScore/10)"). Use column names from the \
  database schema where inferable; otherwise use the user's exact terms.
- Do NOT introduce external definitions or formulas beyond what the user stated in the answer above.
- Do NOT invent column names. If a metric is a computed expression (e.g. defined in external \
  knowledge), refer to it by its formula or its KB name, not a made-up column name.
- Output only the rewritten question, nothing else."""


def merge_clarification(
    current_question: str,
    new_turn: dict,
    llm,
    relevant_kg: str = "",
) -> str:
    """Incrementally refine current_question with one new Q&A turn."""
    prompt = _MERGE_PROMPT.format(
        current_question=current_question,
        new_q=new_turn["q"],
        new_a=new_turn["a"],
        relevant_kg=relevant_kg or "None",
    )
    return llm.invoke(prompt).content.strip()
