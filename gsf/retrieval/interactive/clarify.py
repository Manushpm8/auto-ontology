from __future__ import annotations

import logging
import re
import time
import unicodedata
from typing import Optional, TYPE_CHECKING

import requests
from langchain_core.messages import HumanMessage

from gsf.retrieval.entity_coverage.graph import create_graph as _create_entity_coverage_graph
from gsf.utils.llm_invoke import get_llm_client, safe_invoke_text, safe_invoke_text_nr, RETRY_MAX_ATTEMPTS

# Distance threshold for VDB resolution: entity score must be <= this value with
# no ambiguous second hit to count as "found in schema". Lower = stricter.
# Main uses DEFAULT_MAX_DISTANCE=0.75; we keep 0.65 until we have benchmarks to compare.
CLARIFY_MAX_DISTANCE: float = 0.65

# Compiled entity-coverage LangGraph — shared across calls, compiled once at import.
_ec_app = _create_entity_coverage_graph().compile()

# Regex for detecting calculation-context queries.
# Covers: calculate/calculated/calculation, compute/computed/computation, derive/derived/derivation.
_CALC_TRIGGER = re.compile(r'\b(calculat|comput|deriv)', re.IGNORECASE)

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

Topics already asked about that went UNANSWERED:
{unanswered_topics}

Terms not found in the database schema or external knowledge (ask the user to define these):
{unresolvable_terms}

Formulas or conditions whose exact specification is still missing, ranked most-critical first (ask about the first unasked item before proceeding):
{incomplete_formulas_note}

{sort_direction_note}
{turns_hint}

STRICT RULES — follow every one of these exactly:
1. NEVER ask where data is stored. Do not ask about or mention the words 'tables', 'columns', 'data', 'schema' or SQL structure. If a term from history or external knowledge maps to a schema column by name or meaning (column names may differ in casing), resolve it from the schema without asking. BAD: "Which column stores quality X?"  GOOD: or "What is the exact formula for quality X?". The user has explicit instructions to not "answer any questions about the underlying database schema (including table or column names)".
2. Only ask for information not provided by the schema, relevant external knowledge, resolved schema mappings, or history: undefined terms, acronyms, or exact formulas missing from all four. A metric being NAMED in external knowledge does NOT mean its computation formula is known — if the exact formula for computing a metric from database columns is not explicitly stated anywhere, ask for it.
3. If the question is vague about what to output (e.g., "show relevant metrics", "summarize the results"), prioritize asking the user which specific metrics or fields they want in the output.
4. Do not ask the exact same question about a topic the user could not answer (listed under "Topics already asked about that went UNANSWERED"). If turns remain AND no other unresolved terms or formulas persist, you MAY revisit an unanswered topic from a different angle — e.g. if asking for a formula went unanswered, try asking for a description of the concept instead. You MAY also ask follow-up questions on topics the user DID answer (e.g. when they say "X is calculated by combining Y and Z", you can ask for the exact formula for X).
5. If there are potentially unresolvable terms which do not have satisfactory definitions in the prior clarifications, relevant knowledge, or db_schema, ask about them one at a time. Suggested format: "As a metric, what does [TERM] measure and what is its exact formula?"
6. Pick the most semantically appropriate column yourself when the schema has similar options — do not ask the user to choose.
7. If anything else in the user's question seems unclear, you may ask about it - for example, ambiguous grouping term, thresholds, unspecified limits, or normalization methods.
8. Output a single focused question only — never two questions joined with "and" or "or".
9. Output PROCEED only when ALL of the following hold: (a) you have enough information to write correct SQL, AND (b) every term in "Terms not found in the database schema or external knowledge" is either already in the unanswered topics list or fully defined by the working question, AND (c) "Formulas or conditions whose exact specification is still missing" lists "None". If any condition fails, ASK about the most critical unresolved item before proceeding.

Output PROCEED or ASK: <question>:"""

_KG_COVERAGE_PROMPT = """\
User question (for context only — use it to understand what each term means in this query):
{question}

External knowledge:
{formatted_kg}

