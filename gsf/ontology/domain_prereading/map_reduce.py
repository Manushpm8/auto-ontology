# SPDX-FileCopyrightText: Copyright (c) 2024-2026, NVIDIA CORPORATION & AFFILIATES.
# All rights reserved.
# SPDX-License-Identifier: Apache-2.0

"""Iterative knowledge graph construction for the domain pre-reading pipeline.

Processes chunks sequentially so each chunk's output enriches the context
for the next. The final pass cleans up and produces the DomainSummary.
"""

from __future__ import annotations

import logging
from typing import Sequence

from langchain_core.messages import HumanMessage, SystemMessage

from gsf.ontology.domain_prereading.chunker import Chunk
from gsf.ontology.domain_prereading.llm import invoke_text
from gsf.ontology.domain_prereading.models import DomainSummary

logger = logging.getLogger(__name__)

SYSTEM_PROMPT = """\
You are a Business Ontology Architect. Your job is NOT to mirror a database \
schema. Your job is to read database artifacts — tables, columns, foreign \
keys, and SQL queries — and extract the business reality hidden inside them.

You are building a Business Knowledge Graph: a semantic map of what the \
business does, what exists in it, how things relate, and what it measures.

CORE PRINCIPLES:
- A table is not a Concept. A table is evidence that a Concept exists.
- A foreign key is not a relationship. It is evidence that a business \
relationship exists.
- A SQL query is not a data operation. It is a business question encoded \
in code.

Your output must be in business language, not database language:
- Not "fact_orders" → but "Order"
- Not "customer_id FK → customers.id" → but "Customer PLACES Order"
- Not "SELECT SUM(total) GROUP BY channel" → but "Revenue is measured by Channel"

════════════════════════════════════════
THINKING PROCESS (apply before output)
════════════════════════════════════════

From tables, ask:
- What real-world thing does this table track?
- Is it an Entity (exists over time), an Event (happens at a point in time), \
a Metric (a measurement), or a Dimension (a classification)?

From foreign keys, ask:
- What business relationship does this FK encode?
- What verb phrase names this relationship? (PLACES, CONTAINS, BELONGS_TO...)

From queries, ask:
- What business question is this query answering?
- Which concepts are always joined together? → relationship signal
- What is being measured? (SUM/COUNT/AVG) → metric signal
- What filters reveal lifecycle states or business rules? (WHERE status='X')
- What groupings reveal business dimensions? (GROUP BY channel/region/date)

════════════════════════════════════════
OUTPUT FORMAT
════════════════════════════════════════

Output a YAML-like knowledge graph with these sections:

DOMAINS:
  - name: ""           # specific domain (e.g. "Education (K-12)", "Formula 1 Racing")

CONCEPTS:
  - name: ""           # PascalCase business name
    type: ""           # Entity | Event | Metric | Dimension
    domain: ""         # which domain this belongs to
    description: ""    # 1 sentence in business terms
    learnedFrom: []    # table names or query references

RELATIONSHIPS:
  - subject: ""
    predicate: ""      # verb phrase: places, contains, generates...
    object: ""
    cardinality: ""    # one-to-many | many-to-many | etc.
    strength: ""       # Explicit (FK) | Inferred (JOIN) | Derived (query logic)

METRICS:
  - name: ""
    definition: ""     # what this measures
    computation: ""    # e.g. SUM(amount) WHERE status='complete'
    dimensions: []     # what you can slice by

BUSINESS_RULES:
  - rule: ""           # e.g. "Charter schools filtered by Y/N = 1"
    evidence: ""       # the WHERE/CASE/constraint that revealed it

GLOSSARY:
  - term: ""           # business term or abbreviation
    meaning: ""        # what it means
    evidence: ""       # column name, value, or alias

OPEN_THREADS:
  - question: ""       # concepts you suspect but haven't confirmed
    hint: ""

════════════════════════════════════════
RULES
════════════════════════════════════════

1. Never name a concept after a table. Always find the business name.
2. Never output a FK as-is. Convert to a named verb-phrase relationship.
3. Queries are your richest signal — a 4-table JOIN reveals a relationship chain.
4. Metrics are first-class concepts (Revenue, Rate, Count are graph nodes).
5. Flag uncertainty in OPEN_THREADS rather than guessing.
6. Output must be readable by a business analyst with no SQL knowledge.
"""

FIRST_CHUNK_USER_TEMPLATE = """\
This is CHUNK 1 of {total_chunks}. Build an initial draft of the knowledge \
graph from this data.

--- DATA START ---
{chunk_text}
--- DATA END ---
"""

NEXT_CHUNK_USER_TEMPLATE = """\
This is CHUNK {chunk_num} of {total_chunks}.

Process the new data below. Output ONLY the new or changed entries — the delta:
- New Concepts discovered in this chunk.
- New Relationships discovered.
- New Metrics, Business Rules, Glossary entries, or Domains.
- Updates to existing entries (mark with # UPDATED and include the full updated entry).
- Resolved OPEN_THREADS (mark with # RESOLVED).
- New OPEN_THREADS.

Do NOT repeat unchanged entries from previous chunks. Output ONLY what is new or changed.

--- DATA START ---
{chunk_text}
--- DATA END ---
"""

