"""Judge-LLM for the Rigor pipeline.

Reviews the Gen-LLM's proposed DeltaOntology against the current
CoreOntology and checks for:
  1. Duplicate business terms (same entity under different names)
  2. Naming quality (clear, consistent, CamelCase)
  3. Consistency (edges reference valid business terms)
  4. Merge instructions (proposed term should merge with existing)
"""

from __future__ import annotations

import logging

from langchain_core.messages import HumanMessage, SystemMessage

from gsf.ontology.domain_prereading.llm import invoke_structured
from gsf.ontology.rigor.models import (
    CoreOntology,
    DeltaOntology,
    JudgeVerdict,
)

logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# System prompt
# ---------------------------------------------------------------------------

_JUDGE_SYSTEM_PROMPT = """\
You are an ontology quality reviewer. You receive:
1. A proposed DeltaOntology (new business terms and object properties) \
from a generative model.
2. The current CoreOntology built so far.

Attributes are handled automatically and are NOT part of the review.
Leave `approved_attributes` empty.

Your job is to validate the proposed Delta and return a JudgeVerdict.

## Checks to perform

### 1. Duplicate Detection
- Does the proposed business term already exist in the CoreOntology under a \
different name? E.g., "Client" vs "Customer", "Product" vs "Item".
- If yes: add a MergeInstruction (proposed_name -> merge_into existing name).
- Only merge if they truly refer to the same business entity.

### 2. Naming Quality
- Business term names must be CamelCase (e.g. "OrderItem", not "order_item").
- Relationship names should be camelCase verbs (e.g. "placedBy", "contains").
- If a name is unclear or misleading, reject it with a reason.

### 3. Consistency
- Every ObjectProperty must reference business terms that either:
  a) Already exist in the CoreOntology, OR
  b) Are being proposed in this same Delta.
- If an edge references a business term that doesn't exist and isn't \
proposed, reject the edge.

## Output rules

- approved_business_terms: business terms that pass all checks \
(with any name fixes applied).
- approved_attributes: leave EMPTY (attributes are deterministic).
- approved_object_properties: object properties that pass.
- rejected: elements that fail checks, with reasons.
- merge_instructions: for duplicate business terms, specify which proposed \
name should be merged into which existing name.

Be conservative with rejections — only reject elements with clear problems. \
When in doubt, approve."""


# ---------------------------------------------------------------------------
# Prompt builder
# ---------------------------------------------------------------------------


def _build_judge_prompt(
    delta: DeltaOntology,
    ontology: CoreOntology,
) -> str:
    """Build the user prompt for the Judge-LLM."""
    blocks: list[str] = []

    # Current ontology state
    blocks.append(ontology.snapshot_for_prompt())

    # Proposed delta
    blocks.append("## Proposed Delta (to review)")

    if delta.business_terms:
        term_lines = []
        for t in delta.business_terms:
            parent_tag = f" (subclass of {t.parent})" if t.parent else ""
            term_lines.append(f"  - {t.name}{parent_tag}: {t.description}")
        blocks.append("### Proposed Business Terms\n" + "\n".join(term_lines))

    if delta.object_properties:
        op_lines = [
            f"  - ({op.source_term}) --[{op.name}]--> ({op.target_term})"
            for op in delta.object_properties
        ]
        blocks.append("### Proposed ObjectProperties\n" + "\n".join(op_lines))

    if delta.role_relationships:
        primary = delta.business_terms[0].name if delta.business_terms else "?"
        role_lines = [
            f"  - {primary} {{role: {r.role}}} {r.target_term}"
            for r in delta.role_relationships
        ]
        blocks.append("### Proposed Role Relationships\n" + "\n".join(role_lines))

    if delta.part_of_target:
        blocks.append(f"### Proposed part_of target: {delta.part_of_target}")

    if (
        not delta.business_terms
        and not delta.object_properties
        and not delta.role_relationships
    ):
        blocks.append("(Empty delta — nothing to review.)")

    return "\n\n".join(blocks)


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------


def invoke_judge(
    delta: DeltaOntology,
    ontology: CoreOntology,
) -> JudgeVerdict:
    """Call the Judge-LLM to validate a proposed DeltaOntology."""
    if (
        not delta.business_terms
        and not delta.object_properties
        and not delta.role_relationships
    ):
        logger.info("  [judge] Empty delta — auto-approving.")
        return JudgeVerdict()

    user_prompt = _build_judge_prompt(delta, ontology)

    messages = [
        SystemMessage(content=_JUDGE_SYSTEM_PROMPT),
        HumanMessage(content=user_prompt),
    ]

    logger.info(
        "  [judge] Invoking Judge-LLM (prompt ~%d chars)",
        len(user_prompt),
    )

    verdict = invoke_structured(
        messages,
        JudgeVerdict,
        temperature=0.0,
        max_tokens=4096,
    )

    logger.info(
        "  [judge] Verdict: %d approved terms, %d approved OPs, %d rejected, %d merges",
        len(verdict.approved_business_terms),
        len(verdict.approved_object_properties),
        len(verdict.rejected),
        len(verdict.merge_instructions),
    )

    return verdict
