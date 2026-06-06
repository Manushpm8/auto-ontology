"""Phase 1 — Per-Table Attributes & Concept Assignment (value-add only).

.. deprecated::
    Use ``gsf.ontology.rigor.compile.run_semantic_compilation`` instead.
    The Concept/BELONGS_TO model here is superseded by Term/Attribute
    semantic compilation in ``gsf/ontology/rigor/``.

Reads schema + query stats from Neo4j, calls the LLM per table with
accumulated context (domain summary, FK neighbours, running glossary,
existing concepts), and writes extracted Attribute nodes and Concept
assignments back to Neo4j.

Only attributes that add **new semantic knowledge** are extracted — not
redundant 1:1 column-name restatements. Attribute types:

- DERIVED_METRIC  — multi-column computed concept
- KPI             — business metric / aggregate
- BUSINESS_RULE   — canonical filter / condition
- DOMAIN_CONCEPT  — inferred concept not directly a column
- SKIP            — column to ignore

Each table is also assigned to a Concept (business domain). Tables
connected by foreign keys or frequently joined together should share
the same concept.
"""

from __future__ import annotations

import logging
from typing import Any, Literal

from langchain_core.messages import SystemMessage
from pydantic import BaseModel, Field

from nemo_retriever.tabular_data.ingestion.model.reserved_words import (
    Edges,
    Labels,
)
from nemo_retriever.tabular_data.neo4j import get_neo4j_conn
from nemo_retriever.tabular_data.retrieval.llm_invoke import (
    get_llm_client,
    invoke_with_structured_output,
)

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Pydantic models — structured LLM output
# ---------------------------------------------------------------------------

ATTRIBUTE_TYPES = Literal[
    "DERIVED_METRIC",
    "KPI",
    "BUSINESS_RULE",
    "DOMAIN_CONCEPT",
    "SKIP",
]


class ExtractedAttribute(BaseModel):
    """A value-add semantic attribute extracted from a table's schema.

    Only concepts that add new knowledge beyond what column names /
    types / descriptions already convey.
    """

    canonical_name: str = Field(
        ...,
        description=(
            'Human-readable name. E.g. "Profit Margin", '
            '"Average Order Value", "Active Orders Filter".'
        ),
    )
    attribute_type: ATTRIBUTE_TYPES = Field(
        ...,
        description=(
            "DERIVED_METRIC: multi-column formula. "
            "KPI: business metric / aggregate. "
            "BUSINESS_RULE: canonical filter / condition. "
            "DOMAIN_CONCEPT: inferred concept not directly a column. "
            "SKIP: audit/technical column to ignore."
        ),
    )
    source_columns: list[str] = Field(
        default_factory=list,
        description=(
            "Column(s) this attribute is derived from. "
            "Empty for table-wide domain concepts. "
            'Multiple entries for derived metrics (e.g. ["revenue", "cost"]).'
        ),
    )
    expression: str = Field(
        "",
        description=(
            "SQL expression for computable attributes. "
            'E.g. "SUM(revenue) - SUM(cost)" for a derived metric, '
            '"SUM(amount) / COUNT(DISTINCT order_id)" for a KPI. '
            "Empty for non-computable attributes."
        ),
    )
    canonical_filter: str = Field(
        "",
        description=(
            "SQL WHERE clause for BUSINESS_RULE attributes. "
            "E.g. \"status != 'cancelled' AND is_test = 0\". "
            "Empty for non-rule attributes."
        ),
    )
    skip_reason: str = Field(
        "",
        description="Why this column should be ignored. Empty if not SKIP.",
    )
    confidence: float = Field(
        ...,
        ge=0.0,
        le=1.0,
        description="Confidence in this extraction (0-1).",
    )


