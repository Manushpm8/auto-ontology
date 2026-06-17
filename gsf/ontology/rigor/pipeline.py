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

Resilience:
  - Checkpoints after each table so progress survives crashes
  - LLM calls have timeouts + retries (see llm.py)
  - Individual table failures are caught and skipped

Output: Write CoreOntology to Neo4j
"""

from __future__ import annotations

import json
import logging
import re
from pathlib import Path
from typing import Any

from gsf.ontology.rigor.behavioral import analyze_sql_behavior
from gsf.ontology.rigor.deterministic import run_deterministic, to_term_name
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
from nemo_retriever.tabular_data.ingestion.model.reserved_words import (
    Edges,
    Labels,
)
from nemo_retriever.tabular_data.neo4j import get_neo4j_conn

logger = logging.getLogger(__name__)

_CHECKPOINT_DIR = Path(".rigor_checkpoints")
_INVALID_TERM_NAMES = {"unnamed", "unnamed term", "unknown", "none", ""}

_RESOLVE_PK_FROM_TABLE = f"""
MATCH (:{Labels.SCHEMA} {{name: $schema_name}})-[:{Edges.CONTAINS}]->
      (t:{Labels.TABLE} {{name: $table_name}})
WHERE t.pk IS NOT NULL
RETURN t.pk[0] AS pk_col
"""

_RESOLVE_PK_FALLBACK = f"""
MATCH (:{Labels.SCHEMA} {{name: $schema_name}})-[:{Edges.CONTAINS}]->
      (t:{Labels.TABLE} {{name: $table_name}})-[:{Edges.CONTAINS}]->(c:{Labels.COLUMN})
