from __future__ import annotations

from typing import Optional, TYPE_CHECKING

if TYPE_CHECKING:
    from .state import InteractiveSessionState

_CLARIFY_PROMPT = """\
You are deciding whether to ask the user a clarification question before writing SQL.

Database schema:
{db_schema}

External knowledge:
{external_kg}

User question: {question}

Prior clarifications (Q&A):
{history}

Rules:
- Ask only if there is a specific, answerable ambiguity that would significantly change the SQL.
- If you already have enough information or have asked enough questions, output: PROCEED
- If you need to ask, output: ASK: <your question>
- Keep questions short and targeted. One question at a time.

Output PROCEED or ASK: <question>:"""


def should_clarify(
    session: "InteractiveSessionState",
    llm,
) -> tuple[bool, Optional[str]]:
    """Return (True, question) to ask, or (False, None) to proceed to SQL."""
    history_text = "\n".join(
        f"Q: {h['q']}\nA: {h['a']}" for h in session.clarify_history
    ) or "None"

    prompt = _CLARIFY_PROMPT.format(
        db_schema=session.db_schema[:3000],
        external_kg=session.external_kg[:1000],
        question=session.working_question,
        history=history_text,
    )
    response = llm.invoke(prompt).content.strip()

    if response.upper().startswith("ASK:"):
        question = response[4:].strip()
        return True, question
    return False, None