class ConceptAssignment(BaseModel):
    """Assigns a table to a business-domain concept."""

    concept_name: str = Field(
        ...,
        description=(
            "Name of the concept this table belongs to. "
            "Use an existing concept name if it fits, "
            "otherwise create a new one. "
            'E.g. "Commerce", "Support", "HR".'
        ),
    )
    description: str = Field(
        ...,
        description=(
            "Short description of what this concept covers. "
            "Required for new concepts; can refine for existing ones."
        ),
    )


class TableAttributesResult(BaseModel):
    """Value-add attributes and concept assignment for one table."""

    attributes: list[ExtractedAttribute] = Field(
        ...,
        description=(
            "List of semantic attributes that add NEW knowledge. "
            "Include: "
            "1) DERIVED_METRIC for multi-column computations. "
            "2) KPI for business metrics / aggregates. "
            "3) BUSINESS_RULE for canonical filters analysts always apply. "
            "4) DOMAIN_CONCEPT for inferred concepts not visible "
            "from column names alone. "
            "5) SKIP for audit/technical columns to ignore."
        ),
    )
    concept: ConceptAssignment = Field(
        ...,
        description="The business-domain concept this table belongs to.",
    )


# ---------------------------------------------------------------------------
# Shared-state containers (accumulated across the table loop)
# ---------------------------------------------------------------------------


class GlossaryEntry(BaseModel):
    canonical_name: str
    attribute_type: str
    confidence: float = 0.0
    definition: str = ""

    def confidence_tag(self) -> str:
        if self.confidence >= 0.90:
            return "CONFIRMED"
        if self.confidence >= 0.50:
            return "TENTATIVE"
        return "UNCERTAIN"


# ---------------------------------------------------------------------------
# Neo4j queries
# ---------------------------------------------------------------------------

_FETCH_TABLES_QUERY = f"""
MATCH (db:{Labels.DB} {{name: $db_name}})-[:{Edges.CONTAINS}]->
      (s:{Labels.SCHEMA})-[:{Edges.CONTAINS}]->(t:{Labels.TABLE})
OPTIONAL MATCH (t)<-[:{Edges.SQL}]-(sql:{Labels.SQL})
WITH t, s, count(DISTINCT sql) AS query_count
RETURN t.id          AS id,
       t.name        AS name,
       s.name        AS schema_name,
       t.pk          AS pk,
       t.description AS description,
       query_count
ORDER BY query_count DESC
"""

_FETCH_COLUMNS_QUERY = f"""
MATCH (t:{Labels.TABLE} {{id: $table_id}})-[:{Edges.CONTAINS}]->(c:{Labels.COLUMN})
OPTIONAL MATCH (c)<-[:{Edges.SQL}]-(sql:{Labels.SQL})
WITH c, count(DISTINCT sql) AS sql_ref_count
ORDER BY c.ordinal_position
RETURN c.id              AS id,
       c.name            AS name,
       c.data_type       AS data_type,
       c.description     AS description,
       c.sample_values   AS sample_values,
       c.ordinal_position AS ordinal_position,
       sql_ref_count
"""

_FETCH_FKS_QUERY = f"""
MATCH (t:{Labels.TABLE} {{id: $table_id}})-[:{Edges.CONTAINS}]->(src:{Labels.COLUMN})
      -[:{Edges.FOREIGN_KEY}]->(tgt:{Labels.COLUMN})<-[:{Edges.CONTAINS}]-
      (tgt_table:{Labels.TABLE})
RETURN src.name       AS source_column,
       tgt.name       AS target_column,
       tgt_table.name AS target_table
"""

_FETCH_SQL_TEXTS_QUERY = f"""
MATCH (t:{Labels.TABLE} {{id: $table_id}})<-[:{Edges.SQL}]-(sql:{Labels.SQL})
RETURN sql.sql_full_query AS sql_text,
       sql.total_counter  AS total_counter
ORDER BY sql.total_counter DESC
LIMIT $limit
"""


