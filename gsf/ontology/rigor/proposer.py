"""Gen-LLM Proposer for the Rigor pipeline.

For each table, the Proposer receives:
  - The table's columns, FKs, and SQL queries (from Neo4j)
  - Deterministic edges already created
  - Denormalized candidates to evaluate
  - External ontology matches (LOV, BioPortal, FIBO)
  - BIRD evidence strings
  - The current CoreOntology snapshot

It returns a DeltaOntology with proposed BusinessTerms and
ObjectProperties for this table. Attributes are created
deterministically (see deterministic.py) and are NOT proposed
by the LLM.
"""

from __future__ import annotations

import logging
from typing import Any

from langchain_core.messages import HumanMessage, SystemMessage

from gsf.ontology.domain_prereading.llm import invoke_structured
from gsf.ontology.rigor.deterministic import DeterministicResult
from gsf.ontology.rigor.external_vocab import ExternalMatch
from gsf.ontology.rigor.models import (
    CoreOntology,
    DeltaOntology,
    EnrichedColumn,
)

logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# System prompt
# ---------------------------------------------------------------------------

_SYSTEM_PROMPT = """\
You are an expert ontology engineer building a business ontology from a \
relational database schema. You receive one table at a time and must \
propose ontology elements for it.

Attributes are created automatically for every non-PK, non-FK column — \
you do NOT need to propose them. Leave the `attributes` list empty.

Your output is a DeltaOntology with:

## 1. BusinessTerms (business entities)

- ALWAYS propose ONE primary BusinessTerm for this table (the business \
entity it represents). Use CamelCase naming (e.g. "Customer", "Transaction").
- If a column hides a denormalized entity (flagged as a "denormalized \
candidate"), you may propose an ADDITIONAL inferred BusinessTerm for it.
- If this table's business term is a specialization of an existing term, \
set `parent` to the parent term name (SubClassOf relationship).
- Review the "Existing Business Terms" section. If the table clearly belongs \
to an already-named term, reuse that exact name — do NOT create \
a duplicate.

## 2. ObjectProperties (edges between business terms)

- Deterministic edges (FK, implicit FK, self-ref) are already handled. \
Do NOT re-propose them.
- For denormalized candidates: decide if the column represents a hidden \
entity. If yes, propose the inferred BusinessTerm AND an ObjectProperty \
(e.g. "belongsTo") linking the table's term to it.
- You may propose additional semantic relationships you detect from \
context (e.g. hierarchy, composition, temporal ordering).

## Rules

- ALWAYS propose at least ONE BusinessTerm for this table.
- Be conservative with ObjectProperties: only propose edges with clear \
evidence.
- Names must be clear and business-friendly.
- Every BusinessTerm must have a meaningful one-sentence description.
- Use external ontology matches as naming hints — prefer established \
vocabulary terms when they fit.
- Use evidence strings to understand domain-specific value encodings \
and business rules."""


# ---------------------------------------------------------------------------
# Prompt builder
# ---------------------------------------------------------------------------