For each term below, identify the external knowledge entries that directly define or provide \
the formula/threshold for that term as it is used in the question above. Only include entries \
whose definition or description gives the exact meaning, calculation, or threshold — not \
entries that merely mention or relate to the concept. Use the entry's description and the \
question's domain context to verify relevance, not just name similarity.

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


_FORCED_QUESTION_PROMPT = """\
The following output specification is required for a database query but cannot be resolved \
from the schema or external knowledge:

Term: {term}
Why it's needed: {description}

Generate a single, focused question to ask the user to clarify this specification.
Ask about the business concept — what data they want to see — not about database tables or columns.
Output only the question text, nothing else.\
"""


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

_DEFAULT_SORT_HINT = (
    "DefaultSort: when results include a computed score or metric and the question "
    "does not suggest ascending order, prefer ORDER BY the primary output metric DESC. "
    "The primary metric is the one most central to the query — typically the one used "
    "in a filter condition or explicitly requested as the main output value. "
    "If no single metric is clearly primary, do not add an ORDER BY."
)


def should_inject_default_sort(question: str) -> bool:
    """Inject the default DESC sort hint unless the question already specifies a direction.

    When an explicit direction is present (ascending/descending/highest first/etc.) the hint
    would create a contradictory signal, so we suppress it. In all other cases the hint is
    safe — its text is self-limiting ("when results include a computed score or metric",
    "does not suggest ascending order", "if no single metric is clearly primary, do not add
    an ORDER BY").
    """
    return not bool(_SORT_DIRECTION.search(question))


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
) -> tuple[set[str], str, dict[str, list[str]]]:
    """Check which entities are covered by external knowledge.

    Returns (covered_set, relevant_kg_text, entry_to_original_terms).
    entry_to_original_terms maps each confirmed KB entry name to the original
    natural-language terms that matched it, so callers can annotate cumulative_grounded_kg.
    The LLM outputs YES | <entry name>
    for each covered term; we look up the verbatim entry ourselves so the content
    is never hallucinated. Uses the non-reasoning model for speed.

    When children_map is provided, child entries of any matched parent are appended
    to relevant_kg_text with their full text (name + description + definition),
    bypassing the coverage LLM. Capped at _MAX_CHILDREN_PER_PARENT per parent.
    """
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
            response = safe_invoke_text_nr(prompt).strip()
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
    entry_to_original_terms: dict[str, list[str]] = {}
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
            if match:
                # Always record which original term matched this entry, even if the
                # entry is a duplicate (seen_names dedup below).
                entry_to_original_terms.setdefault(match, []).append(term)
            if match:
                if match not in seen_names:
                    seen_names.add(match)
                    relevant_lines.append(kg_entries[match])
                    covered.add(term)  # confirmed: real KB entry exists
                # Inject children even if parent text was already added (dedup via seen_names
                # prevents duplicate text, but grandchildren would be silently skipped if we
                # only entered this block on first sight of the parent).
                children = norm_children_map.get(match, [])[:_MAX_CHILDREN_PER_PARENT]
                if children:
                    logger.debug(
                        "Clarify — KB entry %r has %d child(ren)", match, len(children)
                    )
                for child_text in children:
                    child_name = _norm_key(child_text.split("\n")[0].lstrip("- ").strip())
                    if child_name and child_name not in seen_names:
                        seen_names.add(child_name)
                        relevant_lines.append(child_text)
                        logger.debug(
                            "Clarify — injected child KB entry: %r (parent: %r)",
                            child_name, match,
                        )
                    # Inject grandchildren — covers 2-level KB hierarchies (e.g. PAR→CGPI→SPR).
                    grandchildren = norm_children_map.get(child_name, [])[:_MAX_CHILDREN_PER_PARENT] if child_name else []
                    if grandchildren:
                        logger.debug(
                            "Clarify — KB entry %r has %d grandchild(ren) via %r",
                            child_name, len(grandchildren), match,
                        )
                    for gc_text in grandchildren:
                        gc_name = _norm_key(gc_text.split("\n")[0].lstrip("- ").strip())
                        if gc_name and gc_name not in seen_names:
                            seen_names.add(gc_name)
                            relevant_lines.append(gc_text)
                            logger.debug(
                                "Clarify — injected grandchild KB entry: %r (child: %r, parent: %r)",
                                gc_name, child_name, match,
                            )
                        # Inject great-grandchildren — covers 3-level KB hierarchies.
                        great_grandchildren = norm_children_map.get(gc_name, [])[:_MAX_CHILDREN_PER_PARENT] if gc_name else []
                        if great_grandchildren:
                            logger.debug(
                                "Clarify — KB entry %r has %d great-grandchild(ren) via %r",
                                gc_name, len(great_grandchildren), match,
                            )
                        for ggc_text in great_grandchildren:
                            ggc_name = _norm_key(ggc_text.split("\n")[0].lstrip("- ").strip())
                            if ggc_name and ggc_name not in seen_names:
                                seen_names.add(ggc_name)
                                relevant_lines.append(ggc_text)
                                logger.debug(
                                    "Clarify — injected great-grandchild KB entry: %r (grandchild: %r, parent: %r)",
                                    ggc_name, gc_name, match,
                                )

    relevant_kg_text = "\n".join(relevant_lines)
    logger.info("Clarify — external_kg covers: %s", covered or "none")
    logger.info("Clarify — relevant_kg_text stored (%d chars): %r", len(relevant_kg_text), relevant_kg_text[:300] if relevant_kg_text else "")
    return covered, relevant_kg_text, entry_to_original_terms


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