def _fetch_sorted_tables(
    database_name: str,
    skip_threshold: int,
) -> list[dict[str, Any]]:
    """Return tables sorted by query count (descending), skipping below threshold."""
    conn = get_neo4j_conn()
    rows = conn.query_read(_FETCH_TABLES_QUERY, {"db_name": database_name})
    tables = []
    for r in rows:
        qc = int(r.get("query_count") or 0)
        if qc < skip_threshold:
            logger.debug(
                "Skipping table %s (query_count=%d < threshold=%d)",
                r["name"],
                qc,
                skip_threshold,
            )
            continue
        tables.append(
            {
                "id": r["id"],
                "name": r["name"],
                "schema_name": r["schema_name"],
                "pk": r.get("pk"),
                "description": r.get("description") or "",
                "query_count": qc,
            }
        )
    return tables


def _fetch_table_context(
    table_id: str,
    sql_limit: int = 20,
) -> dict[str, Any]:
    """Fetch columns, FKs, and top SQL texts for a single table."""
    conn = get_neo4j_conn()
    columns = conn.query_read(_FETCH_COLUMNS_QUERY, {"table_id": table_id})
    fks = conn.query_read(_FETCH_FKS_QUERY, {"table_id": table_id})
    sqls = conn.query_read(
        _FETCH_SQL_TEXTS_QUERY,
        {"table_id": table_id, "limit": sql_limit},
    )
    return {"columns": columns, "fks": fks, "sqls": sqls}


# ---------------------------------------------------------------------------
# Concept queries
# ---------------------------------------------------------------------------

CONCEPT_LABEL = "Concept"
BELONGS_TO_EDGE = "BELONGS_TO"

_FETCH_CONCEPTS_QUERY = f"""
MATCH (concept:{CONCEPT_LABEL} {{source: $source}})
RETURN concept.name        AS name,
       concept.description AS description
"""


def _fetch_existing_concepts() -> list[dict[str, Any]]:
    """Return all Concept nodes created by phase1."""
    conn = get_neo4j_conn()
    rows = conn.query_read(_FETCH_CONCEPTS_QUERY, {"source": EXTRACTION_SOURCE})
    return [
        {
            "name": r["name"],
            "description": r.get("description") or "",
        }
        for r in rows
    ]


# ---------------------------------------------------------------------------
# Prompt builder
# ---------------------------------------------------------------------------

_SYSTEM_PROMPT = """\
You are an expert data engineer. You have two tasks:

## Task 1 — Extract value-add attributes

Analyse the table below and extract only attributes that add NEW \
semantic knowledge — things that cannot be learned just by reading \
the column names, types, and descriptions.

Do NOT create attributes that simply restate what a column already says \
(e.g. do not map "amount" to "Amount" or "date" to "Order Date"). \
The raw schema is already available; your job is to find hidden value.

Attribute types you may use:

1. **DERIVED_METRIC** — a computed value spanning multiple columns. \
Example: "Profit Margin" with expression "SUM(revenue) - SUM(cost)" \
and source_columns ["revenue", "cost"].

2. **KPI** — a business metric or aggregate that analysts track. \
Example: "Average Order Value" with expression \
"SUM(amount) / COUNT(DISTINCT order_id)" and source_columns \
["amount", "order_id"].

3. **BUSINESS_RULE** — a canonical filter or condition that analysts \
always apply when querying this table. \
Example: "Active Orders" with canonical_filter \
"status != 'cancelled' AND is_test = 0" \
and source_columns ["status", "is_test"].

4. **DOMAIN_CONCEPT** — a concept inferred from the table structure, \
query patterns, or domain knowledge that is not directly a column. \
Example: "Customer Lifetime Value" inferred from a transactions table \
with customer_id + amount + date columns.

5. **SKIP** — an audit or technical column that should be ignored \
in analytical queries (created_at, updated_at, etl_batch_id, etc.). \
Include a skip_reason.

Attribute rules:
- Only extract attributes that genuinely add value.
- It is OK to return an empty attributes list if none exist.
- Use the FK context and glossary as hints for domain understanding.
- Use the SQL query history to identify real-world usage patterns.
- All fields in each attribute are required.

## Task 2 — Assign a concept (business domain)

You must also assign this table to a **concept** — a business domain \
that groups related tables together.

- Review the "Existing Concepts" section below. If this table belongs \
to one of them, use its **exact name**.
- If no existing concept fits, create a new one with a clear name \
and description.
- Tables connected by foreign keys or frequently joined together \
should belong to the **same concept**.
- Prefer reusing existing concepts over creating new ones."""


