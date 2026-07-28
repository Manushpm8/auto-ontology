from __future__ import annotations

import logging
import re
import unicodedata
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
    "out of scope", "cannot answer", "can't answer", "unable to answer",
    "not able to answer", "i cannot", "i can't",
])

_CLARIFY_PROMPT = """\
You are deciding whether to ask the user a clarification question before writing SQL.

Database schema:
{db_schema}

Relevant external knowledge (definitions and formulas already known — do NOT ask about these):
{relevant_knowledge}

User question: {question}

Prior clarifications (Q&A):
{history}

Topics already asked about that went UNANSWERED (do NOT ask the exact same question again, but you MAY ask about the same concept from a different angle — e.g. ask for a definition or categorisation instead of a column location):
{unanswered_topics}

Terms not found in the database schema or external knowledge (ask the user to define these):
{unresolvable_terms}

{sort_direction_note}

STRICT RULES — follow every one of these exactly:
1. NEVER ask where data is stored. Do not ask about 'tables', 'columns', 'data', or 'schemas'. You have the schema — use it. If a term from history or external knowledge maps to a schema column by name or meaning (column names may differ in casing, e.g. "SnrRatio" maps to "snrratio"), resolve it from the schema without asking. BAD: "Which column does quality X refer to?"  GOOD: or "What is the exact formula or definition for quality X?"
2. Only ask for information not provided by the schema, relevant external knowledge, or history: undefined terms, acronyms, or exact formulas missing from all three. A metric being NAMED in external knowledge does NOT mean its computation formula is known — if the exact formula for computing a metric from database columns is not explicitly stated anywhere, ask for it.
3. Never re-ask about a topic the user could not answer (listed under "Topics already asked about that went UNANSWERED") — not even rephrased. You MAY ask follow-up questions on topics the user did answer (e.g. if they named a metric, you can ask for its formula).
4. If there are potentially unresolvable terms which do not have satisfactory definitions in the prior clarifications, relevant knowledge, or db_schema, you may ask about them one at a time. If the user cannot answer one, skip it and ask about the next.
5. Pick the most semantically appropriate column yourself when the schema has similar options — do not ask the user to choose.
6. Output a single focused question only — never two questions joined with "and" or "or".
7. Output PROCEED if you have enough information, have exhausted unresolvable terms, or cannot make further progress.

Output PROCEED or ASK: <question>:"""

_KG_COVERAGE_PROMPT = """\
External knowledge:
{formatted_kg}

For each term below, identify the external knowledge entries that directly define or provide \
the formula/threshold for that term. Only include entries whose definition or description \
gives the exact meaning, calculation, or threshold — not entries that merely mention or \
relate to the concept.

Example: for "item weight", include "Unit Weight Index (UWI)" \
(it defines the formula) but NOT "Shipment Volume Index" (it only relates to conditions).

Terms:
{entity_list}

Output in exactly this format (one line per term, pipe-separated entry names after YES):

<term>: YES | <entry name 1> | <entry name 2> | ...
<term>: NO
...\
"""

_FILLER = frozenset([
    # SQL aggregation / math
    "average", "median", "mean", "count", "total", "sum", "min", "max",
    "number", "value", "measure", "metric", "level", "score", "ratio",
    "rate", "index", "indicator", "standard", "deviation", "percentage",
    "coloumn",
    # Schema-structural words — stripping these improves VDB matching
    # e.g. "condition name" → "condition", "signal type" → "signal"
    "name", "type", "id", "key", "code", "label", "category", "of"
])


_SORT_TRIGGERS = re.compile(
    r"\b(sort|order by|rank|arrange)\b",
    re.IGNORECASE,
)
_SORT_DIRECTION = re.compile(
    r"\b(asc\b|desc\b|ascending|descending|"
    r"high(est)?(\s+first)?|low(est)?(\s+first)?|"
    r"larg(est)?(\s+first)?|small(est)?(\s+first)?|"
    r"best(\s+first)?|worst(\s+first)?|"
    r"increasing order|decreasing order|"
    r"top \d|bottom \d|"
    r"from (high(est)?|low(est)?|larg(est)?|small(est)?|best|worst)|"
    r"(high(est)?|low(est)?|larg(est)?|small(est)?|best|worst)\s+to\s+"
    r"(high(est)?|low(est)?|small(est)?|larg(est)?|worst|best))\b",
    re.IGNORECASE,
)


