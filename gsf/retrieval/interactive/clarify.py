from __future__ import annotations

import logging
import re
import time
import unicodedata
from concurrent.futures import ThreadPoolExecutor, as_completed
from typing import Optional, TYPE_CHECKING

import requests
from langchain_core.messages import SystemMessage

from gsf.retrieval.text_to_sql.agents.entities_extraction import EntitiesExtractionModel
from gsf.retrieval.text_to_sql.prompts import create_entity_extraction_prompt
from gsf.retrieval.data_access.semantic_search import search_semantic_index
from gsf.semantic.constants import LABEL_COLUMN_ATTRIBUTE
from gsf.utils.llm_invoke import get_non_reasoning_llm_client, invoke_with_structured_output, safe_invoke_text, LLM_INVOKE_TIMEOUT_S, RETRY_MAX_ATTEMPTS

if TYPE_CHECKING:
    from .state import InteractiveSessionState

logger = logging.getLogger(__name__)

_STUCK_PHRASES = frozenset([
    "out of scope", "not certain", "uncertain","cannot answer",
    "can't answer", "unable to answer", "not able to answer",
])

_CLARIFY_PROMPT = """\
You are deciding whether to ask the user a clarification question before writing SQL.

Database schema:
{db_schema}

Relevant external knowledge:
{relevant_knowledge}

Schema mappings already resolved (use these directly — do NOT ask where these terms are stored):
{resolved_schema_terms}

User question: {question}

Prior clarifications (Q&A):
{history}

Topics already asked about that went UNANSWERED (do NOT ask the exact same question again, but you MAY ask about the same concept from a different angle — e.g. ask for a definition instead of a column location):
{unanswered_topics}

Terms not found in the database schema or external knowledge (ask the user to define these):
{unresolvable_terms}

Formulas or conditions whose exact specification is still missing, ranked most-critical first (ask about the first unasked item before proceeding):
{incomplete_formulas_note}

{sort_direction_note}
{turns_hint}

STRICT RULES — follow every one of these exactly:
1. NEVER ask where data is stored. Do not ask about 'tables', 'columns', 'data', or 'schema'. If a term from history or external knowledge maps to a schema column by name or meaning (column names may differ in casing), resolve it from the schema without asking. BAD: "Which column stores quality X?"  GOOD: or "What is the exact formula for quality X?". The user has explicit instructions to not "answer any questions about the underlying database schema (including table or column names)".
1a. Exception to rule 1: if "Formulas or conditions whose exact specification is still missing" flags that column names for a user-defined formula are unknown, you MAY ask the user to name those specific identifiers — e.g. "What are the exact column names for battery life and battery capacity in your formula?" This is asking the user to complete their own formula specification, not asking about the database structure.
2. Only ask for information not provided by the schema, relevant external knowledge, resolved schema mappings, or history: undefined terms, acronyms, or exact formulas missing from all four. A metric being NAMED in external knowledge does NOT mean its computation formula is known — if the exact formula for computing a metric from database columns is not explicitly stated anywhere, ask for it.
3. Never re-ask about a topic the user could not answer (listed under "Topics already asked about that went UNANSWERED") — not even rephrased. You MAY ask follow-up questions on topics the user did answer (e.g. when they say "X is calculated by combining Y and Z", you can ask for the exact formula for X).
4. If there are potentially unresolvable terms which do not have satisfactory definitions in the prior clarifications, relevant knowledge, or db_schema, ask about them one at a time.
5. Pick the most semantically appropriate column yourself when the schema has similar options — do not ask the user to choose.
6. If anything else in the user's question seems unclear, you may ask about it - for example, ambiguous grouping term, thresholds, or normalization methods.
7. Output a single focused question only — never two questions joined with "and" or "or".
8. Output PROCEED only when BOTH of the following hold: (a) you have enough information to write correct SQL, AND (b) every term in "Terms not found in the database schema or external knowledge" is either already in the unanswered topics list or fully defined by the working question. If the unresolvable terms list contains any term not covered by those two sources, ASK about the most important one before proceeding.

Output PROCEED or ASK: <question>:"""