def _build_prompt(
    table: dict[str, Any],
    context: dict[str, Any],
    domain_summary: str,
    running_glossary: dict[str, GlossaryEntry],
    table_concept_map: dict[str, str],
    existing_concepts: list[dict[str, Any]],
) -> str:
    """Assemble the multi-block user prompt for one table."""
    blocks: list[str] = []

    # Block 1 — Domain summary
    if domain_summary:
        blocks.append(f"## Domain Summary\n{domain_summary}")
    else:
        blocks.append(
            "## Domain Summary\n(No domain summary available — "
            "infer domain from table/column names and sample values.)"
        )

    # Block 2 — FK context (enriched with resolved concepts)
    fk_lines: list[str] = []
    for fk in context["fks"]:
        target_table = fk["target_table"]
        concept = table_concept_map.get(target_table)
        tag = f" [Concept: {concept}]" if concept else " [UNRESOLVED]"
        fk_lines.append(
            f"  - {fk['source_column']} -> {target_table}.{fk['target_column']}{tag}"
        )
    if fk_lines:
        blocks.append("## Foreign-Key Context\n" + "\n".join(fk_lines))

    # Block 3 — Existing concepts
    if existing_concepts:
        concept_lines: list[str] = []
        for c in existing_concepts:
            concept_lines.append(f'  - {c["name"]}: "{c["description"]}"')
        blocks.append(
            "## Existing Concepts\n"
            "Choose one if this table belongs to it, "
            "or create a new concept if none fits.\n" + "\n".join(concept_lines)
        )
    else:
        blocks.append(
            "## Existing Concepts\n"
            "(No concepts created yet — you must create the first one.)"
        )

    # Block 4 — Running glossary (only entries relevant to this table)
    col_names = {c["name"] for c in context["columns"]}
    relevant_glossary = {k: v for k, v in running_glossary.items() if k in col_names}
    if relevant_glossary:
        gl_lines = [
            f"  - {k}: {v.canonical_name} ({v.attribute_type})"
            f" [{v.confidence_tag()}]" + (f" — {v.definition}" if v.definition else "")
            for k, v in relevant_glossary.items()
        ]
        blocks.append(
            "## Running Glossary (previously seen)\n"
            "Confidence tags: CONFIRMED (trust), "
            "TENTATIVE (use as hint, verify), "
            "UNCERTAIN (reconsider).\n" + "\n".join(gl_lines)
        )

    # Block 5 — Table stats
    table_header = (
        f"## Table: {table['schema_name']}.{table['name']}\n"
        f"Description: {table['description'] or '(none)'}\n"
        f"Primary key: {table.get('pk') or '(none)'}\n"
        f"Total queries referencing this table: {table['query_count']}"
    )
    col_lines: list[str] = []
    for c in context["columns"]:
        samples = c.get("sample_values")
        sample_str = (
            ", ".join(str(s) for s in samples[:5])
            if isinstance(samples, list) and samples
            else "(none)"
        )
        col_lines.append(
            f"  - {c['name']} ({c.get('data_type', '?')})"
            f"  |  sql_refs={c.get('sql_ref_count', 0)}"
            f"  |  samples=[{sample_str}]"
            f"  |  desc={c.get('description') or '(none)'}"
        )

    sql_lines: list[str] = []
    for s in context["sqls"][:10]:
        counter = s.get("total_counter", 0)
        text = (s.get("sql_text") or "")[:300]
        sql_lines.append(f"  [{counter}x] {text}")

    blocks.append(table_header)
    blocks.append("### Columns\n" + "\n".join(col_lines))
    if sql_lines:
        blocks.append("### Top SQL queries (by frequency)\n" + "\n".join(sql_lines))

    return "\n\n".join(blocks)