def _ambiguous_sort_direction(question: str) -> bool:
    """True when the question implies sorting but doesn't specify direction."""
    return bool(_SORT_TRIGGERS.search(question)) and not bool(_SORT_DIRECTION.search(question))


_ARTICLES = frozenset(["a", "an", "the"])


def _normalize_entity(entity: str) -> str:
    """Strip filler/aggregation words and leading articles so VDB search targets the core domain term.

    Filler words are only stripped when non-filler words remain — if every word is a filler
    (e.g. "score level"), the phrase is kept intact so the VDB still receives a meaningful query.
    """
    raw = entity.lower().split()
    non_filler = [w for w in raw if w not in _FILLER]
    tokens = non_filler if non_filler else raw
    while tokens and tokens[0] in _ARTICLES:
        tokens.pop(0)
    return " ".join(tokens)


# Unicode hyphen/dash variants that LLMs commonly emit instead of ASCII '-'
_UNICODE_HYPHENS = str.maketrans({
    "‐": "-",  # hyphen
    "‑": "-",  # non-breaking hyphen
    "‒": "-",  # figure dash
    "–": "-",  # en dash
    "—": "-",  # em dash
    "―": "-",  # horizontal bar
})


def _norm_key(s: str) -> str:
    """Lowercase + collapse unicode hyphens to ASCII for reliable key matching."""
    return unicodedata.normalize("NFC", s).translate(_UNICODE_HYPHENS).lower()


def _parse_kg_entries(formatted_kg: str) -> dict[str, str]:
    """Parse formatted KB string into {normalized_entry_name: full_entry_text}."""
    entries: dict[str, str] = {}
    current_name: str | None = None
    current_lines: list[str] = []
    for line in formatted_kg.splitlines():
        if line.startswith("- "):
            if current_name is not None:
                entries[_norm_key(current_name)] = "\n".join(current_lines)
            current_name = line[2:].strip()
            current_lines = [line]
        elif current_name is not None:
            current_lines.append(line)
    if current_name is not None:
        entries[_norm_key(current_name)] = "\n".join(current_lines)
    return entries


def _filter_covered_by_external_knowledge(
    entities: list[str],
    formatted_kg: str,
) -> tuple[set[str], str]:
    """Check which entities are covered by external knowledge.

    Returns (covered_set, relevant_kg_text). The LLM outputs YES | <entry name>
    for each covered term; we look up the verbatim entry ourselves so the content
    is never hallucinated. Uses the non-reasoning model for speed.
    """
    fast_llm = get_non_reasoning_llm_client()
    entity_list = "\n".join(f"- {e}" for e in entities)
    prompt = _KG_COVERAGE_PROMPT.format(formatted_kg=formatted_kg, entity_list=entity_list)
    response = fast_llm.invoke(prompt).content.strip()
    logger.info("Clarify — coverage LLM raw response:\n%s", response)

    kg_entries = _parse_kg_entries(formatted_kg)

    covered: set[str] = set()
    matched_entry_names: list[str] = []
    for line in response.splitlines():
        upper = line.upper()
        if ": YES" not in upper:
            continue
        # Strip an optional leading "COVERAGE:" prefix the LLM sometimes emits
        # before extracting the actual term (e.g. "COVERAGE: snqi: YES | ...")
        stripped = re.sub(r"^coverage\s*:\s*", "", line, count=1, flags=re.IGNORECASE)
        term = stripped.split(":")[0].strip().lstrip("- ").lower()
        covered.add(term)
        # Extract all pipe-separated entry names after YES, normalized for matching
        if "|" in line:
            after_yes = line.split("|", 1)[1]
            for entry_name in after_yes.split("|"):
                name = _norm_key(entry_name.strip())
                if name:
                    matched_entry_names.append(name)

    # Look up verbatim entries by fuzzy prefix match on the parsed KB dict
    relevant_lines: list[str] = []
    seen_names: set[str] = set()
    for entry_name in matched_entry_names:
        match = next(
            (k for k in kg_entries if k.startswith(entry_name) or entry_name.startswith(k)),
            None,
        )
        if match and match not in seen_names:
            seen_names.add(match)
            relevant_lines.append(kg_entries[match])

    relevant_kg_text = "\n".join(relevant_lines)
    logger.info("Clarify — external_kg covers: %s", covered or "none")
    logger.info("Clarify — relevant_kg_text stored (%d chars): %r", len(relevant_kg_text), relevant_kg_text[:300] if relevant_kg_text else "")
    return covered, relevant_kg_text


