"""Main orchestration loop for the Rigor ontology pipeline.

Phase 1: Per-table iterative construction
  - Fetch table context from Neo4j
  - Enrich with BIRD evidence + value descriptions
  - Run deterministic detection (FK, *_id, self-ref, denormalized)
  - Query external vocabularies (LOV, BioPortal, FIBO)
  - Invoke Gen-LLM (Proposer) for DeltaOntology
  - Invoke Judge-LLM for validation
  - Merge validated Delta into CoreOntology

Phase 2: Behavioral analysis
  - Parse SQL queries for JOIN paths -> inferred edges
  - Extract aggregation patterns -> Metrics

Output: Write CoreOntology to Neo4j
"""

from __future__ import annotations

import logging
from typing import Any

from gsf.ontology.rigor.behavioral import analyze_sql_behavior
from gsf.ontology.rigor.deterministic import run_deterministic
from gsf.ontology.rigor.enricher import enrich_attributes
from gsf.ontology.rigor.external_vocab import ExternalVocabService
from gsf.ontology.rigor.judge import invoke_judge
from gsf.ontology.rigor.loaders import (
    enrich_context_with_bird,
    fetch_all_sql_texts,
    fetch_sorted_tables,
    fetch_table_context,
    load_evidence,
)
from gsf.ontology.rigor.models import (
    Attribute,
    BusinessTerm,
    CoreOntology,
    Provenance,
)
from gsf.ontology.rigor.neo4j_ops import write_ontology_to_neo4j
from gsf.ontology.rigor.proposer import invoke_proposer

logger = logging.getLogger(__name__)


