"""LLM Term extraction and hierarchy proposals."""

from __future__ import annotations

import re
from typing import Any

from langchain_core.messages import HumanMessage, SystemMessage

from gsf.semantic.deterministic import to_term_name
from gsf.semantic.domain import DomainSummary
from gsf.semantic.llm import invoke_structured
from gsf.semantic.models import TermProposal
from gsf.semantic import neo4j_dal

_INVALID = {"unnamed", "unknown", "none", ""}
_TERM_RE = re.compile(r"^[A-Z][A-Za-z0-9]+$")

_SYSTEM = """\
You identify the single core business Term for a relational table.
Return CamelCase Term name and a one-sentence description.
Optionally propose IS_A parent or PART_OF container if clearly implied.
Do not propose ROLE relationships here."""


def extract_term(
    table: dict[str, Any],
    ctx: dict[str, Any],
    *,
    domain_summary: DomainSummary | None = None,
) -> TermProposal:
    neighbor_terms = neo4j_dal.fetch_neighbor_terms(table["id"])
    col_lines = [
        f"  - {c['name']} ({c.get('data_type', '')})"
        for c in ctx.get("columns", [])[:30]
    ]
    domain_block = ""
    if domain_summary:
        domain_block = (
            f"\nDomains: {', '.join(domain_summary.domains[:8])}\n"
            f"Core entities: {', '.join(domain_summary.core_entities[:12])}\n"
        )
    prompt = (
        f"Table: {table['name']}\n"
        f"Description: {table.get('description') or ''}\n"
        f"Columns:\n" + "\n".join(col_lines) + "\n"
        f"Known neighbor Terms: {', '.join(neighbor_terms) or '(none)'}\n"
        f"{domain_block}"
    )
    try:
        result = invoke_structured(
            [SystemMessage(content=_SYSTEM), HumanMessage(content=prompt)],
            TermProposal,
            temperature=0.0,
        )
    except Exception:
        provisional = to_term_name(table["name"])
        return TermProposal(name=provisional, description=f"From table {table['name']}")

    name = result.name.strip()
    if name.lower() in _INVALID or not _TERM_RE.match(name):
        name = to_term_name(table["name"])
    result.name = name
    return result