# Generic standalone tokens that reliably produce false-positive VDB matches via
# substring coincidence (e.g. "id" → "idle power"). The new extraction prompt also
# instructs the LLM to omit these, but a runtime guard is kept as a safety net.
# Compound entities like "customer id" are multi-token and pass through normally.
_GENERIC_STANDALONE = frozenset({
    "id", "ids", "key", "keys", "value", "values",
    "code", "codes", "type", "types",
})


def _run_entity_coverage_pipeline(
    question: str,
    semantic_retriever: object,
    db_name: str | None,
) -> dict:
    """Invoke the entity-coverage LangGraph and return its final path_state.

    Uses the reasoning LLM (state["llm"]) for extraction per main's decision.
    Returns an empty dict on any failure so callers degrade gracefully.
    """
    try:
        llm = get_llm_client()
    except Exception as exc:
        logger.error("Clarify — could not init reasoning LLM for entity coverage: %s", exc)
        return {}

    path_state: dict = {
        "max_distance": CLARIFY_MAX_DISTANCE,
        "return_uncovered_entities": True,
    }
    if db_name:
        path_state["target_db"] = db_name

    state = {
        "llm": llm,
        "initial_question": question,
        "messages": [HumanMessage(content=question)],
        "path_state": path_state,
        "semantic_retriever": semantic_retriever,
        "decision": "",
        "domain_rules": [],
        # data_retriever and connectors are not used by the 3-node entity-coverage
        # graph (question_extraction → retrieve_candidates → coverage_grade) but are
        # present in AgentState; we omit them and bypass _build_state intentionally.
    }
    try:
        final_state = _ec_app.invoke(state, config={"recursion_limit": 10})
        return final_state.get("path_state", {})
    except Exception as exc:
        logger.error("Clarify — entity-coverage pipeline failed: %s", exc)
        return {}