def _compact_schema(db_schema: str) -> str:
    """Return 'table: col1, col2, ...' lines — enough for the decide-LLM to know
    what columns exist without the verbosity of DDL + sample rows."""
    result: list[str] = []
    current_table: str | None = None
    columns: list[str] = []
    in_ddl = False  # True only between CREATE TABLE ( ... );
    for line in db_schema.splitlines():
        m = re.match(r'CREATE\s+TABLE\s+["\']?(\w+)["\']?\s*\(', line, re.IGNORECASE)
        if m:
            if current_table and columns:
                result.append(f"{current_table}: {', '.join(columns)}")
            current_table = m.group(1)
            columns = []
            in_ddl = True
        elif in_ddl:
            stripped = line.strip()
            # The closing "); " line ends the DDL block
            if stripped.startswith(");"):
                in_ddl = False
                continue
            if not stripped or stripped.startswith(("PRIMARY KEY", "FOREIGN KEY", "CONSTRAINT", ")")):
                continue
            col_name = stripped.split()[0]
            if col_name:
                columns.append(col_name)
    if current_table and columns:
        result.append(f"{current_table}: {', '.join(columns)}")
    return "\n".join(result) or db_schema[:3000]


def _last_answer_is_stuck(history: list[dict]) -> bool:
    if not history:
        return False
    last_answer = history[-1].get("a", "").lower()
    return any(phrase in last_answer for phrase in _STUCK_PHRASES)


def _find_unresolvable_entities(
    question: str,
    semantic_retriever: object,
    db_name: str | None,
    formatted_kg: str = "",
) -> tuple[list[tuple[str, str | None]], str]:
    """Return (unresolvable_entities, relevant_kg_text).

    KB check runs first so entities covered by external knowledge are never
    sent to the VDB. relevant_kg_text is '' when nothing is covered.
    """
    try:
        extraction_llm = get_non_reasoning_llm_client()
        messages = [SystemMessage(content=create_entity_extraction_prompt(question))]
        result = invoke_with_structured_output(extraction_llm, messages, EntitiesExtractionModel)
        if result is None:
            return [], ""
        entities = [e.strip() for e in (result.required_entity_name or []) if e.strip()]
    except Exception:
        return [], ""

    if not entities or semantic_retriever is None:
        return [], ""

    # Normalize (strip filler/structural words) and deduplicate before any search.
    # "median signal quality" and "signal quality" both → "signal quality" (one search).
    norm_to_original: dict[str, str] = {}
    for entity in entities:
        norm = _normalize_entity(entity)
        if norm and norm not in norm_to_original:
            norm_to_original[norm] = entity
    # Drop entities whose normalized form is a strict substring of another entity in the batch
    # e.g. "condition" ⊂ "atmospheric conditions" → drop; "signal dynamics" ⊄ "signal quality" → keep both
    all_norms = set(norm_to_original.keys())
    search_entities = [e for e in all_norms if not any(e != o and e in o for o in all_norms)]
    logger.info("Clarify — normalized entities: %s", search_entities)

    # KB check before VDB — no need to score terms external knowledge already explains.
    relevant_kg_text = ""
    if formatted_kg and search_entities:
        covered, relevant_kg_text = _filter_covered_by_external_knowledge(search_entities, formatted_kg)
        search_entities = [e for e in search_entities if e.lower() not in covered]
        logger.info("Clarify — after KB filter, sending to VDB: %s", search_entities or "none")

    if not search_entities:
        return [], relevant_kg_text

    unresolvable: list[tuple[str, str | None]] = []
    with ThreadPoolExecutor(max_workers=len(search_entities)) as pool:
        futures = {
            pool.submit(
                search_semantic_index,
                semantic_retriever,
                norm,
                [LABEL_COLUMN_ATTRIBUTE],
                2,
                db_name,
            ): norm
            for norm in search_entities
        }
        for future in as_completed(futures):
            norm = futures[future]
            try:
                hits = future.result()
                best_score = hits[0].get("score") if hits else None
                top_text = hits[0].get("text") if hits else None
                logger.info("Clarify — entity %r: %d hit(s), best score=%s, top hit=%s", norm, len(hits), best_score, top_text)
                if not hits or (best_score is not None and best_score > 0.65):
                    unresolvable.append((norm, top_text))
            except Exception:
                pass

    logger.info("Clarify — unresolvable after VDB: %s", [e for e, _ in unresolvable] or "none")
    return unresolvable, relevant_kg_text


