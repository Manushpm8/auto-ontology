from __future__ import annotations

import logging

from gsf.utils.llm_invoke import safe_invoke_text

logger = logging.getLogger(__name__)

_GROUNDING_PROMPT = """\
You are preparing context for a SQL generation step.

User question: {question}

Domain definitions:
{formatted_kg}

List only the definitions, formulas, or rules above that are directly needed \
to answer this question. Format as concise bullet points. \
If none apply, output: NONE"""


def ground_external_knowledge(question: str, formatted_kg: str, llm) -> str:
    """Return only the knowledge items relevant to *question*, or '' if none."""
    if not formatted_kg:
        return ""
    prompt = _GROUNDING_PROMPT.format(question=question, formatted_kg=formatted_kg)
    response = safe_invoke_text(llm, prompt).strip()
    logger.info("Grounding — response: %s", response[:200])
    if response.upper() == "NONE":
        return ""
    return response