def build_ontology(
    database_name: str,
    bird_root: str | None = None,
    skip_threshold: int = 0,
    write_to_neo4j: bool = True,
) -> CoreOntology:
    """Build a business ontology for a database.

    Args:
        database_name: Name of the database in Neo4j.
        bird_root: Path to BIRD minidev root (optional supplementary data).
        skip_threshold: Skip tables with fewer than this many SQL references.
        write_to_neo4j: Whether to write results to Neo4j.

    Returns:
        The constructed CoreOntology.
    """
    ontology = CoreOntology()

    # -----------------------------------------------------------------
    # INIT — Load data
    # -----------------------------------------------------------------
    logger.info("=" * 60)
    logger.info("Rigor: building ontology for %r", database_name)
    logger.info("=" * 60)

    tables = fetch_sorted_tables(database_name, skip_threshold)
    if not tables:
        logger.warning("No tables found for database %r", database_name)
        return ontology

    all_table_names = [t["name"] for t in tables]
    logger.info("Found %d tables: %s", len(tables), all_table_names)

    evidence: dict[str, list[str]] = {}
    if bird_root:
        evidence = load_evidence(database_name, bird_root)

    vocab_service = ExternalVocabService(db_id=database_name, evidence=evidence)

    # -----------------------------------------------------------------
    # PHASE 1 — Per-table iterative construction
    # -----------------------------------------------------------------
    logger.info("-" * 40)
    logger.info("PHASE 1: Per-table processing (%d tables)", len(tables))
    logger.info("-" * 40)

    for idx, table in enumerate(tables, 1):
        table_key = f"{table.get('schema_name', '')}.{table['name']}"
        logger.info(
            "\n[%d/%d] Processing %s (queries=%d)",
            idx,
            len(tables),
            table_key,
            table.get("query_count", 0),
        )

        # 1. Fetch context from Neo4j
        ctx = fetch_table_context(table["id"])
        columns = ctx.get("columns", [])
        if not columns:
            logger.warning("  Table %s has no columns — skipping.", table_key)
            continue

        # 2. Enrich with BIRD data
        if bird_root:
            enrich_context_with_bird(ctx, table["name"], evidence)

        # 3. Deterministic detection
        det_result = run_deterministic(table, ctx, all_table_names)

        # Add deterministic edges to ontology immediately
        for edge in det_result.edges:
            _ensure_term_exists(ontology, edge.source_term, table)
            _ensure_term_exists(ontology, edge.target_term, table)
            if not ontology.has_edge(edge.source_term, edge.target_term, edge.name):
                ontology.object_properties.append(edge)

        # 4. Collect evidence for this table (needed by enricher + proposer)
        evidence_for_table = vocab_service.get_evidence_for_table(
            table["name"],
            [c.get("name", "") for c in columns],
        )
        ctx["evidence"] = ctx.get("evidence", []) + [
            e for e in evidence_for_table if e not in ctx.get("evidence", [])
        ]
        ctx["table_name"] = table["name"]
        ctx["table_description"] = table.get("description") or ""

        # 5. Enrich attributes via LLM
        enriched_columns = enrich_attributes(
            det_result.attributes, ctx, ctx.get("evidence", [])
        )

        # Build lookup from source_column -> enriched info
        enriched_map = {ec.source_column: ec for ec in enriched_columns}

        # Merge enriched attributes into ontology
        term_name = (
            det_result.attributes[0].term_name if det_result.attributes else None
        )
        if term_name:
            _ensure_term_exists(ontology, term_name, table)
        for attr in det_result.attributes:
            ec = enriched_map.get(attr.source_column)
            ontology.attributes.append(
                Attribute(
                    name=ec.canonical_name if ec else attr.name,
                    datatype=attr.datatype,
                    term_name=attr.term_name,
                    source_column=attr.source_column,
                    provenance=Provenance(
                        source_table=table["name"],
                        source_column=attr.source_column,
                        derivation="deterministic",
                    ),
                    description=ec.description if ec else None,
                    formula=ec.formula if ec else None,
                    usage_hint=ec.usage_hint if ec else None,
                )
            )

        # 6. External vocabulary lookup
        ext_matches = vocab_service.find_similar_terms(table["name"], columns)

        # 7. Invoke Proposer (Gen-LLM)
        delta = invoke_proposer(
            table, ctx, det_result, ext_matches, ontology, enriched_columns
        )

        # 8. Invoke Judge (Judge-LLM)
        verdict = invoke_judge(delta, ontology)

        # 9. Apply verdict and merge into ontology
        validated_delta = verdict.apply(delta, table["name"])
        ontology.merge(validated_delta, table["name"])

        logger.info(
            "  -> Ontology now: %d terms, %d attrs, %d OPs",
            len(ontology.business_terms),
            len(ontology.attributes),
            len(ontology.object_properties),
        )

    # -----------------------------------------------------------------
    # PHASE 2 — Behavioral analysis (SQL queries)
    # -----------------------------------------------------------------
    logger.info("-" * 40)
    logger.info("PHASE 2: Behavioral analysis (SQL queries)")
    logger.info("-" * 40)

    sql_texts = fetch_all_sql_texts(database_name)
    all_evidence = evidence.get("all", [])

    join_edges, metrics = analyze_sql_behavior(sql_texts, ontology, all_evidence)
    ontology.object_properties.extend(join_edges)
    ontology.metrics.extend(metrics)

    logger.info(
        "Phase 2 added: %d join edges, %d metrics",
        len(join_edges),
        len(metrics),
    )

    # -----------------------------------------------------------------
    # WRITE — Save to Neo4j
    # -----------------------------------------------------------------
    if write_to_neo4j:
        logger.info("-" * 40)
        logger.info("Writing ontology to Neo4j")
        logger.info("-" * 40)
        stats = write_ontology_to_neo4j(ontology)
        logger.info("Write stats: %s", stats)

    # -----------------------------------------------------------------
    # Summary
    # -----------------------------------------------------------------
    logger.info("=" * 60)
    logger.info("Rigor pipeline complete for %r", database_name)
    logger.info(
        "  BusinessTerms: %d | Attributes: %d | ObjectProperties: %d | Metrics: %d",
        len(ontology.business_terms),
        len(ontology.attributes),
        len(ontology.object_properties),
        len(ontology.metrics),
    )
    logger.info("=" * 60)

    return ontology


def _ensure_term_exists(
    ontology: CoreOntology,
    term_name: str,
    table: dict[str, Any],
) -> None:
    """Create a placeholder business term if it doesn't exist yet.

    Deterministic edges may reference terms not yet proposed by
    the LLM. These placeholders will be enriched when the target
    table is processed.
    """
    if not ontology.has_term(term_name):
        ontology.business_terms.append(
            BusinessTerm(
                name=term_name,
                description=f"(auto-created from table {table['name']})",
                provenance=[
                    Provenance(
                        source_table=table["name"],
                        derivation="declared_fk",
                    )
                ],
            )
        )