def _ambiguity_check(
    path_state: dict,
) -> set[str]:
    """Return entity strings whose column-attribute hits are ambiguous.

    An entity is ambiguous when it retrieved 2+ column-attribute hits that both
    fall within CLARIFY_MAX_DISTANCE, meaning the VDB cannot single out one column.
    These are demoted to unresolvable even if CoverageGradeAgent counted them covered.
    """
    hits: list[dict] = path_state.get("retrieved_column_attributes") or []
    # Group best score and second-best score per query_entity.
    best: dict[str, float] = {}
    second: dict[str, float] = {}
    for hit in hits:
        score = hit.get("score")
        if score is None:
            continue
        score = float(score)
        if score > CLARIFY_MAX_DISTANCE:
            continue
        for entity in (hit.get("query_entities") or ([hit["query_entity"]] if hit.get("query_entity") else [])):
            if entity not in best or score < best[entity]:
                second[entity] = best.get(entity, float("inf"))
                best[entity] = score
            elif entity not in second or score < second[entity]:
                second[entity] = score
    ambiguous = {e for e, s in second.items() if s <= CLARIFY_MAX_DISTANCE}
    if ambiguous:
        logger.info("Clarify — ambiguous entities (2+ close VDB hits): %s", ambiguous)
    return ambiguous


