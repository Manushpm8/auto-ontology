"""Gen-LLM Proposer for the Rigor pipeline.

For each table, the Proposer receives:
  - The table's columns, FKs, and SQL queries (from Neo4j)
  - Deterministic edges already created
  - Denormalized candidates to evaluate
  - External ontology matches (LOV, BioPortal, FIBO)
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

from gsf.ontology.domain_prereading.models import DomainSummary
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

- ALWAYS propose exactly ONE primary BusinessTerm for this table (the \
business entity it represents). Use CamelCase naming (e.g. "Customer", \
"Transaction").
- Choose a clear, full business name — do NOT keep abbreviations or raw \
table names. For example, if the table is "trans", name the term \
"Transaction"; if "acct", name it "Account".
- Do NOT propose additional BusinessTerms from columns — columns are \
already handled as Attributes. A column like "type" with enumerated \
values (OWNER, USER) is an attribute, not a separate business entity.
- If this table's business term is a specialization of an existing term, \
set `parent` to the parent term name (is_a relationship).
- If this table represents a component of a broader entity, set \
`part_of_target` to the parent Term name.
- Optionally propose `role_relationships` for peer associations between \
this term and existing terms (e.g. assigned_to, plays).
- Some terms in "Existing Business Terms" are auto-generated placeholders \
with raw table names (e.g. "Trans", "Acct"). You should propose the \
correct business name — the system will handle the rename. Only reuse \
an existing name if it is already a good business-friendly name.

## 2. ObjectProperties (edges between business terms)

- Deterministic edges (FK, implicit FK, self-ref) are already handled. \
Do NOT re-propose them.
- You may propose additional semantic relationships you detect from \
context (e.g. hierarchy, composition, temporal ordering).

## Rules

- ALWAYS propose exactly ONE BusinessTerm for this table — no more.
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
    domain_summary: DomainSummary | None = None,
) -> str:
    """Assemble the user prompt with all retrieval context."""
    blocks: list[str] = []

    # Block 1 — Existing ontology state
    blocks.append(ontology.snapshot_for_prompt())

    if domain_summary:
        domains = ", ".join(domain_summary.domains[:10])
        entities = ", ".join(domain_summary.core_entities[:15])
        blocks.append(
            f"## Domain Context\nDomains: {domains}\nCore entities: {entities}"
        )

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

    # Block 5 — Denormalized entity candidates
    if det_result.denormalized_candidates:
        dc_lines = [
            f"  - {dc.column_name} -> inferred entity {dc.inferred_entity_name} "
            f"({dc.pattern})"
            for dc in det_result.denormalized_candidates
        ]
        blocks.append(
            "### Denormalized Column Candidates (evaluate: attribute vs entity)\n"
            + "\n".join(dc_lines)
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
    domain_summary: DomainSummary | None = None,
) -> DeltaOntology:
    """Call the Gen-LLM to propose a DeltaOntology for one table."""
    user_prompt = _build_user_prompt(
        table,
        ctx,
        det_result,
        ext_matches,
        ontology,
        enriched_columns,
        domain_summary=domain_summary,
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
