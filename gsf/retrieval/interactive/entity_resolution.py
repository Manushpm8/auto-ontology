"""Entity extraction and VDB (vector-database) resolution.

Split out of clarify.py: this module owns "what entities does the question
mention, and which schema column/attribute does each one resolve to" —
running the entity-coverage LangGraph, checking VDB-hit ambiguity, and
resolving collisions where two entities land on the same column. External
knowledge (KB) coverage lives in kg_coverage.py; clarification-turn
orchestration lives in clarify.py.
"""

from __future__ import annotations

import logging
import re

from langchain_core.messages import HumanMessage

from gsf.retrieval.entity_coverage.graph import create_graph as _create_entity_coverage_graph
from gsf.retrieval.data_access.semantic_search import search_semantic_index
from gsf.semantic.constants import LABEL_COLUMN_ATTRIBUTE
from gsf.utils.llm_invoke import get_llm_client, safe_invoke_text_nr

from .kg_coverage import _filter_covered_by_external_knowledge

logger = logging.getLogger(__name__)

# Distance threshold for VDB resolution: entity score must be <= this value with
# no ambiguous second hit to count as "found in schema". Lower = stricter.
# Main uses DEFAULT_MAX_DISTANCE=0.75; we keep 0.65 until we have benchmarks to compare.
CLARIFY_MAX_DISTANCE: float = 0.65

# Compiled entity-coverage LangGraph — shared across calls, compiled once at import.
_ec_app = _create_entity_coverage_graph().compile()

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


# ── Collision resolution ────────────────────────────────────────────────────
# When two different entities' best VDB hit lands on the same underlying
# column, the pipeline used to flag it "unreliable" and drop the formula that
# depended on it entirely at evidence-gen. Calibrated against real collisions
# (see conversation/notes): thresholds are deliberately conservative — when in
# doubt, defer to the LLM call rather than silently auto-assigning.
_COLLISION_WINNER_MARGIN = 0.04  # winner's 1st-hit score must beat the loser's by at least this
_COLLISION_LOSER_MAX_GAP = 0.08  # loser's own gap (shared hit -> its next-distinct hit) must be under this

_COMPOSITE_HIT_RE = re.compile(r"\b(json|jsonb|structured)\b", re.IGNORECASE)
# "jsonb" needs its own alternative, not just "json": `\bjson\b` requires a word
# boundary right after "json", which "JSONB" never has (the "b" is a word char
# glued onto it), so a description reading "JSONB column..." — the exact phrasing
# every *_column_meaning_base.json in this dataset uses for Postgres jsonb columns —
# silently fell through to the sample-values fallback and was missed.
# "Sample values: a, b, c" with 2+ comma-separated entries — a column description
# listing multiple distinct sample values is a reliable sign of a multi-key/composite
# column regardless of how the description happens to phrase the type (some say
# "JSON object", others just "Structured ... data" — neither keyword is guaranteed).
_SAMPLE_VALUES_RE = re.compile(r"Sample values:\s*([^.]+)", re.IGNORECASE)


def _is_composite_hit(hit: dict) -> bool:
    """Best-effort check that *hit*'s underlying column is a composite/multi-key
    column (JSON or otherwise structured), i.e. two colliding terms could both
    legitimately refer to it — as different sub-keys — rather than one of them
    being a wrong match.

    Prefers structured type metadata (``data_type``) when present; falls back
    to text checks on the hit's description otherwise.

    ``data_type`` is now threaded through from Neo4j's ``attr.datatype``
    (dal/terms.py -> semantic/embed.py -> data_access/semantic_search.py), but
    only for rows embedded *after* that change landed — existing VDB rows
    won't carry it until the semantic index is re-embedded. Until then this
    still falls through to the text-heuristic path below for most hits. Once
    a broad re-embed has happened, the ``data_type`` branch above should be
    handling the large majority of cases and the text-heuristic fallback can
    likely be trimmed down (or dropped) — revisit then.
    """
    data_type = hit.get("data_type") or hit.get("type")
    if data_type:
        return "json" in str(data_type).lower()
    text = str(hit.get("text") or "")
    if _COMPOSITE_HIT_RE.search(text):
        return True
    sample_match = _SAMPLE_VALUES_RE.search(text)
    if sample_match:
        values = [v.strip() for v in sample_match.group(1).split(",") if v.strip()]
        return len(values) >= 2
    return False


