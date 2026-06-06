"""BFS traversal and TablesQueue for semantic compilation.

Manages seed selection, priority-ordered table discovery, and reviewed
state on physical Table nodes in Neo4j.
"""

from __future__ import annotations

import heapq
import logging
from dataclasses import dataclass, field
from enum import IntEnum
from typing import Any

from langchain_core.messages import HumanMessage, SystemMessage
from pydantic import BaseModel, Field

from gsf.ontology.domain_prereading.llm import invoke_structured
from gsf.ontology.domain_prereading.models import DomainSummary
from gsf.ontology.rigor.loaders import (
    fetch_existing_joins,
    fetch_sorted_tables,
    fetch_table_context,
)
from nemo_retriever.tabular_data.ingestion.model.reserved_words import (
    Edges,
    Labels,
)
from nemo_retriever.tabular_data.neo4j import get_neo4j_conn

logger = logging.getLogger(__name__)

RIGOR_SOURCE = "rigor"


class QueuePriority(IntEnum):
    """Lower value = higher priority (min-heap)."""

    JOIN = 1
    FK = 2
    VDB = 3


@dataclass(order=True)
class QueueEntry:
    priority: int
    hop: int
    table_id: str = field(compare=False)
    table_name: str = field(compare=False)
    source: str = field(compare=False, default="")


class SeedSelectionResult(BaseModel):
    """LLM output for Phase 1 seed table selection."""

    table_name: str = Field(..., description="Physical table name to seed BFS.")
    rationale: str = Field(default="", description="Why this table was chosen.")


class BusinessQuestionsResult(BaseModel):
    """LLM output: simple 2-entity business questions for VDB discovery."""

    questions: list[str] = Field(
        default_factory=list,
        description="Up to 3 simple business questions spanning two entities max.",
    )
    entities: list[str] = Field(
        default_factory=list,
        description="Business entity names extracted from the questions.",
    )


_SEED_SYSTEM = """\
You are selecting the optimal seed table to begin semantic ontology \
compilation over a relational database. Pick exactly ONE table that best \
represents a central business entity and has rich connectivity to the rest \
of the schema. Prefer hub tables referenced by many foreign keys or query \
joins. Return the physical table name exactly as given in the catalog."""


_QUESTION_SYSTEM = """\
Given a database table and its business context, generate up to 3 simple \
natural-language business questions that span at most TWO business entities. \
Also extract the business entity names mentioned. Questions should help \
discover related tables in the schema."""


def _catalog_block(tables: list[dict[str, Any]]) -> str:
    lines = []
    for t in tables:
        desc = t.get("description") or "(no description)"
        lines.append(f"- {t['name']}: {desc} (queries={t.get('query_count', 0)})")
    return "\n".join(lines)


def select_seed_table(
    tables: list[dict[str, Any]],
    domain_summary: DomainSummary | None = None,
) -> dict[str, Any]:
    """Phase 1: pick one seed table via LLM or fallback heuristics."""
    if not tables:
        raise ValueError("No tables available for seed selection")

    if len(tables) == 1:
        return tables[0]

    domain_block = ""
    if domain_summary:
        entities = ", ".join(domain_summary.core_entities[:20])
        domains = ", ".join(domain_summary.domains[:10])
        domain_block = (
            f"\n\nDomain context:\nDomains: {domains}\nCore entities: {entities}\n"
        )

    user_prompt = (
        f"## Table catalog\n{_catalog_block(tables)}"
        f"{domain_block}\n\nSelect the single best seed table."
    )

    try:
        result = invoke_structured(
            [
                SystemMessage(content=_SEED_SYSTEM),
                HumanMessage(content=user_prompt),
            ],
            SeedSelectionResult,
            temperature=0.0,
            max_tokens=512,
        )
        for t in tables:
            if t["name"].lower() == result.table_name.lower():
                logger.info(
                    "Seed table %r selected: %s",
                    t["name"],
                    result.rationale,
                )
                return t
        logger.warning(
            "LLM seed %r not in catalog — falling back to top query_count",
            result.table_name,
        )
    except Exception:
        logger.warning("Seed selection LLM failed — using query_count fallback")

    return tables[0]


def generate_business_questions(
    table: dict[str, Any],
    ctx: dict[str, Any],
    term_name: str,
) -> BusinessQuestionsResult:
    """Generate 2-entity business questions for VDB table discovery."""
    desc = table.get("description") or ctx.get("table_description") or ""
    prompt = (
        f"Table: {table['name']}\n"
        f"Business term: {term_name}\n"
        f"Description: {desc}\n"
        f"Columns: {', '.join(c['name'] for c in ctx.get('columns', [])[:20])}"
    )
    try:
        return invoke_structured(
            [
                SystemMessage(content=_QUESTION_SYSTEM),
                HumanMessage(content=prompt),
            ],
            BusinessQuestionsResult,
            temperature=0.2,
            max_tokens=1024,
        )
    except Exception:
        logger.warning("Business question generation failed for %s", table["name"])
        return BusinessQuestionsResult()