FINALIZE_USER_TEMPLATE = """\
All {total_chunks} chunks have been processed. Below is the accumulated \
knowledge graph built from all deltas:

{accumulated_state}

Now finalize:
1. Merge duplicate entries.
2. Resolve OPEN_THREADS where possible (remove resolved ones).
3. Finalize all Concept names in business language (no table names).
4. Promote Inferred relationships confirmed across multiple chunks to Explicit.
5. Remove database jargon.
6. Output the final clean, deduplicated knowledge graph.
"""

SUMMARIZE_SYSTEM_PROMPT = """\
You are a data-domain summarizer. Convert a Business Knowledge Graph into a \
compact structured Domain Summary.

Return a JSON object with these fields:
- domains: list of DISTINCT business domains found (specific and descriptive)
- core_entities: deduplicated primary business entities, ranked by importance
- core_metrics: deduplicated key metrics/KPIs with their definitions
- business_rules: list of implicit rules discovered (from BUSINESS_RULES section)
- glossary_hints: list of term mappings using "→" notation (from GLOSSARY section)
- dominant_join_patterns: most frequent relationship paths (from RELATIONSHIPS)

ALL fields MUST be populated. No empty lists.
"""

SUMMARIZE_USER_TEMPLATE = """\
Convert the following Business Knowledge Graph into a structured Domain Summary:

{knowledge_graph}
"""


def iterative_build(chunks: Sequence[Chunk]) -> str:
    """Process chunks sequentially, accumulating deltas into a knowledge graph.

    Uses multi-turn conversation so the model retains context across chunks.
    Each chunk returns only new/changed entries (delta). The full accumulated
    state is built in code and passed to the finalization step.
    """
    from langchain_core.messages import AIMessage

    total = len(chunks)
    messages: list = [SystemMessage(content=SYSTEM_PROMPT)]
    accumulated_state = ""

    for i, chunk in enumerate(chunks, 1):
        logger.info(
            "Processing chunk %d/%d (source=%s)...", i, total, chunk.source_type
        )

        if i == 1:
            user_content = FIRST_CHUNK_USER_TEMPLATE.format(
                total_chunks=total,
                chunk_text=chunk.text,
            )
        else:
            user_content = NEXT_CHUNK_USER_TEMPLATE.format(
                chunk_num=i,
                total_chunks=total,
                chunk_text=chunk.text,
            )

        messages.append(HumanMessage(content=user_content))
        response = invoke_text(messages, max_tokens=4096)

        if not response:
            logger.warning(
                "Chunk %d/%d returned empty — likely context overflow. "
                "Compacting conversation and retrying...",
                i,
                total,
            )
            messages = _compact_conversation(
                messages, accumulated_state, i, total, chunk
            )
            response = invoke_text(messages, max_tokens=4096)

        if response:
            messages.append(AIMessage(content=response))
            accumulated_state += f"\n\n# --- Chunk {i} ---\n{response}"
        else:
            logger.error("Chunk %d/%d: retry also empty, skipping.", i, total)
            messages.append(AIMessage(content="(no new entries)"))

        logger.info(
            "Chunk %d/%d complete (delta=%d chars, total=%d chars)",
            i,
            total,
            len(response),
            len(accumulated_state),
        )

    logger.info(
        "Running finalization pass (accumulated %d chars)...", len(accumulated_state)
    )
    finalize_messages = [
        SystemMessage(content=SYSTEM_PROMPT),
        HumanMessage(
            content=FINALIZE_USER_TEMPLATE.format(
                total_chunks=total,
                accumulated_state=accumulated_state,
            )
        ),
    ]
    final_graph = invoke_text(finalize_messages, max_tokens=4096)

    if not final_graph:
        logger.warning("Finalization returned empty — using accumulated state")
        final_graph = accumulated_state

    logger.info("Knowledge graph finalized (%d chars)", len(final_graph))
    return final_graph


def _compact_conversation(
    messages: list,
    accumulated_state: str,
    chunk_num: int,
    total: int,
    chunk: Chunk,
) -> list:
    """Compact conversation when context overflows.

    Keeps the system prompt and a summary of accumulated state,
    then re-frames with just the current chunk.
    """
    from langchain_core.messages import AIMessage

    system_msg = messages[0]
    compact_user = NEXT_CHUNK_USER_TEMPLATE.format(
        chunk_num=chunk_num,
        total_chunks=total,
        chunk_text=chunk.text,
    )
    return [
        system_msg,
        HumanMessage(content="Here is the knowledge graph built so far:"),
        AIMessage(content=accumulated_state),
        HumanMessage(content=compact_user),
    ]


def summarize_graph(knowledge_graph: str) -> DomainSummary:
    """Convert the final knowledge graph text into a structured DomainSummary."""
    from gsf.ontology.domain_prereading.llm import invoke_structured

    messages = [
        SystemMessage(content=SUMMARIZE_SYSTEM_PROMPT),
        HumanMessage(
            content=SUMMARIZE_USER_TEMPLATE.format(knowledge_graph=knowledge_graph)
        ),
    ]
    summary = invoke_structured(messages, DomainSummary, max_tokens=4096)
    logger.info("Summary produced: domains=%r", summary.domains)
    return summary