def _shared_column_note(entities: list[str], hit: dict, fallback_id: str) -> str:
    """Format the "these terms legitimately share one composite column" note
    injected directly into SQL-gen evidence."""
    names = ", ".join(f'"{e}"' for e in entities)
    col_name = re.sub(r"^ColumnAttribute:\s*", "", str(hit.get("text") or "")).split(".")[0].strip()
    logger.info("Clarify — collision resolved as shared composite column: %s -> %s", entities, col_name)
    return (
        f"Note: {names} both resolve to the same column ({col_name or fallback_id}) "
        f"— use the correct sub-key/field for each; they are not the same value."
    )


def _entity_ranked_hits(
    entity: str, semantic_retriever: object, db_name: str | None, k: int = 5
) -> list[dict]:
    """Fresh, per-entity ranked VDB candidates for *entity* (best first).

    Deliberately re-queries the VDB directly rather than reusing the pipeline's
    already-retrieved col_hits: those are deduped globally by column id across
    all entities in the question (keeping only the single lowest score per
    id), so two colliding entities show up with an *identical* shared score —
    the exact per-entity margin this resolution needs is discarded by that
    dedup. Re-querying is only done for entities that actually collide, so the
    extra VDB calls are rare.
    """
    try:
        hits = search_semantic_index(
            semantic_retriever, entity, label_filter=[LABEL_COLUMN_ATTRIBUTE],
            per_label_k=k, database_name=db_name,
        )
    except Exception:
        logger.warning("Clarify — collision re-query failed for %r", entity, exc_info=True)
        return []
    return sorted(hits, key=lambda h: float(h.get("score") or float("inf")))


_COLLISION_LLM_PROMPT = """\
Two or more terms extracted from a question both resolved, via vector search, to the \
SAME database column — that's almost certainly wrong for at least one of them. Decide \
which column each term actually refers to.

Question: {question}

Relevant knowledge: {relevant_kg}

Terms and their top candidate columns (best match first):
{candidates_block}

For each term, output exactly one line:
<term>: <chosen candidate id>

Pick the candidate id that best matches what the term refers to — it does not have to be \
the first-listed one. If a term genuinely doesn't match any candidate shown, output:
<term>: NONE
"""


def _llm_disambiguate_collision(
    question: str,
    relevant_kg: str,
    entity_candidates: dict[str, list[dict]],
) -> dict[str, str] | None:
    """Ask a fast LLM to assign each colliding entity to a distinct candidate id.

    Returns {entity: chosen_id}. Returns None on any parse failure or if any
    entity is missing from the response — callers must treat None as "could
    not resolve" and fail safe (never guess a mapping ourselves here).
    """
    blocks = []
    for entity, hits in entity_candidates.items():
        lines = "\n".join(f"  id={h.get('id')}: {str(h.get('text') or '')[:150]}" for h in hits)
        blocks.append(f'"{entity}":\n{lines}')
    prompt = _COLLISION_LLM_PROMPT.format(
        question=question, relevant_kg=relevant_kg or "(none)", candidates_block="\n\n".join(blocks)
    )
    try:
        response = safe_invoke_text_nr(prompt).strip()
    except Exception:
        logger.warning("Clarify — collision LLM call failed", exc_info=True)
        return None

    valid_ids = {str(h.get("id")) for hits in entity_candidates.values() for h in hits}
    result: dict[str, str] = {}
    for line in response.splitlines():
        if ":" not in line:
            continue
        term, _, chosen = line.partition(":")
        term = term.strip().strip('"')
        chosen = chosen.strip()
        if term in entity_candidates and (chosen in valid_ids or chosen.upper() == "NONE"):
            result[term] = chosen
    if len(result) != len(entity_candidates):
        logger.warning("Clarify — collision LLM response incomplete/unparseable: %r", response[:300])
        return None
    return result


