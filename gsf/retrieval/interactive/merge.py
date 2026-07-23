from __future__ import annotations

_MERGE_PROMPT = """\
You are rewriting a database question to make it precise and unambiguous.

Original question: {original_question}

Clarifications obtained:
{history}

Rewrite the question as a single, specific, unambiguous question incorporating all clarifications.
Output only the rewritten question, nothing else."""


def merge_clarification(
    original_question: str,
    clarify_history: list[dict],
    llm,
) -> str:
    """Return merged working question from original + all Q&A."""
    if not clarify_history:
        return original_question
    history_text = "\n".join(f"Q: {h['q']}\nA: {h['a']}" for h in clarify_history)
    prompt = _MERGE_PROMPT.format(
        original_question=original_question,
        history=history_text,
    )
    return llm.invoke(prompt).content.strip()