def _find_unresolvable_entities(
    question: str,
    semantic_retriever: object,
    db_name: str | None,
    formatted_kg: str = "",
    children_map: dict[str, list[str]] | None = None,
) -> tuple[list[tuple[str, str | None]], list[tuple[str, str, float]], str, set[str], dict[str, list[str]]]:
    """Return (unresolvable_entities, resolved_hits, relevant_kg_text, all_norms, entry_to_original_terms).

    Flow:
      1. Entity-coverage pipeline (reasoning LLM): extracts entities + runs VDB for all
         of them against column attributes, SQL attributes, and custom analyses.
      2. Ambiguity check: any entity with 2+ column-attribute hits within CLARIFY_MAX_DISTANCE
         is demoted to unresolvable regardless of coverage grade.
      3. KB check on ALL extracted entities (not just VDB-uncovered): populates
         relevant_kg_text for the prompt and identifies KB-covered entities.
      4. Final unresolvable = (VDB-uncovered ∪ ambiguous) − KB-covered.

    resolved_hits contains (entity, hit_text, score) for entities cleanly resolved
    by VDB (score <= CLARIFY_MAX_DISTANCE, unambiguous); the caller uses score <= 0.63
    for evidence generation. entry_to_original_terms maps each confirmed KB entry name
    to the original natural-language terms that matched it (for cumulative_grounded_kg).
    """
    if semantic_retriever is None:
        return [], [], "", set(), {}

    # --- Step 1: run entity-coverage pipeline ---
    ec_path_state = _run_entity_coverage_pipeline(question, semantic_retriever, db_name)
    if not ec_path_state:
        return [], [], "", set(), {}

    raw_entities: list[str] = list(ec_path_state.get("entities") or [])
    if not raw_entities:
        return [], [], "", set(), {}

    # Normalize and deduplicate for consistent downstream handling.
    # "median signal quality" and "signal quality" both → "signal quality" (one entry).
    norm_to_original: dict[str, str] = {}
    for entity in raw_entities:
        norm = _normalize_entity(entity)
        if norm and norm not in norm_to_original:
            norm_to_original[norm] = entity
    # Drop entities whose normalized form is a strict substring of another in the batch.
    # e.g. "condition" ⊂ "atmospheric conditions" → drop; "signal dynamics" ⊄ "signal quality" → keep both
    all_norms = set(norm_to_original.keys())
    search_norms = {e for e in all_norms if not any(e != o and e in o for o in all_norms)}
    logger.info("Clarify — extracted entities (normalized): %s", sorted(search_norms))

    # Strip generic standalone tokens — the prompt already excludes them but LLMs
    # occasionally emit them; a second VDB hit on "id" or "type" would be misleading.
    generic_skipped = search_norms & _GENERIC_STANDALONE
    if generic_skipped:
        logger.info("Clarify — dropping generic standalone terms: %s", generic_skipped)
    search_norms -= generic_skipped

    # --- Step 2: ambiguity check on column-attribute hits ---
    ambiguous_entities = _ambiguity_check(ec_path_state)

    # Build VDB-uncovered set: entities the pipeline marked uncovered + ambiguous ones.
    vdb_uncovered: set[str] = set(ec_path_state.get("uncovered_entities") or [])
    # Normalize uncovered_entities to match our norm keys (pipeline emits raw strings).
    vdb_uncovered_norms: set[str] = set()
    for raw in vdb_uncovered:
        norm = _normalize_entity(raw)
        if norm:
            vdb_uncovered_norms.add(norm)
        else:
            vdb_uncovered_norms.add(raw.lower().strip())
    # Merge in ambiguous entities (they came back "covered" by score but are not reliable).
    needs_kb_rescue = vdb_uncovered_norms | (ambiguous_entities & search_norms)

    # Build resolved_hits from entities cleanly covered at VDB (unambiguous, within threshold).
    # Use the pipeline's enriched candidates (Neo4j-resolved attribute+term names) rather than
    # raw VDB text blobs. Fall back to raw text when no enriched candidate is available.
    resolved_hits: list[tuple[str, str, float]] = []
    col_hits: list[dict] = ec_path_state.get("retrieved_column_attributes") or []
    candidates_by_id: dict[str, dict] = {
        c["id"]: c
        for c in (ec_path_state.get("final_response") or {}).get("candidates", [])
        if c.get("id")
    }
    best_hit_per_entity: dict[str, dict] = {}
    for hit in col_hits:
        score = hit.get("score")
        if score is None or float(score) > CLARIFY_MAX_DISTANCE:
            continue
        for entity in (hit.get("query_entities") or ([hit["query_entity"]] if hit.get("query_entity") else [])):
            if entity not in best_hit_per_entity or float(score) < float(best_hit_per_entity[entity].get("score", float("inf"))):
                best_hit_per_entity[entity] = hit
    for entity, hit in best_hit_per_entity.items():
        norm = _normalize_entity(entity) or entity.lower().strip()
        if norm not in needs_kb_rescue and norm in search_norms:
            candidate = candidates_by_id.get(str(hit.get("id") or ""))
            if candidate and candidate.get("attribute"):
                term = candidate.get("term") or ""
                hit_text = f'{candidate["attribute"]} ({term})' if term else candidate["attribute"]
            else:
                hit_text = hit.get("text") or ""
            resolved_hits.append((norm, hit_text, float(hit.get("score", 1.0))))

    # --- Step 3: KB check on ALL extracted entities ---
    # Run on all search_norms (not just uncovered) so relevant_kg_text is complete
    # and entities explained by KB don't end up in the unresolvable list.
    relevant_kg_text = ""
    entry_to_original_terms: dict[str, list[str]] = {}
    kb_covered_norms: set[str] = set()
    if formatted_kg and search_norms:
        kb_entities = [norm_to_original.get(norm, norm) for norm in search_norms]
        orig_lower_to_norm = {norm_to_original.get(n, n).lower(): n for n in search_norms}
        covered_originals, relevant_kg_text, entry_to_original_terms = _filter_covered_by_external_knowledge(
            kb_entities, formatted_kg, question, children_map
        )
        kb_covered_norms = {orig_lower_to_norm.get(orig, orig) for orig in covered_originals}
        logger.info("Clarify — KB covers: %s", kb_covered_norms or "none")

    # --- Step 4: final unresolvable = (VDB-uncovered ∪ ambiguous) − KB-covered ---
    final_unresolvable_norms = needs_kb_rescue - kb_covered_norms
    # Also mark generics as unresolvable (they were never sent to VDB).
    final_unresolvable_norms |= {_normalize_entity(e) or e for e in generic_skipped}

    # VDB-only: resolved by VDB but not covered by external KB.
    # Returned so the caller can check for missing calculation formulas.
    vdb_only_norms: set[str] = {norm for norm, _, _ in resolved_hits} - kb_covered_norms

    unresolvable: list[tuple[str, str | None]] = [
        (norm, None) for norm in final_unresolvable_norms
    ]

    logger.info("Clarify — unresolvable after VDB+KB: %s", [e for e, _ in unresolvable] or "none")
    logger.info("Clarify — resolved by VDB: %s", [(e, f"{s:.3f}") for e, _, s in resolved_hits] or "none")
    return unresolvable, resolved_hits, relevant_kg_text, all_norms, entry_to_original_terms, vdb_only_norms


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
        unresolvable, resolved_hits, relevant_kg, extracted_norms, entry_to_original_terms, vdb_only_norms = _find_unresolvable_entities(
            session.working_question,
            session.semantic_retriever,
            session.db_name,
            session.external_kg,
            session.external_kg_children_map,
        )
        session._cached_unresolvable = unresolvable
        session._cached_unresolvable_for = session.working_question
        session._cached_vdb_only_norms = vdb_only_norms
        # Always replace with the fresh KB result — never carry stale content forward.
        # An empty result is valid (entities not covered by KB this turn).
        session._grounded_kg = relevant_kg
        session._grounded_kg_for = session.working_question
        # Accumulate across turns: union of all KB entries seen this phase.
        # Annotate each new entry with the original natural-language terms that matched
        # it so the evidence builder can bridge phrasing gaps (e.g. "significant compliance
        # issues" → "High Audit Compliance Pressure") even when later turns stop linking them.
        if relevant_kg:
            existing = _parse_kg_entries(session.cumulative_grounded_kg)
            for name, text in _parse_kg_entries(relevant_kg).items():
                if name not in existing:
                    matched_from = entry_to_original_terms.get(name, [])
                    if matched_from:
                        first_nl = text.index("\n") if "\n" in text else len(text)
                        text = text[:first_nl] + f"\n# matched from: {', '.join(matched_from)}" + text[first_nl:]
                    session.cumulative_grounded_kg = (
                        session.cumulative_grounded_kg + "\n" + text
                        if session.cumulative_grounded_kg
                        else text
                    )
        # For terms covered by BOTH KB and VDB (score < 0.62), inject a
        # disambiguation note so the evidence LLM can choose between the KB
        # formula and the direct schema column rather than blindly applying both.
        _KB_VDB_DISAMBIG_THRESHOLD = 0.62
        kb_covered_hits = [
            (norm, col_text, score) for norm, col_text, score in resolved_hits
            if norm not in vdb_only_norms and score < _KB_VDB_DISAMBIG_THRESHOLD
        ]
        if kb_covered_hits:
            # Build term → (entry_name, kb_text) from entry_to_original_terms + parsed KB
            kb_entries_parsed = _parse_kg_entries(relevant_kg)
            term_to_kb_entry: dict[str, tuple[str, str]] = {}
            for entry_name, matched_terms in entry_to_original_terms.items():
                entry_text = next(
                    (v for k, v in kb_entries_parsed.items()
                     if k.startswith(entry_name) or entry_name.startswith(k)), ""
                )
                for t in matched_terms:
                    term_to_kb_entry.setdefault(t, (entry_name, entry_text))
            for norm, col_text, score in kb_covered_hits:
                entry_name, kb_text = term_to_kb_entry.get(norm, ("", ""))
                if not kb_text:
                    continue
                # Dedup by KB entry name — stable across turns unlike norm phrasing
                marker = f"[DISAMBIGUATION for KB:'{entry_name}'"
                if marker in session.cumulative_grounded_kg:
                    continue
                note = (
                    f"\n{marker}: "
                    f"KB defines it as: {kb_text[:200].strip()} "
                    f"— but schema also has a direct column: {col_text[:120].strip()}. "
                    f"In evidence, choose whichever fits the question domain — not both.]"
                )
                session.cumulative_grounded_kg += note
                logger.info(
                    "Clarify — KB+VDB disambiguation note added for KB entry %r (VDB score=%.3f)",
                    entry_name, score,
                )

        # Capture all extracted entities on the very first clarify call (turn 0).
        if not session.initial_extracted_entities:
            session.initial_extracted_entities = sorted(extracted_norms)
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

    sort_direction_note = ""  # Sort direction is handled by the default DESC hint at SQL gen time.

    grounded_kg_for_prompt = session._grounded_kg or "None"
    logger.info(
        "Clarify — feeding to decide-LLM | relevant_knowledge (%d chars): %r",
        len(grounded_kg_for_prompt),
        grounded_kg_for_prompt[:300],
    )

    # Turn-0 scan: run completeness before any Q&A to surface missing formulas.
    # Fires when KB has content OR when the question implies a calculation and there
    # are VDB-only entities (schema hits with no KB formula).
    has_calc_vdb = (
        _CALC_TRIGGER.search(session.working_question)
        and bool(session._cached_vdb_only_norms)
    )
    if not session.clarify_history and not session.incomplete_formula_terms and (
        session._grounded_kg or has_calc_vdb
    ):
        from .completeness import detect_incomplete_formulas
        if has_calc_vdb:
            hits_map = {e: t for e, t, _ in resolved_hits}
            vdb_only = []
            for e in sorted(session._cached_vdb_only_norms):
                if e in hits_map:
                    desc = re.sub(r"^ColumnAttribute:[^.]+\.\s*", "", hits_map[e]).rstrip()
                    vdb_only.append(f"{e}: {desc}" if desc else e)
                else:
                    vdb_only.append(e)
        else:
            vdb_only = []
        gaps = detect_incomplete_formulas(
            session.working_question,
            last_turn=None,
            relevant_kg=session._grounded_kg,
            current_gaps=[],
            llm=llm,
            vdb_only_entities=vdb_only,
        )
        if gaps:
            session.incomplete_formula_terms = gaps
            logger.info("Completeness (turn-0 scan) — gaps: %s", gaps)

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

    # Guard: override PROCEED if any incomplete term has never been asked about.
    # The decide-LLM may rationalize PROCEED when it can see VDB hints, but a term
    # that hasn't appeared in any past question is genuinely unasked and needs a turn.
    if not response.upper().startswith("ASK:") and session.incomplete_formula_terms and turns_remaining > 0:
        asked_tokens = {
            re.sub(r"[^\w]", "", tok).lower()
            for h in session.clarify_history
            for tok in re.split(r"[\s/\W]+", h["q"])
            if tok
        } - {""}
        for term, description in session.incomplete_formula_terms:
            term_tokens = {
                re.sub(r"[^\w]", "", tok)
                for tok in re.split(r"[\s/]+", term.lower())
                if tok and tok not in _FILLER and tok not in _CONNECTIVES
            } - {""}
            if term_tokens and not (term_tokens & asked_tokens):
                question = _generate_forced_question(term, description, llm)
                logger.info("Clarify — DECISION override: ASK (never-asked incomplete term %r)", term)
                return True, question

    if response.upper().startswith("ASK:"):
        question = response[4:].strip()
        logger.info("Clarify — DECISION: ASK  (history len=%d)", len(session.clarify_history))
        return True, question
    logger.info("Clarify — DECISION: PROCEED  (history len=%d)", len(session.clarify_history))
    return False, None