def _resolve_collisions(
    question: str,
    relevant_kg_text: str,
    best_hit_per_entity: dict[str, dict],
    semantic_retriever: object,
    db_name: str | None,
    kb_covered_norms: set[str] | None = None,
) -> list[str]:
    """Resolve entities whose best VDB hit collides with another entity's, in place.

    Mutates *best_hit_per_entity* so every entity ends up mapped to a distinct
    column, or is explicitly confirmed as legitimately sharing one (the JSON
    case). Returns "shared JSON column" notes to inject directly into SQL-gen
    evidence (bypassing evidence-gen's LLM, which isn't reliable about
    preserving instructions passed through it).

    Resolution order, most confident/cheapest first:
      0. Either colliding entity is already KB-covered (has a formula from
         external knowledge, e.g. "Aggressive Trading Intensity") -> it doesn't
         need a raw column identity at all; drop it from best_hit_per_entity
         rather than let it win or get auto-assigned one. It only showed up
         as a VDB "entity" because the working question repeats its name from
         a clarification answer, not because it's a schema concept — resolving
         it to a column risks handing evidence-gen a confident-looking but
         nonsensical mapping (e.g. a computed metric name assigned to an
         unrelated real column) with no disambiguation signal attached.
      1. Otherwise, shared hit is a JSON column -> assume both terms correctly
         share it (they likely need different sub-keys within it); note it,
         don't reassign.
      2. Otherwise, if one term's 1st-hit score beats the other's by >= 0.04
         AND the loser's own gap to its next-distinct candidate is < 0.08:
         auto-assign the loser to that next-distinct candidate.
      3. Otherwise: ask a fast LLM to pick, given both terms' top candidates.
      4. If the LLM call fails to parse: drop the lower-confidence entity from
         best_hit_per_entity entirely (treat as VDB-unresolved) rather than guess.
    """
    json_notes: list[str] = []
    kb_covered_norms = kb_covered_norms or set()

    id_to_entities: dict[str, list[str]] = {}
    for entity, hit in best_hit_per_entity.items():
        id_to_entities.setdefault(str(hit.get("id") or ""), []).append(entity)
    collisions = {hid: ents for hid, ents in id_to_entities.items() if len(ents) > 1}
    if not collisions:
        return json_notes

    for shared_id, entities in collisions.items():
        # Step 0: KB-covered entities don't compete for a column at all.
        kb_covered_here = [
            e for e in entities if (_normalize_entity(e) or e.lower().strip()) in kb_covered_norms
        ]
        for e in kb_covered_here:
            logger.info(
                "Clarify — collision: %r is already KB-covered (has a formula), "
                "dropping its VDB column claim to %r instead of resolving it", e, shared_id,
            )
            best_hit_per_entity.pop(e, None)
        entities = [e for e in entities if e not in kb_covered_here]
        if len(entities) < 2:
            continue  # no real collision left once KB-covered terms are removed
        shared_hit = best_hit_per_entity[entities[0]]
        if _is_composite_hit(shared_hit):
            json_notes.append(_shared_column_note(entities, shared_hit, shared_id))
            continue

        # Fresh per-entity queries — see _entity_ranked_hits for why col_hits can't be reused.
        fresh_hits = {e: _entity_ranked_hits(e, semantic_retriever, db_name) for e in entities}

        def _score_for_shared(e: str) -> float:
            hit = next((h for h in fresh_hits[e] if str(h.get("id") or "") == shared_id), None)
            return float(hit["score"]) if hit else float(best_hit_per_entity[e].get("score") or float("inf"))

        ranked = sorted(entities, key=_score_for_shared)
        winner = ranked[0]
        winner_score = _score_for_shared(winner)

        needs_llm: list[str] = []
        for loser in ranked[1:]:
            loser_score = _score_for_shared(loser)
            next_distinct = next(
                (h for h in fresh_hits[loser] if str(h.get("id") or "") != shared_id), None
            )
            margin_ok = (loser_score - winner_score) >= _COLLISION_WINNER_MARGIN
            gap_ok = (
                next_distinct is not None
                and (float(next_distinct["score"]) - loser_score) < _COLLISION_LOSER_MAX_GAP
            )
            if margin_ok and gap_ok:
                logger.info(
                    "Clarify — collision auto-resolved: %r kept %r (%.3f); "
                    "%r reassigned to %r (%.3f)",
                    winner, shared_id, winner_score,
                    loser, next_distinct.get("id"), float(next_distinct["score"]),
                )
                best_hit_per_entity[loser] = next_distinct
            else:
                needs_llm.append(loser)

        if not needs_llm:
            continue

        entity_candidates = {e: fresh_hits[e][:3] for e in [winner, *needs_llm]}
        decision = _llm_disambiguate_collision(question, relevant_kg_text, entity_candidates)
        if decision is None:
            # Fail safe: don't guess. Drop the lower-confidence entities so they
            # fall through the normal "VDB-unresolved" path instead of silently
            # keeping a possibly-wrong shared mapping.
            for loser in needs_llm:
                logger.warning("Clarify — collision unresolved for %r; dropping VDB hit", loser)
                best_hit_per_entity.pop(loser, None)
            continue

        by_id = {str(h.get("id")): h for hits in entity_candidates.values() for h in hits}
        for entity, chosen_id in decision.items():
            if chosen_id.upper() == "NONE" or chosen_id not in by_id:
                best_hit_per_entity.pop(entity, None)
            else:
                best_hit_per_entity[entity] = by_id[chosen_id]
        logger.info("Clarify — collision LLM-resolved: %s", decision)

        # Retroactive check: the LLM may itself have assigned the same id to
        # multiple entities (deciding they genuinely share a column) rather than
        # picking distinct ones. That's a new, unvalidated collision — apply the
        # same composite-column safety net as the original shared_id, instead of
        # silently accepting it as if it were a plain single-value mapping.
        new_id_to_entities: dict[str, list[str]] = {}
        for entity, chosen_id in decision.items():
            if chosen_id.upper() != "NONE" and chosen_id in by_id:
                new_id_to_entities.setdefault(chosen_id, []).append(entity)
        for new_id, ents in new_id_to_entities.items():
            # Same Step-0 exclusion as the pre-LLM collision above: a KB-covered
            # entity doesn't need a column identity at all, so it shouldn't be
            # able to trigger (or be caught up in) this composite-column check
            # just because the LLM also happened to assign it the same id.
            kb_covered_here = [
                e for e in ents if (_normalize_entity(e) or e.lower().strip()) in kb_covered_norms
            ]
            for e in kb_covered_here:
                logger.info(
                    "Clarify — collision (post-LLM): %r is already KB-covered, "
                    "dropping its VDB column claim to %r instead of resolving it", e, new_id,
                )
                best_hit_per_entity.pop(e, None)
            ents = [e for e in ents if e not in kb_covered_here]
            if len(ents) < 2:
                continue
            if _is_composite_hit(by_id[new_id]):
                json_notes.append(_shared_column_note(ents, by_id[new_id], new_id))
            else:
                logger.warning(
                    "Clarify — LLM converged %s onto a single non-composite column %r; "
                    "treating as unresolved rather than trusting a suspicious shared assignment",
                    ents, new_id,
                )
                for e in ents:
                    best_hit_per_entity.pop(e, None)

    return json_notes