def _build_user_prompt(
    table: dict[str, Any],
    ctx: dict[str, Any],
    det_result: DeterministicResult,
    ext_matches: list[ExternalMatch],
    ontology: CoreOntology,
    enriched_columns: list[EnrichedColumn] | None = None,
) -> str:
    """Assemble the user prompt with all retrieval context."""
    blocks: list[str] = []

    # Block 1 — Existing ontology state
    blocks.append(ontology.snapshot_for_prompt())

    # Block 2 — Table metadata
    table_header = (
        f"## Table: {table.get('schema_name', '')}.{table['name']}\n"
        f"Description: {table.get('description') or '(none)'}\n"
        f"Primary key: {table.get('pk') or '(none)'}\n"
        f"Total queries referencing: {table.get('query_count', 0)}"
    )
    blocks.append(table_header)

    # Block 3 — Enriched attributes (already created)
    if enriched_columns:
        attr_lines = []
        for ec in enriched_columns:
            line = f"  - {ec.canonical_name} ({ec.source_column}): {ec.description}"
            if ec.formula:
                line += f"  |  formula={ec.formula}"
            if ec.usage_hint:
                line += f"  |  hint={ec.usage_hint}"
            attr_lines.append(line)
        blocks.append(
            "### Enriched Attributes (already created — use for context)\n"
            + "\n".join(attr_lines)
        )

    # Block 4 — Deterministic edges already created
    if det_result.edges:
        edge_lines = [
            f"  - ({e.source_term}) --[{e.name}]--> ({e.target_term})"
            f"  [{e.provenance.derivation}]"
            for e in det_result.edges
        ]
        blocks.append(
            "### Deterministic Edges (already created — do NOT re-propose)\n"
            + "\n".join(edge_lines)
        )

    # Block 5 — Denormalized candidates
    if det_result.denormalized_candidates:
        cand_lines = [
            f"  - {dc.column_name} -> possibly {dc.inferred_entity_name}"
            f"  (pattern: {dc.pattern}, type: {dc.column_type})"
            for dc in det_result.denormalized_candidates
        ]
        blocks.append(
            "### Denormalized Candidates (decide: inferred BusinessTerm + edge?)\n"
            + "\n".join(cand_lines)
        )

    # Block 6 — FK context
    fk_lines: list[str] = []
    for fk in ctx.get("fks", []):
        fk_lines.append(
            f"  - {fk['source_column']} -> {fk['target_table']}.{fk['target_column']}"
        )
    if fk_lines:
        blocks.append("### Foreign Keys\n" + "\n".join(fk_lines))

    # Block 7 — Top SQL queries
    sql_lines: list[str] = []
    for s in ctx.get("sqls", [])[:10]:
        counter = s.get("total_counter", 0)
        text = (s.get("sql_text") or "")[:300]
        sql_lines.append(f"  [{counter}x] {text}")
    if sql_lines:
        blocks.append("### Top SQL queries (by frequency)\n" + "\n".join(sql_lines))

    # Block 8 — External ontology matches
    if ext_matches:
        ext_lines: list[str] = []
        for em in ext_matches:
            for ec in em.concepts[:3]:
                ext_lines.append(
                    f"  - [{ec.source}/{ec.vocabulary}] {ec.label}"
                    + (f": {ec.definition[:100]}" if ec.definition else "")
                )
        if ext_lines:
            blocks.append(
                "### External Ontology Matches (use as naming hints)\n"
                + "\n".join(ext_lines)
            )

    # Block 9 — BIRD evidence
    evidence = ctx.get("evidence", [])
    if evidence:
        ev_lines = [f"  - {ev}" for ev in evidence[:15]]
        blocks.append(
            "### Domain Evidence (expert-annotated knowledge)\n" + "\n".join(ev_lines)
        )

    return "\n\n".join(blocks)


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------


def invoke_proposer(
    table: dict[str, Any],
    ctx: dict[str, Any],
    det_result: DeterministicResult,
    ext_matches: list[ExternalMatch],
    ontology: CoreOntology,
    enriched_columns: list[EnrichedColumn] | None = None,
) -> DeltaOntology:
    """Call the Gen-LLM to propose a DeltaOntology for one table."""
    user_prompt = _build_user_prompt(
        table, ctx, det_result, ext_matches, ontology, enriched_columns
    )

    messages = [
        SystemMessage(content=_SYSTEM_PROMPT),
        HumanMessage(content=user_prompt),
    ]

    logger.info(
        "  [proposer] Invoking Gen-LLM for %s (prompt ~%d chars)",
        table["name"],
        len(user_prompt),
    )

    result = invoke_structured(
        messages,
        DeltaOntology,
        temperature=0.1,
        max_tokens=4096,
    )

    logger.info(
        "  [proposer] Result: %d business_terms, %d obj_props",
        len(result.business_terms),
        len(result.object_properties),
    )

    return result