WHERE toLower(c.name) = 'id'
RETURN c.name AS pk_col LIMIT 1
"""


def _make_resolve_target_pk(schema_name: str) -> Any:
    """Build a callback that resolves the PK column of a target table.

    Strategy:
    1. Check ``Table.pk`` property (set during schema ingestion).
    2. Fall back to a Column node named ``id``.
    3. Return ``None`` if neither found — caller should skip the edge.
    """
    conn = get_neo4j_conn()

    def resolve(table_name: str) -> str | None:
        params = {"table_name": table_name, "schema_name": schema_name}
        rows = conn.query_read(_RESOLVE_PK_FROM_TABLE, params)
        if rows and rows[0].get("pk_col"):
            return rows[0]["pk_col"]
        rows = conn.query_read(_RESOLVE_PK_FALLBACK, params)
        if rows and rows[0].get("pk_col"):
            return rows[0]["pk_col"]
        return None

    return resolve


# -----------------------------------------------------------------
# Checkpoint helpers — survive crashes without losing progress
# -----------------------------------------------------------------


def _checkpoint_path(database_name: str, schema_name: str) -> Path:
    """Return the checkpoint file path for a database/schema run."""
    return _CHECKPOINT_DIR / f"{database_name}__{schema_name}.json"


def _save_checkpoint(
    database_name: str,
    schema_name: str,
    ontology: CoreOntology,
    completed_tables: list[str],
) -> None:
    """Persist ontology + list of finished tables to disk."""
    _CHECKPOINT_DIR.mkdir(parents=True, exist_ok=True)
    path = _checkpoint_path(database_name, schema_name)
    payload = {
        "completed_tables": completed_tables,
        "ontology": ontology.model_dump(mode="json"),
    }
    path.write_text(json.dumps(payload, indent=2))
    logger.info(
        "  [checkpoint] Saved → %s (%d tables done)",
        path,
        len(completed_tables),
    )


def _load_checkpoint(
    database_name: str,
    schema_name: str,
) -> tuple[CoreOntology, list[str]] | None:
    """Load a previous checkpoint if one exists.

    Returns (ontology, completed_tables) or None.
    """
    path = _checkpoint_path(database_name, schema_name)
    if not path.exists():
        return None
    try:
        data = json.loads(path.read_text())
        if data.get("status") == "completed":
            logger.info(
                "[checkpoint] Schema %s.%s already completed — skipping",
                database_name,
                schema_name,
            )
            return "completed"  # type: ignore[return-value]
        ontology = CoreOntology.model_validate(data["ontology"])
        completed = data["completed_tables"]
        logger.info(
            "[checkpoint] Resumed from %s — %d tables already done, "
            "%d terms, %d attrs, %d OPs loaded",
            path,
            len(completed),
            len(ontology.business_terms),
            len(ontology.attributes),
            len(ontology.object_properties),
        )
        return ontology, completed
    except Exception:
        logger.warning(
            "[checkpoint] Failed to load %s — starting fresh",
            path,
            exc_info=True,
        )
        return None


def _mark_checkpoint_complete(database_name: str, schema_name: str) -> None:
    """Replace checkpoint with a small completion marker."""
    _CHECKPOINT_DIR.mkdir(parents=True, exist_ok=True)
    path = _checkpoint_path(database_name, schema_name)
    path.write_text(json.dumps({"status": "completed"}))
    logger.info("[checkpoint] Marked complete → %s", path)


# -----------------------------------------------------------------
# Main pipeline
# -----------------------------------------------------------------


def build_ontology(
    database_name: str,
    bird_root: str | None = None,
    skip_threshold: int = 0,
    write_to_neo4j: bool = True,
    resume: bool = True,
    schema_name: str | None = None,
) -> CoreOntology | None:
    """Build a business ontology for a database.

    Args:
        database_name: Name of the database in Neo4j.
        bird_root: Path to BIRD minidev root (optional supplementary data).
        skip_threshold: Skip tables with fewer than this many SQL references.
        write_to_neo4j: Whether to write results to Neo4j.
        resume: If True, resume from the last checkpoint (if any).
        schema_name: Neo4j Schema node name (e.g. ``"public"``).
            Defaults to *database_name* for BIRD compatibility.

    Returns:
        The constructed CoreOntology, or None if already completed.
    """
    if schema_name is None:
        schema_name = database_name
    # Try to resume from a previous checkpoint
    completed_tables: list[str] = []
    ontology = CoreOntology()
    if resume:
        loaded = _load_checkpoint(database_name, schema_name)
        if loaded == "completed":
            return None
        if loaded:
            ontology, completed_tables = loaded

    # -----------------------------------------------------------------
    # INIT — Load data
    # -----------------------------------------------------------------
    logger.info("=" * 60)
    logger.info("Rigor: building ontology for %r", database_name)
    logger.info("=" * 60)

    tables = fetch_sorted_tables(database_name, skip_threshold, schema_name=schema_name)
    if not tables:
        logger.warning("No tables found for database %r", database_name)
        return ontology

    all_table_names = [t["name"] for t in tables]
    logger.info("Found %d tables: %s", len(tables), all_table_names)

    evidence: dict[str, list[str]] = {}
    if bird_root:
        evidence = load_evidence(database_name, bird_root)

    vocab_service = ExternalVocabService(db_id=database_name, evidence=evidence)
    resolve_target_pk = _make_resolve_target_pk(schema_name)

    # -----------------------------------------------------------------
    # PHASE 1 — Per-table iterative construction
    # -----------------------------------------------------------------
    logger.info("-" * 40)
    logger.info("PHASE 1: Per-table processing (%d tables)", len(tables))
    logger.info("-" * 40)

    for idx, table in enumerate(tables, 1):
        table_key = f"{table.get('schema_name', '')}.{table['name']}"

        # Skip tables already completed in a previous run
        if table["name"] in completed_tables:
            logger.info(
                "\n[%d/%d] Skipping %s (already completed in checkpoint)",
                idx,
                len(tables),
                table_key,
            )
            continue

        logger.info(
            "\n[%d/%d] Processing %s (queries=%d)",
            idx,
            len(tables),
            table_key,
            table.get("query_count", 0),
        )

        try:
            _process_one_table(
                table,
                ontology,
                all_table_names,
                bird_root,
                evidence,
                vocab_service,
                resolve_target_pk,
            )
        except Exception:
            logger.error(
                "  [FAILED] Table %s — skipping (deterministic work preserved)",
                table_key,
                exc_info=True,
            )

        # Checkpoint after every table (even failed ones, so we don't retry)
        completed_tables.append(table["name"])
        _save_checkpoint(database_name, schema_name, ontology, completed_tables)

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

    sql_texts = fetch_all_sql_texts(database_name, schema_name=schema_name)
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
        stats = write_ontology_to_neo4j(
            ontology, schema_name=schema_name, database_name=database_name
        )
        logger.info("Write stats: %s", stats)

    # -----------------------------------------------------------------
    # Summary
    # -----------------------------------------------------------------
    _mark_checkpoint_complete(database_name, schema_name)

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


# -----------------------------------------------------------------
# Per-table processing — extracted so failures can be caught
# -----------------------------------------------------------------


def _process_one_table(
    table: dict[str, Any],
    ontology: CoreOntology,
    all_table_names: list[str],
    bird_root: str | None,
    evidence: dict[str, list[str]],
    vocab_service: ExternalVocabService,
    resolve_target_pk: Any = None,
) -> None:
    """Run deterministic + LLM steps for a single table.

    Raises on LLM timeout/error so the caller can catch and skip.
    """
    table_key = f"{table.get('schema_name', '')}.{table['name']}"

    # 1. Fetch context from Neo4j
    ctx = fetch_table_context(table["id"])
    columns = ctx.get("columns", [])
    fks = ctx.get("fks", [])
    if not columns:
        logger.warning("  Table %s has no columns — skipping.", table_key)
        return

    logger.info("  %d columns, %d FKs from graph", len(columns), len(fks))
    for fk in fks:
        logger.info(
            "    FK: %s -> %s.%s",
            fk["source_column"],
            fk["target_table"],
            fk["target_column"],
        )

    # 2. Enrich with BIRD data
    if bird_root:
        enrich_context_with_bird(ctx, table["name"], evidence)

    # 3. Deterministic detection
    det_result = run_deterministic(table, ctx, all_table_names, resolve_target_pk)

    # Add deterministic edges to ontology immediately, resolving term names
    # via table_to_term so we don't recreate renamed placeholders.
    for edge in det_result.edges:
        src_table = edge.provenance.source_table or table["name"]
        tgt_table = edge.provenance.target_table or table["name"]
        src = ontology.resolve_term(src_table)
        tgt = ontology.resolve_term(tgt_table)
        edge.source_term = src
        edge.target_term = tgt
        _ensure_term_exists(ontology, src, src_table)
        _ensure_term_exists(ontology, tgt, tgt_table)
        if not ontology.has_edge(src, tgt, edge.name):
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

    # 5. Enrich attributes via LLM (may raise on timeout)
    enriched_columns = enrich_attributes(
        det_result.attributes, ctx, ctx.get("evidence", [])
    )

    # Build lookup from source_column -> enriched info
    enriched_map = {ec.source_column: ec for ec in enriched_columns}
    col_map = {c["name"]: c for c in columns}

    # Merge enriched attributes into ontology
    term_name = det_result.attributes[0].term_name if det_result.attributes else None
    if term_name:
        _ensure_term_exists(
            ontology,
            term_name,
            table["name"],
            table_description=table.get("description") or "",
        )
        ontology.table_to_term[table["name"]] = term_name
    for attr in det_result.attributes:
        ec = enriched_map.get(attr.source_column)
        neo4j_col = col_map.get(attr.source_column, {})
        attr_desc = (
            ec.description if ec and ec.description else None
        ) or neo4j_col.get("description")
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
                description=attr_desc,
                formula=ec.formula if ec else None,
                usage_hint=ec.usage_hint if ec else None,
                is_primary_key=attr.is_primary_key,
            )
        )

    # 6. External vocabulary lookup
    ext_matches = vocab_service.find_similar_terms(table["name"], columns)

    # 7. Invoke Proposer (Gen-LLM) — may raise on timeout
    delta = invoke_proposer(
        table, ctx, det_result, ext_matches, ontology, enriched_columns
    )

    # Keep only the first BusinessTerm (prompt asks for exactly one)
    if len(delta.business_terms) > 1:
        logger.warning(
            "  [proposer] Returned %d terms, keeping only first: %s",
            len(delta.business_terms),
            delta.business_terms[0].name,
        )
        delta.business_terms = delta.business_terms[:1]

    # 8. Invoke Judge (Judge-LLM) — may raise on timeout
    verdict = invoke_judge(delta, ontology)

    # 9. Apply verdict and merge into ontology
    validated_delta = verdict.apply(delta, table["name"])

    # Reject invalid term names from the LLM
    provisional = to_term_name(table["name"])
    cleaned_terms = []
    for bt in validated_delta.business_terms:
        name_lower = bt.name.strip().lower()
        if name_lower in _INVALID_TERM_NAMES or not re.match(
            r"^[A-Z][A-Za-z0-9]+$", bt.name.strip()
        ):
            logger.warning(
                "  [guard] Rejected invalid term name %r, using %s",
                bt.name,
                provisional,
            )
            bt.name = provisional
        cleaned_terms.append(bt)
    validated_delta.business_terms = cleaned_terms

    ontology.merge(
        validated_delta,
        source_table=table["name"],
        source_schema=table.get("schema_name", ""),
    )

    # 10. Rename provisional term if the LLM chose a better name
    if validated_delta.business_terms:
        proposed_name = validated_delta.business_terms[0].name
        if proposed_name != provisional and ontology.has_term(provisional):
            refs = ontology.rename_term(provisional, proposed_name)
            logger.info(
                "  [rename] %s -> %s (%d references updated)",
                provisional,
                proposed_name,
                refs,
            )


def _ensure_term_exists(
    ontology: CoreOntology,
    term_name: str,
    source_table: str,
    table_description: str = "",
) -> None:
    """Create a placeholder business term if it doesn't exist yet.

    *source_table* should be the table this term represents (e.g. for
    a FK edge ``request_tasks.user_id -> users``, the *target* term's
    source_table is ``"users"``, NOT ``"request_tasks"``).

    *table_description* is used as the initial description when available,
    so the term carries meaningful context even before the LLM runs.
    """
    existing = ontology.get_term(term_name)
    if existing:
        if table_description and (
            not existing.description
            or existing.description.startswith("(auto-created")
            or len(table_description) > len(existing.description)
        ):
            existing.description = table_description
    else:
        desc = table_description or f"(auto-created from table {source_table})"
        ontology.business_terms.append(
            BusinessTerm(
                name=term_name,
                description=desc,
                provenance=[
                    Provenance(
                        source_table=source_table,
                        derivation="declared_fk",
                    )
                ],
            )
        )