_KG_COVERAGE_PROMPT = """\
User question (for context only — use it to understand what each term means in this query):
{question}

External knowledge:
{formatted_kg}

For each term below, identify the external knowledge entries that directly define or provide \
the formula/threshold for that term as it is used in the question above. Only include entries \
whose definition or description gives the exact meaning, calculation, or threshold — not \
entries that merely mention or relate to the concept.

Example: for "item weight", include "Unit Weight Index (UWI)" \
(it defines the formula) but NOT "Shipment Volume Index" (it only relates to conditions).

Terms:
{entity_list}

Output in exactly this format (one line per term, entry names after YES separated by " ;; "):

<term>: YES ;; <entry name 1> ;; <entry name 2> ;; ...
<term>: NO
...\
"""

_FILLER = frozenset([
    # SQL aggregation / math
    "average", "median", "mean", "count", "total", "sum", "min", "max",
    "number", "value", "measure", "metric", "level", "score", "ratio",
    "rate", "index", "indicator", "standard", "deviation", "percentage",
    "column",
    # Schema-structural words — stripping these improves VDB matching
    # e.g. "condition name" → "condition", "signal type" → "signal"
    "name", "type", "id", "key", "code", "label", "category",
])

# Structural connectives — always stripped from anywhere in the phrase,
# never counted toward the filler threshold (unlike _FILLER words).
# e.g. "number of records" → ["number", "records"] before threshold check.
_CONNECTIVES = frozenset(["of", "by"])


_SORT_TRIGGERS = re.compile(
    r"\b(sort(ed)?|order(ed)?\s+by|rank(ed)?|arrange(d)?)\b",
    re.IGNORECASE,
)
_SORT_DIRECTION = re.compile(
    r"\b(asc\b|desc\b|ascending|descending|"
    # bare high/low etc. only count when followed by "first" — otherwise they're adjectives
    r"high(est)?\s+first|low(est)?\s+first|"
    r"larg(est)?\s+first|small(est)?\s+first|"
    r"best\s+first|worst\s+first|"
    r"increasing order|decreasing order|"
    r"top \d|bottom \d|"
    r"from (high(est)?|low(est)?|larg(est)?|small(est)?|best|worst)|"
    r"(high(est)?|low(est)?|larg(est)?|small(est)?|worst|best)\s+to\s+"
    r"(high(est)?|low(est)?|small(est)?|larg(est)?|worst|best))\b",
    re.IGNORECASE,
)


def _ambiguous_sort_direction(question: str) -> bool:
    """True when the question implies sorting but doesn't specify direction."""
    return bool(_SORT_TRIGGERS.search(question)) and not bool(_SORT_DIRECTION.search(question))


_ARTICLES = frozenset(["a", "an", "the"])


def _normalize_entity(entity: str) -> str:
    """Strip filler/aggregation words and leading articles so VDB search targets the core domain term.

    Connectives ("of", "by") are stripped first and never count toward the filler threshold.
    Filler words are only stripped when they make up half or fewer of the remaining words —
    if every word is a filler (e.g. "score level") or strictly more than half are fillers
    (e.g. "total point count"), the phrase is kept intact so the VDB still receives a
    meaningful query.
    """
    raw = [w for w in entity.lower().split() if w not in _CONNECTIVES]
    non_filler = [w for w in raw if w not in _FILLER]
    if not non_filler or len(non_filler) < len(raw) / 2:
        tokens = raw
    else:
        tokens = non_filler
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


def _slim_kg_for_coverage(formatted_kg: str) -> str:
    """Strip Definition lines so the coverage prompt is ~3x smaller.

    The coverage LLM only needs entry names and descriptions to decide
    which KB entries match an extracted entity. The full definitions are
    looked up separately from the original formatted_kg, so nothing is lost.
    """
    return "\n".join(
        line for line in formatted_kg.splitlines()
        if not line.startswith("  Definition:")
    )


_MAX_CHILDREN_PER_PARENT = 5