def discover_tables_via_vdb(
    entities: list[str],
    database_name: str,
    retriever: Any,
    known_table_names: set[str],
    top_k: int = 5,
) -> list[str]:
    """Vector search Data Layer VDB for tables matching business entities."""
    discovered: list[str] = []
    for entity in entities[:5]:
        query = f"table related to {entity} business entity"
        try:
            hits = retriever.retrieve(query, top_k=top_k)
        except Exception:
            logger.warning("VDB search failed for entity %r", entity)
            continue
        for hit in hits or []:
            meta = hit.get("metadata") or hit.get("content_metadata") or {}
            label = meta.get("label", "")
            name = meta.get("name", "")
            if label == Labels.TABLE and name and name not in known_table_names:
                discovered.append(name)
    return list(dict.fromkeys(discovered))


class TablesQueue:
    """Priority queue for BFS table expansion."""

    def __init__(
        self,
        database_name: str,
        schema_name: str,
        tables_by_name: dict[str, dict[str, Any]],
        join_edges: list[dict[str, Any]] | None = None,
    ) -> None:
        self.database_name = database_name
        self.schema_name = schema_name
        self.tables_by_name = tables_by_name
        self.join_edges = join_edges or []
        self._heap: list[QueueEntry] = []
        self._seen: set[str] = set()

        self._join_neighbors: dict[str, list[tuple[str, int]]] = {}
        for edge in self.join_edges:
            src = edge["source_table"]
            tgt = edge["target_table"]
            self._join_neighbors.setdefault(src, []).append((tgt, 1))
            self._join_neighbors.setdefault(tgt, []).append((src, 1))

    def push(
        self,
        table_name: str,
        priority: QueuePriority,
        hop: int = 0,
        source: str = "",
    ) -> None:
        table = self.tables_by_name.get(table_name)
        if not table or table["id"] in self._seen:
            return
        entry = QueueEntry(
            priority=int(priority),
            hop=hop,
            table_id=table["id"],
            table_name=table_name,
            source=source,
        )
        heapq.heappush(self._heap, entry)

    def push_seed(self, table_name: str) -> None:
        self.push(table_name, QueuePriority.FK, hop=0, source="seed")

    def pop(self) -> QueueEntry | None:
        while self._heap:
            entry = heapq.heappop(self._heap)
            if entry.table_id in self._seen:
                continue
            self._seen.add(entry.table_id)
            return entry
        return None

    def discover_neighbors(
        self,
        table_name: str,
        hop: int,
        vdb_table_names: list[str] | None = None,
    ) -> None:
        """Enqueue FK, join, and VDB-discovered neighbors."""
        table = self.tables_by_name.get(table_name)
        if not table:
            return

        ctx = fetch_table_context(table["id"])
        for fk in ctx.get("fks", []):
            tgt = fk["target_table"]
            if tgt in self.tables_by_name:
                self.push(tgt, QueuePriority.FK, hop=hop + 1, source="fk")

        for neighbor, _ in self._join_neighbors.get(table_name, []):
            if neighbor in self.tables_by_name:
                self.push(neighbor, QueuePriority.JOIN, hop=hop + 1, source="join")

        for name in vdb_table_names or []:
            if name in self.tables_by_name:
                self.push(name, QueuePriority.VDB, hop=hop + 1, source="vdb")


def mark_reviewed(table_id: str) -> None:
    """Tag a physical Table node as reviewed in Neo4j."""
    get_neo4j_conn().query_write(
        f"MATCH (t:{Labels.TABLE} {{id: $table_id}}) "
        "SET t.reviewed = true, t.reviewed_source = $source",
        {"table_id": table_id, "source": RIGOR_SOURCE},
    )


def clear_reviewed_flags(database_name: str, schema_name: str) -> None:
    """Reset reviewed flags before a fresh compilation run."""
    get_neo4j_conn().query_write(
        f"""
        MATCH (db:{Labels.DB} {{name: $db_name}})-[:{Edges.CONTAINS}]->
              (s:{Labels.SCHEMA} {{name: $schema_name}})-[:{Edges.CONTAINS}]->
              (t:{Labels.TABLE})
        SET t.reviewed = false
        REMOVE t.reviewed_source
        """,
        {"db_name": database_name, "schema_name": schema_name},
    )


def discover_unreviewed_tables(
    database_name: str,
    schema_name: str,
) -> list[dict[str, Any]]:
    """Return tables not yet marked reviewed (Phase 5)."""
    rows = get_neo4j_conn().query_read(
        f"""
        MATCH (db:{Labels.DB} {{name: $db_name}})-[:{Edges.CONTAINS}]->
              (s:{Labels.SCHEMA} {{name: $schema_name}})-[:{Edges.CONTAINS}]->
              (t:{Labels.TABLE})
        WHERE coalesce(t.reviewed, false) = false
        RETURN t.id AS id, t.name AS name, s.name AS schema_name,
               t.description AS description
        ORDER BY t.name
        """,
        {"db_name": database_name, "schema_name": schema_name},
    )
    return [
        {
            "id": r["id"],
            "name": r["name"],
            "schema_name": r["schema_name"],
            "description": r.get("description") or "",
            "query_count": 0,
        }
        for r in rows
    ]


def build_tables_index(
    database_name: str,
    schema_name: str,
    skip_threshold: int = 0,
) -> tuple[list[dict[str, Any]], dict[str, dict[str, Any]]]:
    """Load all tables and index by name."""
    tables = fetch_sorted_tables(database_name, skip_threshold, schema_name=schema_name)
    return tables, {t["name"]: t for t in tables}


def load_join_edges(database_name: str, schema_name: str) -> list[dict[str, Any]]:
    return fetch_existing_joins(database_name, schema_name=schema_name)