# ---------------------------------------------------------------------------
# Neo4j write-back — Attribute nodes with HAS_ATTRIBUTE edges
# ---------------------------------------------------------------------------

ATTRIBUTE_LABEL = "Attribute"
HAS_ATTRIBUTE_EDGE = "HAS_ATTRIBUTE"
EXTRACTION_SOURCE = "phase1"

_WRITE_COLUMN_ATTRIBUTE_QUERY = f"""
MATCH (t:{Labels.TABLE} {{id: $table_id}})-[:{Edges.CONTAINS}]->
      (c:{Labels.COLUMN} {{name: $col_name}})
MERGE (c)-[:{HAS_ATTRIBUTE_EDGE}]->(a:{ATTRIBUTE_LABEL} {{
    canonical_name: $canonical_name, source: $source
}})
SET a.attribute_type   = $attribute_type,
    a.expression       = $expression,
    a.canonical_filter = $canonical_filter,
    a.skip_reason      = $skip_reason,
    a.confidence       = $confidence
RETURN c.name AS col_name, a.canonical_name AS attr_name
"""

_WRITE_TABLE_ATTRIBUTE_QUERY = f"""
MATCH (t:{Labels.TABLE} {{id: $table_id}})
MERGE (t)-[:{HAS_ATTRIBUTE_EDGE}]->(a:{ATTRIBUTE_LABEL} {{
    canonical_name: $canonical_name, source: $source
}})
SET a.attribute_type   = $attribute_type,
    a.expression       = $expression,
    a.canonical_filter = $canonical_filter,
    a.confidence       = $confidence
RETURN t.name AS table_name, a.canonical_name AS attr_name
"""


def _write_results_to_neo4j(
    table_id: str,
    result: TableAttributesResult,
) -> int:
    """Create Attribute nodes with HAS_ATTRIBUTE edges.

    - Attributes with empty source_columns attach to the Table node.
    - Attributes with source_columns attach to each referenced Column node.

    Returns the number of Attribute nodes successfully written.
    """
    conn = get_neo4j_conn()
    written = 0

    for attr in result.attributes:
        params = {
            "table_id": table_id,
            "canonical_name": attr.canonical_name,
            "attribute_type": attr.attribute_type,
            "expression": attr.expression,
            "canonical_filter": attr.canonical_filter,
            "skip_reason": attr.skip_reason,
            "confidence": attr.confidence,
            "source": EXTRACTION_SOURCE,
        }

        if not attr.source_columns:
            rows = conn.query_write(_WRITE_TABLE_ATTRIBUTE_QUERY, params)
            if rows:
                logger.info(
                    "  [write] Table Attribute %r (type=%s)",
                    attr.canonical_name,
                    attr.attribute_type,
                )
                written += 1
            else:
                logger.warning(
                    "  [write] table NOT FOUND (table_id=%s)",
                    table_id,
                )
        else:
            for col_name in attr.source_columns:
                col_params = {**params, "col_name": col_name}
                rows = conn.query_write(_WRITE_COLUMN_ATTRIBUTE_QUERY, col_params)
                if rows:
                    logger.info(
                        "  [write] Attribute %r on column %r (type=%s, conf=%.2f)",
                        attr.canonical_name,
                        col_name,
                        attr.attribute_type,
                        attr.confidence,
                    )
                    written += 1
                else:
                    logger.warning(
                        "  [write] column %r NOT FOUND (table_id=%s)",
                        col_name,
                        table_id,
                    )

    return written