def _filter_covered_by_external_knowledge(
    entities: list[str],
    formatted_kg: str,
    question: str = "",
    children_map: dict[str, list[str]] | None = None,
) -> tuple[set[str], str]:
    """Check which entities are covered by external knowledge.

    Returns (covered_set, relevant_kg_text). The LLM outputs YES | <entry name>
    for each covered term; we look up the verbatim entry ourselves so the content
    is never hallucinated. Uses the non-reasoning model for speed.

    When children_map is provided, child entries of any matched parent are appended
    to relevant_kg_text with their full text (name + description + definition),
    bypassing the coverage LLM. Capped at _MAX_CHILDREN_PER_PARENT per parent.
    """
    fast_llm = get_non_reasoning_llm_client()
    entity_list = "\n".join(f"- {e}" for e in entities)
    slim_kg = _slim_kg_for_coverage(formatted_kg)
    prompt = _KG_COVERAGE_PROMPT.format(
        formatted_kg=slim_kg,
        entity_list=entity_list,
        question=question or "(not provided)",
    )
    def _is_valid_coverage_response(r: str, n_entities: int) -> bool:
        """True when r looks like structured YES/NO lines, not prose."""
        lines = [l for l in r.splitlines() if l.strip()]
        if not lines:
            return False
        # A valid response has ~n_entities lines. Responses with far more lines
        # are chain-of-thought prose that happens to contain ": YES"/": NO" substrings.
        if len(lines) > n_entities * 4 + 10:
            return False
        structured = sum(1 for l in lines if ": YES" in l.upper() or ": NO" in l.upper())
        return structured >= max(1, n_entities // 2)

    response = ""
    for attempt in range(RETRY_MAX_ATTEMPTS):
        try:
            response = safe_invoke_text(fast_llm, prompt).strip()
            if response and _is_valid_coverage_response(response, len(entities)):
                break
            if response:
                logger.warning(
                    "Clarify — coverage LLM returned prose on attempt %d/%d — retrying",
                    attempt + 1, RETRY_MAX_ATTEMPTS,
                )
            else:
                logger.warning(
                    "Clarify — coverage LLM returned empty on attempt %d/%d — retrying",
                    attempt + 1, RETRY_MAX_ATTEMPTS,
                )
            response = ""
        except requests.exceptions.ReadTimeout:
            logger.warning(
                "Clarify — coverage LLM timed out on attempt %d/%d",
                attempt + 1, RETRY_MAX_ATTEMPTS,
            )
        except Exception as e:
            logger.error("Clarify — coverage LLM error on attempt %d/%d: %s", attempt + 1, RETRY_MAX_ATTEMPTS, e)
            break
        if attempt < RETRY_MAX_ATTEMPTS - 1:
            time.sleep(2 ** (attempt + 1))
        else:
            logger.error("Clarify — coverage LLM failed after %d attempts; treating all entities as unresolvable", RETRY_MAX_ATTEMPTS)
    logger.info("Clarify — coverage LLM raw response:\n%s", response)

    kg_entries = _parse_kg_entries(formatted_kg)

    # Parse term → claimed entry names from coverage response.
    # Do NOT mark a term as covered yet — only do so after the KB lookup confirms
    # the entry actually exists. The coverage LLM sometimes hallucinates entry names
    # from its training data (e.g. SNQI when the benchmark has masked it from the KB).
    term_to_entry_names: dict[str, list[str]] = {}
    for line in response.splitlines():
        upper = line.upper()
        if ": YES" not in upper:
            continue
        # Strip an optional leading "COVERAGE:" prefix the LLM sometimes emits
        stripped = re.sub(r"^coverage\s*:\s*", "", line, count=1, flags=re.IGNORECASE)
        term = stripped.split(":")[0].strip().lstrip("- ").lower()
        entry_names: list[str] = []
        if ";;" in line:
            after_yes = line.split("YES", 1)[1]
            for entry_name in after_yes.split(";;"):
                name = _norm_key(entry_name.strip())
                if name:
                    entry_names.append(name)
        term_to_entry_names[term] = entry_names

    # Look up verbatim entries; only mark a term covered when a real KB entry is found.
    logger.debug("Clarify — term_to_entry_names: %s", term_to_entry_names)
    covered: set[str] = set()
    relevant_lines: list[str] = []
    seen_names: set[str] = set()
    # Normalize children_map keys once for efficient lookup
    norm_children_map: dict[str, list[str]] = (
        {_norm_key(k): v for k, v in children_map.items()} if children_map else {}
    )
    for term, entry_names in term_to_entry_names.items():
        for entry_name in entry_names:
            match = next(
                (k for k in kg_entries if k.startswith(entry_name) or entry_name.startswith(k)),
                None,
            )
            logger.debug("Clarify — lookup %r → match=%r", entry_name, match)
            if match and match not in seen_names:
                seen_names.add(match)
                relevant_lines.append(kg_entries[match])
                covered.add(term)  # confirmed: real KB entry exists
                # Inject children of this matched parent (full text, no coverage LLM needed)
                for child_text in norm_children_map.get(match, [])[:_MAX_CHILDREN_PER_PARENT]:
                    child_name = _norm_key(child_text.split("\n")[0].lstrip("- ").strip())
                    if child_name and child_name not in seen_names:
                        seen_names.add(child_name)
                        relevant_lines.append(child_text)
                        logger.debug("Clarify — injected child KB entry: %r (parent: %r)", child_name, match)

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


def _format_resolved_hit(entity: str, hit_text: str) -> str:
    """Format a VDB hit as a concise KB-style entry for evidence generation."""
    # hit_text is a ColumnAttribute description; strip the verbose prefix if present
    # e.g. "ColumnAttribute: Lunar Stage of Term ... lunarstage (char) ..."
    body = re.sub(r"^ColumnAttribute:[^.]+\.\s*", "", hit_text or "")
    return f"- {entity}\n  Definition: {body}" if body else f"- {entity}\n  Definition: {hit_text}"


def _format_resolved_schema_terms(resolved_hits: list[tuple[str, str, float]]) -> str:
    """Format resolved VDB hits as concise one-liners for the decide-LLM prompt."""
    lines = []
    for entity, hit_text, _ in resolved_hits:
        body = re.sub(r"^ColumnAttribute:[^.]+\.\s*", "", hit_text or "").rstrip()
        lines.append(f'- "{entity}" → {body}' if body else f'- "{entity}" → {hit_text}')
    return "\n".join(lines)


def _update_vdb_resolved_hits(
    session: "InteractiveSessionState",
    resolved_hits: list[tuple[str, str, float]],
) -> None:
    """Persist resolved hits to session. Confident hits (score<=0.63) go to evidence."""
    session._cached_resolved_hits = resolved_hits
    confident = [(e, t) for e, t, s in resolved_hits if s <= 0.63]
    if not confident:
        return
    for entity, hit_text in confident:
        entry = _format_resolved_hit(entity, hit_text)
        if entry not in session._vdb_resolved_hits:
            session._vdb_resolved_hits = (
                session._vdb_resolved_hits + "\n" + entry
                if session._vdb_resolved_hits
                else entry
            )


def expand_kg_with_children(formatted_kg: str, children_map: dict[str, list[str]]) -> str:
    """Return formatted_kg with child entries appended for every parent already present.

    Used by the debug/grounding path where the coverage LLM sees the full KB and
    children need to be visible inline. Deduplicates by normalized entry name.
    """
    if not children_map or not formatted_kg:
        return formatted_kg
    existing = set(_parse_kg_entries(formatted_kg).keys())
    extra: list[str] = []
    for parent_name, child_texts in children_map.items():
        if _norm_key(parent_name) not in existing:
            continue
        for child_text in child_texts[:_MAX_CHILDREN_PER_PARENT]:
            child_name = _norm_key(child_text.split("\n")[0].lstrip("- ").strip())
            if child_name and child_name not in existing:
                existing.add(child_name)
                extra.append(child_text)
    if not extra:
        return formatted_kg
    return formatted_kg + "\n" + "\n".join(extra)


def _find_unresolvable_entities(
    question: str,
    semantic_retriever: object,
    db_name: str | None,
    formatted_kg: str = "",
    children_map: dict[str, list[str]] | None = None,
) -> tuple[list[tuple[str, str | None]], list[tuple[str, str, float]], str, set[str]]:
    """Return (unresolvable_entities, resolved_hits, relevant_kg_text).

    KB check runs first so entities covered by external knowledge are never
    sent to the VDB. relevant_kg_text is '' when nothing is covered.
    resolved_hits contains (entity, hit_text, score) for entities found in the
    schema (score <= 0.65); the caller uses score <= 0.63 for evidence generation.
    """
    try:
        extraction_llm = get_non_reasoning_llm_client()
        messages = [SystemMessage(content=create_entity_extraction_prompt(question))]
        result = invoke_with_structured_output(extraction_llm, messages, EntitiesExtractionModel)
        if result is None:
            return [], [], "", set()
        entities = [e.strip() for e in (result.required_entity_name or []) if e.strip()]
    except Exception:
        return [], [], "", set()

    if not entities or semantic_retriever is None:
        return [], [], "", set()

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
    # Use original entity names for KB matching (e.g. "bandwidth ratio" matches
    # "Bandwidth-Frequency Ratio (BFR)") but keep normalized names for VDB column search.
    relevant_kg_text = ""
    if formatted_kg and search_entities:
        kb_entities = [norm_to_original.get(norm, norm) for norm in search_entities]
        orig_lower_to_norm = {norm_to_original.get(n, n).lower(): n for n in search_entities}
        covered_originals, relevant_kg_text = _filter_covered_by_external_knowledge(kb_entities, formatted_kg, question, children_map)
        covered_norms = {orig_lower_to_norm.get(orig, orig) for orig in covered_originals}
        search_entities = [e for e in search_entities if e not in covered_norms]
        logger.info("Clarify — after KB filter, sending to VDB: %s", search_entities or "none")

    if not search_entities:
        return [], [], relevant_kg_text, all_norms

    # Standalone generic terms (single token, no domain meaning) reliably produce
    # false-positive VDB matches via substring coincidence (e.g. "id" → "idle power").
    # Compound entities like "customer id" are multi-token and pass through normally.
    _GENERIC_STANDALONE = frozenset({
        "id", "ids", "key", "keys", "value", "values",
        "code", "codes", "type", "types",
    })
    vdb_entities = [e for e in search_entities if e not in _GENERIC_STANDALONE]
    skipped = [e for e in search_entities if e in _GENERIC_STANDALONE]
    if skipped:
        logger.info("Clarify — skipping VDB for generic standalone terms: %s", skipped)
    unresolvable: list[tuple[str, str | None]] = [(e, None) for e in skipped]
    resolved_hits: list[tuple[str, str, float]] = []  # (entity, hit_text, score)

    if not vdb_entities:
        return unresolvable, resolved_hits, relevant_kg_text, all_norms

    with ThreadPoolExecutor(max_workers=len(vdb_entities)) as pool:
        futures = {
            pool.submit(
                search_semantic_index,
                semantic_retriever,
                norm,
                [LABEL_COLUMN_ATTRIBUTE],
                2,
                db_name,
            ): norm
            for norm in vdb_entities
        }
        for future in as_completed(futures):
            norm = futures[future]
            try:
                hits = future.result()
                best_score = hits[0].get("score") if hits else None
                top_text = hits[0].get("text") if hits else None
                second_score = hits[1].get("score") if len(hits) >= 2 else None
                logger.info("Clarify — entity %r: %d hit(s), best score=%s, top hit=%s", norm, len(hits), best_score, top_text)
                ambiguous = second_score is not None and second_score <= 0.65
                if not hits or best_score is None or best_score > 0.65 or ambiguous:
                    if ambiguous:
                        logger.info("Clarify — entity %r: ambiguous (2nd hit score=%s), treating as unresolvable", norm, second_score)
                    unresolvable.append((norm, top_text))
                else:
                    resolved_hits.append((norm, top_text or "", best_score))
            except Exception:
                pass

    logger.info("Clarify — unresolvable after VDB: %s", [e for e, _ in unresolvable] or "none")
    logger.info("Clarify — resolved by VDB: %s", [(e, f"{s:.3f}") for e, _, s in resolved_hits] or "none")
    return unresolvable, resolved_hits, relevant_kg_text, all_norms


_PRUNE_RESOLVED_PROMPT = """\
Clarification question asked: {question}
User's answer: {answer}

The following terms could not be resolved from the database schema or external knowledge.
For each term, decide whether the user's answer now fully resolves it — i.e., the answer
explicitly defines what the term means, what data to show, or how to compute it.

Terms:
{terms}

Output one line per term, exactly:
<term>: RESOLVED
<term>: UNRESOLVED\
"""


def prune_resolved_terms(
    persistent: list[str],
    last_turn: dict,
    llm,
) -> list[str]:
    """Remove terms from persistent_unresolved that the latest user answer resolved.

    Returns the pruned list (terms still unresolved after this answer).
    """
    if not persistent or not last_turn:
        return persistent
    term_list = "\n".join(f"- {t}" for t in persistent)
    prompt = _PRUNE_RESOLVED_PROMPT.format(
        question=last_turn["q"],
        answer=last_turn["a"],
        terms=term_list,
    )
    response = safe_invoke_text(llm, prompt).strip()
    logger.info("Clarify — prune_resolved_terms: %s", response[:200])
    resolved: set[str] = set()
    for line in response.splitlines():
        if ": RESOLVED" in line.upper():
            term = line.split(":")[0].strip().lstrip("- ").lower()
            resolved.add(term)
    remaining = [t for t in persistent if t.lower() not in resolved]
    if resolved:
        logger.info("Clarify — persistent terms resolved: %s", resolved)
    return remaining


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
        unresolvable, resolved_hits, relevant_kg, extracted_norms = _find_unresolvable_entities(
            session.working_question,
            session.semantic_retriever,
            session.db_name,
            session.external_kg,
            session.external_kg_children_map,
        )
        session._cached_unresolvable = unresolvable
        session._cached_unresolvable_for = session.working_question
        # Always replace with the fresh KB result — never carry stale content forward.
        # An empty result is valid (entities not covered by KB this turn).
        session._grounded_kg = relevant_kg
        session._grounded_kg_for = session.working_question
        # Accumulate across turns: union of all KB entries seen this phase.
        if relevant_kg:
            existing = _parse_kg_entries(session.cumulative_grounded_kg)
            for name, text in _parse_kg_entries(relevant_kg).items():
                if name not in existing:
                    session.cumulative_grounded_kg = (
                        session.cumulative_grounded_kg + "\n" + text
                        if session.cumulative_grounded_kg
                        else text
                    )
        # Confident VDB resolutions (score <= 0.63) go to evidence generation.
        _update_vdb_resolved_hits(session, resolved_hits)
        # Prune persistent terms that were extracted this turn but are no longer
        # unresolvable — they were resolved via KB coverage or VDB this turn.
        current_unresolvable_names = {e for e, _ in unresolvable}
        resolved_by_kb_or_vdb = extracted_norms - current_unresolvable_names
        if resolved_by_kb_or_vdb:
            logger.info("Clarify — persistent terms resolved by KB/VDB: %s", resolved_by_kb_or_vdb)
        session.persistent_unresolved = [
            t for t in session.persistent_unresolved if t not in resolved_by_kb_or_vdb
        ]
        # Accumulate new unresolvable terms into the persistent list,
        # skipping any already resolved in a prior turn.
        existing = set(session.persistent_unresolved)
        for name, _ in unresolvable:
            if name not in existing and name not in session.resolved_persistent:
                session.persistent_unresolved.append(name)
                existing.add(name)
    else:
        logger.info("Clarify — reusing cached unresolvable entities (question unchanged)")
        resolved_hits = []
        for entity, hit_text, score in (session._cached_resolved_hits or []):
            resolved_hits.append((entity, hit_text, score))
    unresolvable = session._cached_unresolvable or []

    # Merge current unresolvable with any persistently-tracked terms that dropped
    # out of entity extraction in later turns.
    current_names = [e for e, _ in unresolvable]
    current_set = set(current_names)
    persistent_extra = [t for t in session.persistent_unresolved if t not in current_set]
    all_unresolvable = current_names + persistent_extra
    unresolvable_text = "\n".join(f"- {t}" for t in all_unresolvable) if all_unresolvable else "None"

    resolved_schema_text = _format_resolved_schema_terms(resolved_hits) or "None"

    sort_ambiguous = _ambiguous_sort_direction(session.working_question)
    sort_direction_note = (
        "IMPORTANT: The question mentions sorting/ordering but does not specify "
        "ascending or descending. You MUST ask the user for the sort direction before proceeding. "
        "If the query groups or filters by multiple fields, also consider whether a secondary sort key "
        "is needed and ask about it if it would meaningfully affect the result order."
        if sort_ambiguous else ""
    )
    if sort_ambiguous:
        logger.info("Clarify — sort direction ambiguous: %s", sort_ambiguous)

    grounded_kg_for_prompt = session._grounded_kg or "None"
    logger.info(
        "Clarify — feeding to decide-LLM | relevant_knowledge (%d chars): %r",
        len(grounded_kg_for_prompt),
        grounded_kg_for_prompt[:300],
    )

    # Turn-0 KB scan: run completeness on the initial KB before any Q&A so
    # KB-sourced ambiguities surface in the decide prompt immediately.
    if not session.clarify_history and not session.incomplete_formula_terms and session._grounded_kg:
        from .completeness import detect_incomplete_formulas
        kb_gaps = detect_incomplete_formulas(
            session.working_question,
            last_turn=None,
            relevant_kg=session._grounded_kg,
            current_gaps=[],
            llm=llm,
        )
        if kb_gaps:
            session.incomplete_formula_terms = kb_gaps
            logger.info("Completeness (turn-0 KB scan) — gaps: %s", kb_gaps)

    if session.incomplete_formula_terms:
        incomplete_formulas_note = "\n".join(
            f"- {term}: {missing}" for term, missing in session.incomplete_formula_terms
        )
    else:
        incomplete_formulas_note = "None"

    _PATIENCE = 3
    turns_used = len(session.clarify_history)
    turns_remaining = session.max_clarify_turns - turns_used
    turns_hint = (
        f"NOTE: You have {turns_remaining} clarification turns remaining. "
        "If you have not yet resolved all formulas, thresholds, or ambiguous conditions "
        "required to write correct SQL, re-examine the question and ASK rather than PROCEED."
        if turns_remaining > _PATIENCE else ""
    )

    prompt = _CLARIFY_PROMPT.format(
        db_schema=_compact_schema(session.db_schema),
        relevant_knowledge=grounded_kg_for_prompt,
        resolved_schema_terms=resolved_schema_text,
        question=session.working_question,
        history=history_text,
        unanswered_topics=unanswered_topics_text,
        unresolvable_terms=unresolvable_text,
        incomplete_formulas_note=incomplete_formulas_note,
        sort_direction_note=sort_direction_note,
        turns_hint=turns_hint,
    )
    response = safe_invoke_text(llm, prompt).strip()

    if response.upper().startswith("ASK:"):
        question = response[4:].strip()
        logger.info("Clarify — DECISION: ASK  (history len=%d)", len(session.clarify_history))
        return True, question
    logger.info("Clarify — DECISION: PROCEED  (history len=%d)", len(session.clarify_history))
    return False, None


def refresh_grounded_kg(session: "InteractiveSessionState") -> None:
    """Run entity extraction and KB coverage without making a clarification decision.

    Updates session._grounded_kg and session.cumulative_grounded_kg so Evidence
    generation has current KB context. Used in Phase 2 where clarification questions
    are not allowed but KB grounding is still needed.
    """
    if session._cached_unresolvable_for == session.working_question:
        logger.info("Clarify — KB already current for Phase 2 question (cached)")
        return
    unresolvable, resolved_hits, relevant_kg, _ = _find_unresolvable_entities(
        session.working_question,
        session.semantic_retriever,
        session.db_name,
        session.external_kg,
        session.external_kg_children_map,
    )
    session._cached_unresolvable = unresolvable
    session._cached_unresolvable_for = session.working_question
    session._grounded_kg = relevant_kg
    session._grounded_kg_for = session.working_question
    _update_vdb_resolved_hits(session, resolved_hits)
    if relevant_kg:
        existing = _parse_kg_entries(session.cumulative_grounded_kg)
        for name, text in _parse_kg_entries(relevant_kg).items():
            if name not in existing:
                session.cumulative_grounded_kg = (
                    session.cumulative_grounded_kg + "\n" + text
                    if session.cumulative_grounded_kg
                    else text
                )
