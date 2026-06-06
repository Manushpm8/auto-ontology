"""Phase 1: LLM seed table selection (first BFS tree only)."""

from __future__ import annotations

import logging
from typing import Any

from langchain_core.messages import HumanMessage, SystemMessage

from gsf.semantic.domain import DomainSummary, load_system_prompt
from gsf.semantic.llm import invoke_structured
from gsf.semantic.models import SeedSelectionResult

logger = logging.getLogger(__name__)

_SEED_SYSTEM = """\
Select exactly ONE physical table to seed semantic compilation.
Prefer hub tables with rich connectivity and clear business meaning.
Return the table name exactly as given in the catalog."""


def select_seed_table(
    tables: list[dict[str, Any]],
    domain_summary: DomainSummary | None = None,
) -> dict[str, Any]:
    if not tables:
        raise ValueError("No tables available for seed selection")
    if len(tables) == 1:
        return tables[0]

    lines = []
    for t in tables:
        desc = t.get("description") or "(no description)"
        lines.append(f"- {t['name']}: {desc} (queries={t.get('query_count', 0)})")

    domain_block = ""
    if domain_summary:
        domain_block = (
            f"\nDomains: {', '.join(domain_summary.domains[:10])}\n"
            f"Core entities: {', '.join(domain_summary.core_entities[:15])}\n"
        )

    system = load_system_prompt() + "\n\n" + _SEED_SYSTEM
    prompt = (
        "## Catalog\n" + "\n".join(lines) + domain_block + "\nSelect one seed table."
    )

    try:
        result = invoke_structured(
            [SystemMessage(content=system), HumanMessage(content=prompt)],
            SeedSelectionResult,
        )
        for t in tables:
            if t["name"].lower() == result.table_name.lower():
                logger.info("Seed %r: %s", t["name"], result.rationale)
                return t
        logger.warning("LLM seed %r not in catalog", result.table_name)
    except Exception:
        logger.warning("Seed LLM failed — using top query_count table")

    return tables[0]