# ---------------------------------------------------------------------------
# Neo4j write-back — Concept nodes with BELONGS_TO edges
# ---------------------------------------------------------------------------

_MERGE_CONCEPT_QUERY = f"""
MERGE (concept:{CONCEPT_LABEL} {{name: $concept_name, source: $source}})
SET concept.description = $description
RETURN concept.name AS concept_name
"""

_LINK_ATTRIBUTES_TO_CONCEPT_QUERY = f"""
MATCH (t:{Labels.TABLE} {{id: $table_id}})-[:{HAS_ATTRIBUTE_EDGE}]->
      (a:{ATTRIBUTE_LABEL} {{source: $source}})
MATCH (concept:{CONCEPT_LABEL} {{name: $concept_name, source: $source}})
MERGE (a)-[:{BELONGS_TO_EDGE}]->(concept)
RETURN count(a) AS linked
"""

_LINK_COLUMNS_TO_CONCEPT_QUERY = f"""
MATCH (t:{Labels.TABLE} {{id: $table_id}})-[:{Edges.CONTAINS}]->
      (c:{Labels.COLUMN})
MATCH (concept:{CONCEPT_LABEL} {{name: $concept_name, source: $source}})
MERGE (c)-[:{BELONGS_TO_EDGE}]->(concept)
RETURN count(c) AS linked
"""


def _write_concept_to_neo4j(
    table_id: str,
    concept: ConceptAssignment,
    has_attributes: bool,
) -> bool:
    """Create/update a Concept node and link either Attributes or
    Columns to it.

    - If has_attributes: Attribute -[:BELONGS_TO]-> Concept
      (Column already reaches Concept via Column -[:HAS_ATTRIBUTE]-> Attribute)
    - If no attributes: Column -[:BELONGS_TO]-> Concept (direct fallback)

    No Table -> Concept edge is created.
    Returns True if the concept was successfully written.
    """
    conn = get_neo4j_conn()
    params = {
        "concept_name": concept.concept_name,
        "description": concept.description,
        "source": EXTRACTION_SOURCE,
    }

    rows = conn.query_write(_MERGE_CONCEPT_QUERY, params)
    if not rows:
        logger.warning(
            "  [write] failed to MERGE concept %r",
            concept.concept_name,
        )
        return False

    logger.info("  [write] Concept %r", concept.concept_name)

    link_params = {
        "table_id": table_id,
        "concept_name": concept.concept_name,
        "source": EXTRACTION_SOURCE,
    }

    if has_attributes:
        result = conn.query_write(_LINK_ATTRIBUTES_TO_CONCEPT_QUERY, link_params)
        linked = result[0]["linked"] if result else 0
        logger.info(
            "  [write] %d Attribute(s) -> Concept %r",
            linked,
            concept.concept_name,
        )
    else:
        result = conn.query_write(_LINK_COLUMNS_TO_CONCEPT_QUERY, link_params)
        linked = result[0]["linked"] if result else 0
        logger.info(
            "  [write] %d Column(s) -> Concept %r (no attributes)",
            linked,
            concept.concept_name,
        )

    return True


# ---------------------------------------------------------------------------
# Main loop
# ---------------------------------------------------------------------------