def _find_unresolvable_entities(
    question: str,
    semantic_retriever: object,
    db_name: str | None,
    formatted_kg: str = "",
    children_map: dict[str, list[str]] | None = None,
) -> tuple[
    list[tuple[str, str | None]],
    list[tuple[str, str, float, str]],
    str,
    set[str],
    dict[str, list[str]],
    set[str],
    list[str],
]:
    """Return (unresolvable_entities, resolved_hits, relevant_kg_text, all_norms,
    entry_to_original_terms, vdb_only_norms, json_shared_notes).

    Flow:
      1. Entity-coverage pipeline (reasoning LLM): extracts entities + runs VDB for all
         of them against column attributes, SQL attributes, and custom analyses.
      2. Ambiguity check: any entity with 2+ column-attribute hits within CLARIFY_MAX_DISTANCE
         is demoted to unresolvable regardless of coverage grade.
      3. KB check on ALL extracted entities (not just VDB-uncovered): populates
         relevant_kg_text for the prompt and identifies KB-covered entities.
      3b. Collision resolution: entities whose best hit collided with another
          entity's are auto-resolved (JSON-shared / margin-based) or sent to a
          small LLM disambiguation call — see _resolve_collisions.
      4. Final unresolvable = (VDB-uncovered ∪ ambiguous) − KB-covered.

    resolved_hits contains (entity, hit_text, score, hit_id) for entities cleanly resolved
    by VDB (score <= CLARIFY_MAX_DISTANCE, unambiguous, post-collision-resolution); the
    caller uses score <= 0.63 for evidence generation. hit_id is the underlying attribute
    node's ID — a stable identity check for "do two terms resolve to the same column",
    since hit_text is only a display label and isn't guaranteed unique or consistently
    formatted across hits.
    entry_to_original_terms maps each confirmed KB entry name
    to the original natural-language terms that matched it (for cumulative_grounded_kg).
    json_shared_notes are "these terms share a JSON column, use distinct sub-keys" notes
    to inject verbatim into SQL-gen evidence — see _resolve_collisions.
    """
    if semantic_retriever is None:
        return [], [], "", set(), {}, set(), []

    # --- Step 1: run entity-coverage pipeline ---
    ec_path_state = _run_entity_coverage_pipeline(question, semantic_retriever, db_name)
    if not ec_path_state:
        return [], [], "", set(), {}, set(), []

    raw_entities: list[str] = list(ec_path_state.get("entities") or [])
    if not raw_entities:
        return [], [], "", set(), {}, set(), []

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

    # Find each entity's single best VDB hit (score <= CLARIFY_MAX_DISTANCE).
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

    # --- Step 3: KB check on ALL extracted entities ---
    # Run on all search_norms (not just uncovered) so relevant_kg_text is complete
    # and entities explained by KB don't end up in the unresolvable list.
    # Done before collision resolution below so relevant_kg_text is available to
    # the LLM disambiguation fallback.
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

    # Resolve any entities that collided on the same best hit, in place.
    json_notes = _resolve_collisions(
        question, relevant_kg_text, best_hit_per_entity, semantic_retriever, db_name,
        kb_covered_norms,
    )

    # Build resolved_hits from entities cleanly covered at VDB (unambiguous, within
    # threshold, and post-collision-resolution). Use the pipeline's enriched candidates
    # (Neo4j-resolved attribute+term names) rather than raw VDB text blobs. Fall back to
    # raw text when no enriched candidate is available.
    resolved_hits: list[tuple[str, str, float, str]] = []
    for entity, hit in best_hit_per_entity.items():
        norm = _normalize_entity(entity) or entity.lower().strip()
        if norm not in needs_kb_rescue and norm in search_norms:
            candidate = candidates_by_id.get(str(hit.get("id") or ""))
            if candidate and candidate.get("attribute"):
                term = candidate.get("term") or ""
                hit_text = f'{candidate["attribute"]} ({term})' if term else candidate["attribute"]
            else:
                hit_text = hit.get("text") or ""
            resolved_hits.append(
                (norm, hit_text, float(hit.get("score", 1.0)), str(hit.get("id") or ""))
            )

    # --- Step 4: final unresolvable = (VDB-uncovered ∪ ambiguous) − KB-covered ---
    final_unresolvable_norms = needs_kb_rescue - kb_covered_norms
    # Also mark generics as unresolvable (they were never sent to VDB).
    final_unresolvable_norms |= {_normalize_entity(e) or e for e in generic_skipped}

    # VDB-only: resolved by VDB but not covered by external KB.
    # Returned so the caller can check for missing calculation formulas.
    vdb_only_norms: set[str] = {norm for norm, *_ in resolved_hits} - kb_covered_norms

    unresolvable: list[tuple[str, str | None]] = [
        (norm, None) for norm in final_unresolvable_norms
    ]

    logger.info("Clarify — unresolvable after VDB+KB: %s", [e for e, _ in unresolvable] or "none")
    logger.info(
        "Clarify — resolved by VDB: %s",
        [(e, f"{s:.3f}") for e, _, s, *_ in resolved_hits] or "none",
    )
    return (
        unresolvable, resolved_hits, relevant_kg_text, all_norms,
        entry_to_original_terms, vdb_only_norms, json_notes,
    )