def should_clarify(
    session: "InteractiveSessionState",
    llm,
) -> tuple[bool, Optional[str]]:
    """Return (True, question) to ask, or (False, None) to proceed to SQL."""
    def _fmt_turn(h: dict) -> str:
        a = h["a"]
        stuck = any(phrase in a.lower() for phrase in _STUCK_PHRASES)
        label = " [UNANSWERED — user could not clarify]" if stuck else ""
        return f"Q: {h['q']}\nA: {a}{label}"

    history_text = "\n".join(_fmt_turn(h) for h in session.clarify_history) or "None"

    unanswered = [
        h["q"] for h in session.clarify_history
        if any(phrase in h["a"].lower() for phrase in _STUCK_PHRASES)
    ]
    unanswered_topics_text = "\n".join(f"- {q}" for q in unanswered) if unanswered else "None"

    if session._cached_unresolvable_for != session.working_question:
        unresolvable, relevant_kg = _find_unresolvable_entities(
            session.working_question,
            session.semantic_retriever,
            session.db_name,
            session.external_kg,
        )
        session._cached_unresolvable = unresolvable
        session._cached_unresolvable_for = session.working_question
        # Always replace with the fresh KB result — never carry stale content forward.
        # An empty result is valid (entities not covered by KB this turn).
        session._grounded_kg = relevant_kg
        session._grounded_kg_for = session.working_question
    else:
        logger.info("Clarify — reusing cached unresolvable entities (question unchanged)")
    unresolvable = session._cached_unresolvable or []

    all_unresolvable = [e for e, _ in unresolvable]
    unresolvable_text = "\n".join(f"- {t}" for t in all_unresolvable) if all_unresolvable else "None"

    sort_ambiguous = _ambiguous_sort_direction(session.working_question)
    sort_direction_note = (
        "IMPORTANT: The question mentions sorting/ordering but does not specify "
        "ascending or descending. You MUST ask the user for the sort direction before proceeding."
        if sort_ambiguous else ""
    )
    logger.info("Clarify — sort direction ambiguous: %s", sort_ambiguous)

    grounded_kg_for_prompt = session._grounded_kg or "None"
    logger.info(
        "Clarify — feeding to decide-LLM | relevant_knowledge (%d chars): %r",
        len(grounded_kg_for_prompt),
        grounded_kg_for_prompt[:300],
    )
    prompt = _CLARIFY_PROMPT.format(
        db_schema=_compact_schema(session.db_schema),
        relevant_knowledge=grounded_kg_for_prompt,
        question=session.working_question,
        history=history_text,
        unanswered_topics=unanswered_topics_text,
        unresolvable_terms=unresolvable_text,
        sort_direction_note=sort_direction_note,
    )
    response = llm.invoke(prompt).content.strip()

    if response.upper().startswith("ASK:"):
        question = response[4:].strip()
        logger.info("Clarify — DECISION: ASK  (history len=%d)", len(session.clarify_history))
        return True, question
    logger.info("Clarify — DECISION: PROCEED  (history len=%d)", len(session.clarify_history))
    return False, None
