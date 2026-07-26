from __future__ import annotations

import logging
from concurrent.futures import ThreadPoolExecutor, as_completed
from typing import Optional, TYPE_CHECKING

from langchain_core.messages import SystemMessage

from gsf.retrieval.text_to_sql.agents.entities_extraction import EntitiesExtractionModel
from gsf.retrieval.text_to_sql.prompts import create_entity_extraction_prompt
from gsf.retrieval.data_access.semantic_search import search_semantic_index
from gsf.semantic.constants import LABEL_COLUMN_ATTRIBUTE
from gsf.utils.llm_invoke import get_non_reasoning_llm_client, invoke_with_structured_output

if TYPE_CHECKING:
    from .state import InteractiveSessionState

logger = logging.getLogger(__name__)

_STUCK_PHRASES = frozenset([
    "not sure", "don't understand", "dont understand",
    "don't know", "dont know", "unclear", "i'm confused",
    "no idea", "not certain", "i don't", "i dont",
])

_CLARIFY_PROMPT = """\
You are deciding whether to ask the user a clarification question before writing SQL.

Database schema:
{db_schema}

External knowledge:
{external_kg}

User question: {question}

Prior clarifications (Q&A):
{history}

Potentially unresolvable terms (poor VDB match — best hit shown for context; judge whether it truly covers the term):
{unresolvable_terms}

Rules:
- do NOT ask the user where data is stored, which table or column to use, or how things are organised in the database. EXAMPLE: instead of asking "Which column in table X represents quality Y?" ask "Can you please define quality Y?"
- Only ask for information neither the database schema nor the external knowledge can provide: undefined business terms, acronyms, formulas, or domain concepts missing from both.
- If there are unresolvable terms listed above, prioritize asking about those — they are the most likely source of genuine ambiguity.
- Ask only if there is a specific, answerable ambiguity that would significantly change the SQL.
- If the user's last answer indicates they cannot answer (e.g. contains "not sure", "don't understand", "don't know"), do NOT repeat the same question — either try a completely different clarification angle or output PROCEED.
- If the schema has multiple similar columns, pick the most semantically appropriate one yourself — do not ask the user to choose.
- Output a single, focused question only — never join two questions with "and" or "or". In case of several unresolvable_terms, you may submit one question, and after recieving clarification, ask another one, until all ambiguities are resolved.
- If you already have enough information or have asked enough questions, output: PROCEED
- If you need to ask, output: ASK: <your question>

Output PROCEED or ASK: <question>:"""


def _last_answer_is_stuck(history: list[dict]) -> bool:
    if not history:
        return False
    last_answer = history[-1].get("a", "").lower()
    return any(phrase in last_answer for phrase in _STUCK_PHRASES)


def _find_unresolvable_entities(
    question: str,
    semantic_retriever: object,
    db_name: str | None,
) -> list[tuple[str, str | None]]:
    """Return entities extracted from *question* that have no VDB hits."""
    try:
        llm = get_non_reasoning_llm_client()
        messages = [SystemMessage(content=create_entity_extraction_prompt(question))]
        result = invoke_with_structured_output(llm, messages, EntitiesExtractionModel)
        if result is None:
            return []
        entities = [e.strip() for e in (result.required_entity_name or []) if e.strip()]
    except Exception:
        return []

    if not entities or semantic_retriever is None:
        return []

    # list of (entity, top_hit_text_or_None)
    unresolvable: list[tuple[str, str | None]] = []
    with ThreadPoolExecutor(max_workers=len(entities)) as pool:
        futures = {
            pool.submit(
                search_semantic_index,
                semantic_retriever,
                entity,
                [LABEL_COLUMN_ATTRIBUTE],
                2,
                db_name,
            ): entity
            for entity in entities
        }
        for future in as_completed(futures):
            entity = futures[future]
            try:
                hits = future.result()
                best_score = hits[0].get("score") if hits else None
                top_text = hits[0].get("text") if hits else None
                logger.info("Clarify — entity %r: %d hit(s), best score=%s, top hit=%s", entity, len(hits), best_score, top_text)
                if not hits or (best_score is not None and best_score > 0.55):
                    unresolvable.append((entity, top_text))
            except Exception:
                pass

    logger.info("Clarify — unresolvable entities: %s", [e for e, _ in unresolvable] or "none")
    return unresolvable


def should_clarify(
    session: "InteractiveSessionState",
    llm,
) -> tuple[bool, Optional[str]]:
    """Return (True, question) to ask, or (False, None) to proceed to SQL."""
    history_text = "\n".join(
        f"Q: {h['q']}\nA: {h['a']}" for h in session.clarify_history
    ) or "None"

    # If last answer was a "can't answer" signal, track it in history but
    # let the prompt rule handle pivoting/proceeding — don't short-circuit here
    # so the LLM can still try a different angle if genuinely useful.

    unresolvable = _find_unresolvable_entities(
        session.working_question,
        session.semantic_retriever,
        session.db_name,
    )
    if unresolvable:
        unresolvable_text = "\n".join(
            f"- {entity} (closest DB match: {top_hit or 'none'})"
            for entity, top_hit in unresolvable
        )
    else:
        unresolvable_text = "None"

    prompt = _CLARIFY_PROMPT.format(
        db_schema=session.db_schema[:3000],
        external_kg=session.external_kg,
        question=session.working_question,
        history=history_text,
        unresolvable_terms=unresolvable_text,
    )
    response = llm.invoke(prompt).content.strip()

    if response.upper().startswith("ASK:"):
        question = response[4:].strip()
        logger.info("Clarify — DECISION: ASK  (history len=%d)", len(session.clarify_history))
        return True, question
    logger.info("Clarify — DECISION: PROCEED  (history len=%d)", len(session.clarify_history))
    return False, None
