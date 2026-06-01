"""Column enrichment via LLM for the Rigor pipeline.

One LLM call per table: takes the raw deterministic attributes and
enriches each with a canonical name, description, optional formula,
and optional usage hint.  The enriched info is used both on the
Attribute nodes and as context for the proposer.
"""

from __future__ import annotations

import logging
from typing import Any

from langchain_core.messages import HumanMessage, SystemMessage

from gsf.ontology.domain_prereading.llm import invoke_structured
from gsf.ontology.rigor.models import (
    ColumnEnrichmentResult,
    EnrichedColumn,
    ProposedAttribute,
)

logger = logging.getLogger(__name__)

_SYSTEM_PROMPT = """\
You are a data analyst enriching column metadata for a relational \
database table. For EACH column listed below, provide:

1. **canonical_name** — a clear, business-friendly CamelCase name. \
If the column name is already meaningful (e.g. "gender", "amount"), \
keep it as-is but in CamelCase. If the name is opaque (e.g. "A2"), \
use the description and evidence to infer the real name.

2. **description** — one sentence explaining what this column \
represents in business terms. Use the column description and \
evidence to write an accurate description.

3. **formula** (optional) — if this column is computed or derived \
from other columns, describe the formula (e.g. "revenue - cost"). \
Set to null if not applicable.

4. **usage_hint** (optional) — practical guidance for using this \
column in queries or analysis (e.g. "filter by this for regional \
breakdowns", "join key for transaction lookups"). Set to null if \
not applicable.

You MUST return exactly one entry per input column, using the exact \
`source_column` name provided."""


def _build_enrichment_prompt(
    attributes: list[ProposedAttribute],
    ctx: dict[str, Any],
    evidence: list[str],
) -> str:
    """Build the user prompt listing columns and available context."""
    col_map: dict[str, dict[str, Any]] = {}
    for c in ctx.get("columns", []):
        col_map[c["name"]] = c

    blocks: list[str] = []

    blocks.append(
        f"## Table: {ctx.get('table_name', '(unknown)')}\n"
        f"Description: {ctx.get('table_description') or '(none)'}"
    )

    col_lines: list[str] = []
    for attr in attributes:
        col = col_map.get(attr.source_column, {})
        samples = col.get("sample_values")
        sample_str = (
            ", ".join(str(s) for s in samples[:5])
            if isinstance(samples, list) and samples
            else "(none)"
        )
        col_lines.append(
            f"  - source_column: {attr.source_column}\n"
            f"    datatype: {attr.datatype}\n"
            f"    description: {col.get('description') or '(none)'}\n"
            f"    samples: [{sample_str}]"
        )
    blocks.append("### Columns to enrich\n" + "\n".join(col_lines))

    if evidence:
        ev_lines = [f"  - {ev}" for ev in evidence[:20]]
        blocks.append("### Domain Evidence\n" + "\n".join(ev_lines))

    return "\n\n".join(blocks)


def enrich_attributes(
    attributes: list[ProposedAttribute],
    ctx: dict[str, Any],
    evidence: list[str],
) -> list[EnrichedColumn]:
    """Enrich raw attributes with canonical names and descriptions.

    Returns one EnrichedColumn per input attribute.  If the LLM call
    fails or returns partial results, missing columns get a passthrough
    enrichment (raw column name as canonical name, empty description).
    """
    if not attributes:
        return []

    user_prompt = _build_enrichment_prompt(attributes, ctx, evidence)

    messages = [
        SystemMessage(content=_SYSTEM_PROMPT),
        HumanMessage(content=user_prompt),
    ]

    logger.info(
        "  [enricher] Enriching %d columns (prompt ~%d chars)",
        len(attributes),
        len(user_prompt),
    )

    result = invoke_structured(
        messages,
        ColumnEnrichmentResult,
        temperature=0.0,
        max_tokens=4096,
    )

    enriched_map: dict[str, EnrichedColumn] = {}
    if result and result.columns:
        for ec in result.columns:
            enriched_map[ec.source_column] = ec

    final: list[EnrichedColumn] = []
    for attr in attributes:
        if attr.source_column in enriched_map:
            final.append(enriched_map[attr.source_column])
        else:
            final.append(
                EnrichedColumn(
                    source_column=attr.source_column,
                    canonical_name=attr.name,
                    description="",
                )
            )

    logger.info(
        "  [enricher] Enriched %d/%d columns via LLM",
        len(enriched_map),
        len(attributes),
    )

    return final
