# SPDX-FileCopyrightText: Copyright (c) 2024-2026, NVIDIA CORPORATION & AFFILIATES.
# All rights reserved.
# SPDX-License-Identifier: Apache-2.0

"""MAP + REDUCE for the domain pre-reading pipeline."""

from __future__ import annotations

import logging
from concurrent.futures import ThreadPoolExecutor, as_completed
from typing import Sequence

from langchain_core.messages import HumanMessage, SystemMessage

from gsf.ontology.domain_prereading.chunker import Chunk
from gsf.ontology.domain_prereading.llm import invoke_structured
from gsf.ontology.domain_prereading.models import ChunkSignals, DomainSummary

logger = logging.getLogger(__name__)

MAP_CONCURRENCY = 8

MAP_SYSTEM_PROMPT = """\
You are a data-domain analyst. Given a chunk of database schema definitions \
and/or SQL queries, identify the business domain concepts present.

Return a structured response with:
- domain_areas: high-level business domains (e.g. "e-commerce", "finance")
- key_entities: primary business objects (e.g. "Customer", "Order")
- key_metrics: measurable KPIs (e.g. "Revenue", "Churn Rate")
- business_rules: implicit filters or constraints seen in queries
- glossary_hints: column/alias to business term mappings (use "→" notation)
- join_patterns: frequent table relationships (use "→" notation)
"""

MAP_USER_TEMPLATE = """\
What business domain concepts, entities, and metrics appear in this chunk?
List: domain area, key entities, key metrics, business rules you see.

--- CHUNK START ---
{chunk_text}
--- CHUNK END ---
"""

REDUCE_SYSTEM_PROMPT = """\
You are a data-domain summarizer. You are given multiple signal extractions \
from chunks of a database corpus (schema + queries). Merge them into one \
coherent Domain Summary.

Return a structured response with:
- domain: single high-level domain classification
- core_entities: deduplicated list of primary business entities, ranked by frequency
- core_metrics: deduplicated list of key metrics/KPIs
- business_rules: merged list of implicit rules discovered
- glossary_hints: merged column/alias to business term mappings (use "→" notation)
- dominant_join_patterns: most frequent table join paths with approx. percentage

Deduplicate, merge synonyms, and rank by frequency/importance. Keep the \
output concise (under 1500 tokens).
"""


def _map_chunk(chunk: Chunk) -> ChunkSignals:
    """MAP phase: extract signals from a single chunk."""
    messages = [
        SystemMessage(content=MAP_SYSTEM_PROMPT),
        HumanMessage(content=MAP_USER_TEMPLATE.format(chunk_text=chunk.text)),
    ]
    try:
        return invoke_structured(messages, ChunkSignals)
    except Exception:
        logger.exception("MAP failed for chunk (tables=%s)", chunk.tables)
        return ChunkSignals()


def map_phase(chunks: Sequence[Chunk]) -> list[ChunkSignals]:
    """Run MAP in parallel across all chunks with bounded concurrency."""
    results: list[ChunkSignals] = [ChunkSignals()] * len(chunks)

    with ThreadPoolExecutor(max_workers=MAP_CONCURRENCY) as pool:
        future_to_idx = {
            pool.submit(_map_chunk, chunk): i for i, chunk in enumerate(chunks)
        }
        for future in as_completed(future_to_idx):
            idx = future_to_idx[future]
            results[idx] = future.result()

    succeeded = sum(
        1 for r in results if r.key_entities or r.domain_areas
    )
    logger.info("MAP phase complete: %d/%d chunks produced signals", succeeded, len(chunks))
    return results


def _merge_signals_text(signals: list[ChunkSignals]) -> str:
    """Flatten all chunk signals into a single text block for the REDUCE call."""
    sections: list[str] = []
    for i, sig in enumerate(signals, 1):
        lines = [f"--- Chunk {i} ---"]
        if sig.domain_areas:
            lines.append(f"Domain areas: {', '.join(sig.domain_areas)}")
        if sig.key_entities:
            lines.append(f"Entities: {', '.join(sig.key_entities)}")
        if sig.key_metrics:
            lines.append(f"Metrics: {', '.join(sig.key_metrics)}")
        if sig.business_rules:
            lines.append(f"Rules: {'; '.join(sig.business_rules)}")
        if sig.glossary_hints:
            lines.append(f"Glossary: {'; '.join(sig.glossary_hints)}")
        if sig.join_patterns:
            lines.append(f"Joins: {', '.join(sig.join_patterns)}")
        sections.append("\n".join(lines))
    return "\n\n".join(sections)


def reduce_phase(signals: list[ChunkSignals]) -> DomainSummary:
    """REDUCE phase: merge all chunk signals into a single DomainSummary."""
    merged_text = _merge_signals_text(signals)
    messages = [
        SystemMessage(content=REDUCE_SYSTEM_PROMPT),
        HumanMessage(
            content=(
                "Merge the following per-chunk signal extractions into one "
                "coherent Domain Summary:\n\n" + merged_text
            ),
        ),
    ]
    summary = invoke_structured(messages, DomainSummary, max_tokens=3000)
    logger.info("REDUCE phase complete: domain=%r", summary.domain)
    return summary