def extract_attributes(
    database_name: str,
    domain_summary: str = "",
    skip_threshold: int = 0,
) -> dict[str, Any]:
    """Run Phase 1 attribute extraction and concept assignment.

    Returns a summary dict with per-table results and the final shared state.
    """
    llm = get_llm_client()

    running_glossary: dict[str, GlossaryEntry] = {}
    table_concept_map: dict[str, str] = {}
    summary: dict[str, Any] = {
        "tables": {},
        "skipped_tables": [],
        "concepts": {},
    }

    tables = _fetch_sorted_tables(database_name, skip_threshold)
    logger.info(
        "Phase 1: processing %d tables for database %r",
        len(tables),
        database_name,
    )

    for idx, table in enumerate(tables, 1):
        table_key = f"{table['schema_name']}.{table['name']}"
        logger.info(
            "[%d/%d] Extracting attributes for %s (queries=%d)",
            idx,
            len(tables),
            table_key,
            table["query_count"],
        )

        context = _fetch_table_context(table["id"])

        cols = context["columns"]
        if not cols:
            logger.warning("Table %s has no columns — skipping.", table_key)
            summary["skipped_tables"].append(table_key)
            continue

        if len(cols) <= 2 and len(context["fks"]) == len(cols):
            logger.info(
                "Table %s looks like a bridge table — skipping.",
                table_key,
            )
            summary["skipped_tables"].append(table_key)
            continue

        existing_concepts = _fetch_existing_concepts()

        user_prompt = _build_prompt(
            table,
            context,
            domain_summary,
            running_glossary,
            table_concept_map,
            existing_concepts,
        )

        messages = [
            SystemMessage(content=_SYSTEM_PROMPT),
            SystemMessage(content=user_prompt),
        ]

        result = invoke_with_structured_output(llm, messages, TableAttributesResult)

        if result is None:
            logger.error("LLM returned None for %s — skipping.", table_key)
            summary["tables"][table_key] = {"error": "LLM returned None"}
            continue

        # --- Update shared state ---
        derived = 0
        kpis = 0
        rules = 0
        domain_concepts = 0
        skipped = 0

        for attr in result.attributes:
            if attr.attribute_type == "SKIP":
                skipped += 1
                continue

            if attr.attribute_type == "DERIVED_METRIC":
                derived += 1
            elif attr.attribute_type == "KPI":
                kpis += 1
            elif attr.attribute_type == "BUSINESS_RULE":
                rules += 1
            elif attr.attribute_type == "DOMAIN_CONCEPT":
                domain_concepts += 1

            for col in attr.source_columns:
                existing = running_glossary.get(col)
                if existing is None or attr.confidence >= existing.confidence:
                    running_glossary[col] = GlossaryEntry(
                        canonical_name=attr.canonical_name,
                        attribute_type=attr.attribute_type,
                        confidence=attr.confidence,
                    )

        written = _write_results_to_neo4j(table["id"], result)

        has_attrs = any(a.attribute_type != "SKIP" for a in result.attributes)
        concept_written = _write_concept_to_neo4j(
            table["id"], result.concept, has_attrs
        )

        table_concept_map[table["name"]] = result.concept.concept_name

        concept_name = result.concept.concept_name
        if concept_name not in summary["concepts"]:
            summary["concepts"][concept_name] = {
                "description": result.concept.description,
                "tables": [],
            }
        summary["concepts"][concept_name]["tables"].append(table["name"])

        summary["tables"][table_key] = {
            "total_columns": len(cols),
            "attributes_returned": len(result.attributes),
            "derived_metrics": derived,
            "kpis": kpis,
            "business_rules": rules,
            "domain_concepts": domain_concepts,
            "skipped": skipped,
            "written_to_neo4j": written,
            "concept": concept_name,
            "concept_written": concept_written,
        }
        logger.info(
            "  -> %d attrs (derived=%d, kpi=%d, rule=%d, "
            "concept=%d, skip=%d) | %d written | concept=%r",
            len(result.attributes),
            derived,
            kpis,
            rules,
            domain_concepts,
            skipped,
            written,
            concept_name,
        )

    summary["running_glossary"] = {
        k: v.model_dump() for k, v in running_glossary.items()
    }
    summary["table_concept_map"] = dict(table_concept_map)

    logger.info(
        "Phase 1 complete: %d tables processed, %d skipped, %d concepts.",
        len(summary["tables"]),
        len(summary["skipped_tables"]),
        len(summary["concepts"]),
    )
    return summary