def _generate_forced_question(term: str, description: str, llm) -> str:
    """Generate a targeted clarification question for a term that is both incomplete and unresolvable."""
    prompt = _FORCED_QUESTION_PROMPT.format(term=term, description=description)
    return safe_invoke_text(llm, prompt).strip()


def refresh_grounded_kg(session: "InteractiveSessionState") -> None:
    """Run entity extraction and KB coverage without making a clarification decision.

    Updates session._grounded_kg and session.cumulative_grounded_kg so Evidence
    generation has current KB context. Used in Phase 2 where clarification questions
    are not allowed but KB grounding is still needed.
    """
    if session._cached_unresolvable_for == session.working_question:
        logger.info("Clarify — KB already current for Phase 2 question (cached)")
        return
    unresolvable, resolved_hits, relevant_kg, _, entry_to_original_terms, _vdb_only = _find_unresolvable_entities(
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
                matched_from = entry_to_original_terms.get(name, [])
                if matched_from:
                    first_nl = text.index("\n") if "\n" in text else len(text)
                    text = text[:first_nl] + f"\n# matched from: {', '.join(matched_from)}" + text[first_nl:]
                session.cumulative_grounded_kg = (
                    session.cumulative_grounded_kg + "\n" + text
                    if session.cumulative_grounded_kg
                    else text
                )
