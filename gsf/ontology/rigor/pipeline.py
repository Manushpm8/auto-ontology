"""Main orchestration loop for semantic compilation.

Phase 0: Domain summary (optional, supplied or loaded by compile.py)
Phase 1: Seed table selection
Phase 2: BFS expansion via TablesQueue
Phase 3: Role synthesis with path attachment
Phase 4: Deterministic orphan fallback (after BFS)
Phase 5: Coverage sweep until all tables reviewed
Phase 6: Behavioral SQL analysis + write + embed (via compile.py)
"""

from __future__ import annotations

import json
import logging
import os
import re
from pathlib import Path
from typing import Any

from nemo_retriever.retriever import Retriever

from gsf.ontology.domain_prereading.models import DomainSummary
from gsf.ontology.rigor.behavioral import analyze_sql_behavior
from gsf.ontology.rigor.deterministic import run_deterministic, to_term_name
from gsf.ontology.rigor.enricher import enrich_attributes
from gsf.ontology.rigor.external_vocab import ExternalVocabService
from gsf.ontology.rigor.judge import invoke_judge
from gsf.ontology.rigor.loaders import (
    fetch_all_sql_texts,
    fetch_table_context,
)
from gsf.ontology.rigor.models import (
    Attribute,
    BusinessTerm,
    CoreOntology,
    ObjectProperty,
    Provenance,
)
from gsf.ontology.rigor.neo4j_ops import (
    lookup_existing_attribute,
    write_ontology_to_neo4j,
)
from gsf.ontology.rigor.paths import synthesize_role_edges
from gsf.ontology.rigor.proposer import invoke_proposer
from gsf.ontology.rigor.traversal import (
    TablesQueue,
    build_tables_index,
    clear_reviewed_flags,
    discover_tables_via_vdb,
    generate_business_questions,
    load_join_edges,
    mark_reviewed,
    select_seed_table,
)

logger = logging.getLogger(__name__)

_CHECKPOINT_DIR = Path(".rigor_checkpoints")
_INVALID_TERM_NAMES = {"unnamed", "unnamed term", "unknown", "none", ""}

_EMBED_ENDPOINT = os.environ.get(
    "EMBED_ENDPOINT", "https://integrate.api.nvidia.com/v1"
)
_EMBED_MODEL = os.environ.get("EMBED_MODEL", "nvidia/llama-nemotron-embed-1b-v2")
_NVIDIA_API_KEY = os.environ.get("NVIDIA_API_KEY", "")


def _checkpoint_path(database_name: str) -> Path:
    return _CHECKPOINT_DIR / f"{database_name}.json"


def _save_checkpoint(
    database_name: str,
    ontology: CoreOntology,
    completed_tables: list[str],
) -> None:
    _CHECKPOINT_DIR.mkdir(parents=True, exist_ok=True)
    path = _checkpoint_path(database_name)
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
) -> tuple[CoreOntology, list[str]] | None:
    path = _checkpoint_path(database_name)
    if not path.exists():
        return None
    try:
        data = json.loads(path.read_text())
        ontology = CoreOntology.model_validate(data["ontology"])
        completed = data["completed_tables"]
        logger.info(
            "[checkpoint] Resumed from %s — %d tables already done",
            path,
            len(completed),
        )
        return ontology, completed
    except Exception:
        logger.warning("[checkpoint] Failed to load %s", path, exc_info=True)
        return None


def _clear_checkpoint(database_name: str) -> None:
    path = _checkpoint_path(database_name)
    if path.exists():
        path.unlink()


def _build_data_retriever(database_name: str) -> Retriever | None:
    if not _NVIDIA_API_KEY:
        return None
    try:
        from gsf.vdb import get_data_vdb

        return Retriever(
            top_k=10,
            vdb_kwargs={"vdb": get_data_vdb(database_name=database_name)},
            embed_kwargs={
                "model_name": _EMBED_MODEL,
                "embed_invoke_url": _EMBED_ENDPOINT,
                "api_key": _NVIDIA_API_KEY,
            },
        )
    except Exception:
        logger.warning("Could not build data-layer retriever for VDB discovery")
        return None


def _pending_tables(
    tables: list[dict[str, Any]],
    completed_tables: list[str],
) -> list[dict[str, Any]]:
    """Tables not yet processed in this compilation run."""
    done = set(completed_tables)
    return [t for t in tables if t["name"] not in done]


def build_ontology(
    database_name: str,
    skip_threshold: int = 0,
    write_to_neo4j: bool = True,
    resume: bool = True,
    schema_name: str | None = None,
    domain_summary: DomainSummary | None = None,
) -> CoreOntology:
    """Build a semantic ontology for a database using BFS compilation."""
    if schema_name is None:
        schema_name = database_name

    completed_tables: list[str] = []
    ontology = CoreOntology()

    if resume:
        loaded = _load_checkpoint(database_name)
        if loaded:
            ontology, completed_tables = loaded
    else:
        _clear_checkpoint(database_name)

    logger.info("=" * 60)
    logger.info("Semantic compilation for %r (schema=%r)", database_name, schema_name)
    logger.info("=" * 60)

    tables, tables_by_name = build_tables_index(
        database_name, schema_name, skip_threshold
    )
    if not tables:
        logger.warning("No tables found for database %r", database_name)
        return ontology

    pending = _pending_tables(tables, completed_tables)
    if not pending and completed_tables:
        logger.info(
            "Checkpoint has all %d tables complete — nothing to process. "
            "Use --no-resume to rebuild.",
            len(completed_tables),
        )
        return ontology

    if not completed_tables or not resume:
        clear_reviewed_flags(database_name, schema_name)
        logger.info("Cleared reviewed flags — starting BFS from catalog")

    all_table_names = list(tables_by_name.keys())
    join_edges = load_join_edges(database_name, schema_name)
    retriever = _build_data_retriever(database_name)

    vocab_service = ExternalVocabService(db_id=database_name)

    bfs_trees = 0
    while True:
        pending = _pending_tables(tables, completed_tables)
        if not pending:
            logger.info("All %d tables processed — BFS complete", len(tables))
            break

        if bfs_trees == 0 and domain_summary:
            seed = select_seed_table(pending, domain_summary)
        else:
            seed = pending[0]

        logger.info("-" * 40)
        logger.info(
            "BFS tree %d: seed=%s (%d pending / %d total tables)",
            bfs_trees + 1,
            seed["name"],
            len(pending),
            len(tables),
        )
        logger.info("-" * 40)

        queue = TablesQueue(database_name, schema_name, tables_by_name, join_edges)
        queue.push_seed(seed["name"])

        while True:
            entry = queue.pop()
            if entry is None:
                break

            table = tables_by_name.get(entry.table_name)
            if not table or table["name"] in completed_tables:
                continue

            table_key = f"{table.get('schema_name', '')}.{table['name']}"
            logger.info(
                "Processing %s (priority=%d, source=%s, hop=%d)",
                table_key,
                entry.priority,
                entry.source,
                entry.hop,
            )

            try:
                vdb_names = _discover_vdb_neighbors(
                    table, retriever, all_table_names, ontology
                )
                _process_one_table(
                    table,
                    ontology,
                    all_table_names,
                    vocab_service,
                    domain_summary,
                )
                queue.discover_neighbors(
                    table["name"], entry.hop, vdb_table_names=vdb_names
                )
            except Exception:
                logger.error(
                    "  [FAILED] Table %s — skipping",
                    table_key,
                    exc_info=True,
                )

            mark_reviewed(table["id"])
            completed_tables.append(table["name"])
            if table["name"] not in ontology.reviewed_tables:
                ontology.reviewed_tables.append(table["name"])
            _save_checkpoint(database_name, ontology, completed_tables)

        synthesize_role_edges(ontology, database_name, schema_name)
        bfs_trees += 1

    logger.info("-" * 40)
    logger.info("Orphan sweep (deterministic fallback)")
    logger.info("-" * 40)
    orphan_sweep(
        ontology,
        database_name,
        schema_name,
        tables_by_name,
        completed_tables,
        join_edges,
    )
    _save_checkpoint(database_name, ontology, completed_tables)

    logger.info("-" * 40)
    logger.info("Behavioral analysis (SQL queries)")
    logger.info("-" * 40)

    sql_texts = fetch_all_sql_texts(database_name, schema_name=schema_name)
    join_edges_behavior, metrics = analyze_sql_behavior(sql_texts, ontology)
    ontology.object_properties.extend(join_edges_behavior)
    ontology.metrics.extend(metrics)

    if write_to_neo4j:
        logger.info("-" * 40)
        logger.info("Writing ontology to Neo4j")
        logger.info("-" * 40)
        stats = write_ontology_to_neo4j(ontology)
        logger.info("Write stats: %s", stats)

    _clear_checkpoint(database_name)

    logger.info("=" * 60)
    logger.info("Semantic compilation complete for %r", database_name)
    logger.info(
        "  Terms: %d | Attributes: %d | Relationships: %d | Metrics: %d",
        len(ontology.business_terms),
        len(ontology.attributes),
        len(ontology.object_properties),
        len(ontology.metrics),
    )
    logger.info("=" * 60)

    return ontology


def _discover_vdb_neighbors(
    table: dict[str, Any],
    retriever: Retriever | None,
    all_table_names: list[str],
    ontology: CoreOntology,
) -> list[str]:
    if retriever is None:
        return []
    term_name = ontology.table_to_term.get(table["name"], to_term_name(table["name"]))
    ctx = fetch_table_context(table["id"])
    questions = generate_business_questions(table, ctx, term_name)
    known = set(all_table_names)
    return discover_tables_via_vdb(
        questions.entities,
        table.get("schema_name", "") or "",
        retriever,
        known,
    )


def orphan_sweep(
    ontology: CoreOntology,
    database_name: str,
    schema_name: str,
    tables_by_name: dict[str, dict[str, Any]],
    completed_tables: list[str],
    join_edges: list[dict[str, Any]],
) -> int:
    """Phase 4: deterministically bind tables BFS did not map to a Term."""
    orphans = [
        t for t in tables_by_name.values() if t["name"] not in ontology.table_to_term
    ]
    bound = 0

    for orphan in orphans:
        orphan_name = orphan["name"]
        ctx = fetch_table_context(orphan["id"])
        neighbor_term: str | None = None
        neighbor_table: str | None = None

        for fk in ctx.get("fks", []):
            tgt = fk["target_table"]
            if tgt in ontology.table_to_term:
                neighbor_term = ontology.table_to_term[tgt]
                neighbor_table = tgt
                break

        if not neighbor_term:
            for edge in join_edges:
                if edge["source_table"] == orphan_name:
                    tgt = edge["target_table"]
                elif edge["target_table"] == orphan_name:
                    tgt = edge["source_table"]
                else:
                    continue
                if tgt in ontology.table_to_term:
                    neighbor_term = ontology.table_to_term[tgt]
                    neighbor_table = tgt
                    break

        orphan_term = to_term_name(orphan_name)
        if not ontology.has_term(orphan_term):
            ontology.business_terms.append(
                BusinessTerm(
                    name=orphan_term,
                    description=f"(orphan fallback from table {orphan_name})",
                    provenance=[
                        Provenance(
                            source_table=orphan_name,
                            derivation="orphan_fallback",
                        )
                    ],
                )
            )

        if neighbor_term:
            prov = Provenance(
                source_table=orphan_name,
                target_table=neighbor_table or "",
                derivation="orphan_fallback",
            )
            if not ontology.has_edge(orphan_term, neighbor_term, "part_of", "part_of"):
                ontology.object_properties.append(
                    ObjectProperty(
                        name="part_of",
                        source_term=orphan_term,
                        target_term=neighbor_term,
                        relation_kind="part_of",
                        provenance=prov,
                    )
                )
            term = ontology.get_term(orphan_term)
            if term:
                term.part_of = neighbor_term

        det = run_deterministic(orphan, ctx, list(tables_by_name.keys()))
        bind_term = neighbor_term or orphan_term
        for attr in det.attributes:
            if ontology.has_attribute(bind_term, attr.source_column):
                continue
            ontology.attributes.append(
                Attribute(
                    name=attr.name,
                    attribute_type="column",
                    datatype=attr.datatype,
                    term_name=bind_term,
                    source_column=attr.source_column,
                    provenance=Provenance(
                        source_table=orphan_name,
                        source_column=attr.source_column,
                        derivation="orphan_fallback",
                    ),
                )
            )

        ontology.table_to_term[orphan_name] = orphan_term
        mark_reviewed(orphan["id"])
        if orphan_name not in completed_tables:
            completed_tables.append(orphan_name)
        if orphan_name not in ontology.reviewed_tables:
            ontology.reviewed_tables.append(orphan_name)
        bound += 1
        logger.info(
            "  [orphan] Bound %s -> term %s (neighbor=%s)",
            orphan_name,
            bind_term,
            neighbor_term,
        )

    logger.info("Orphan sweep bound %d table(s)", bound)
    return bound


def _process_one_table(
    table: dict[str, Any],
    ontology: CoreOntology,
    all_table_names: list[str],
    vocab_service: ExternalVocabService,
    domain_summary: DomainSummary | None = None,
) -> None:
    """Run LLM term extraction then deterministic column attributes for one table."""
    table_key = f"{table.get('schema_name', '')}.{table['name']}"

    ctx = fetch_table_context(table["id"])
    columns = ctx.get("columns", [])
    if not columns:
        logger.warning("  Table %s has no columns — skipping.", table_key)
        return

    if domain_summary:
        ctx["domain_summary"] = domain_summary.model_dump()

    det_result = run_deterministic(table, ctx, all_table_names)

    ctx["table_name"] = table["name"]
    ctx["table_description"] = table.get("description") or ""

    # Enrich column metadata for proposer context only — do not write attributes yet.
    enriched_columns = enrich_attributes(det_result.attributes, ctx, [])

    ext_matches = vocab_service.find_similar_terms(table["name"], columns)

    delta = invoke_proposer(
        table,
        ctx,
        det_result,
        ext_matches,
        ontology,
        enriched_columns,
        domain_summary=domain_summary,
    )

    if len(delta.business_terms) > 1:
        delta.business_terms = delta.business_terms[:1]

    verdict = invoke_judge(delta, ontology)
    validated_delta = verdict.apply(delta, table["name"])

    provisional = to_term_name(table["name"])
    cleaned_terms = []
    for bt in validated_delta.business_terms:
        name_lower = bt.name.strip().lower()
        if name_lower in _INVALID_TERM_NAMES or not re.match(
            r"^[A-Z][A-Za-z0-9]+$", bt.name.strip()
        ):
            bt.name = provisional
        cleaned_terms.append(bt)
    validated_delta.business_terms = cleaned_terms

    ontology.merge(validated_delta, table["name"])

    if validated_delta.business_terms:
        proposed_name = validated_delta.business_terms[0].name
        if proposed_name != provisional and ontology.has_term(provisional):
            ontology.rename_term(provisional, proposed_name)
        ontology.table_to_term[table["name"]] = validated_delta.business_terms[0].name
    elif table["name"] not in ontology.table_to_term:
        ontology.table_to_term[table["name"]] = provisional
        if not ontology.has_term(provisional):
            ontology.business_terms.append(
                BusinessTerm(
                    name=provisional,
                    description=f"(from table {table['name']})",
                    provenance=[
                        Provenance(
                            source_table=table["name"],
                            derivation="deterministic",
                        )
                    ],
                )
            )

    term_name = ontology.table_to_term[table["name"]]
    enriched_map = {ec.source_column: ec for ec in enriched_columns}

    for edge in det_result.edges:
        src = ontology.resolve_term(edge.provenance.source_table or table["name"])
        tgt = ontology.resolve_term(edge.provenance.target_table or table["name"])
        edge.source_term = src
        edge.target_term = tgt
        if not ontology.has_edge(src, tgt, edge.name):
            ontology.object_properties.append(edge)

    for attr in det_result.attributes:
        canonical = enriched_map.get(attr.source_column)
        attr_name = canonical.canonical_name if canonical else attr.name
        if ontology.has_attribute(term_name, attr.source_column):
            continue
        existing = lookup_existing_attribute(attr_name, term_name)
        if existing:
            continue
        ontology.attributes.append(
            Attribute(
                name=attr_name,
                attribute_type="column",
                datatype=attr.datatype,
                term_name=term_name,
                source_column=attr.source_column,
                provenance=Provenance(
                    source_table=table["name"],
                    source_column=attr.source_column,
                    derivation="deterministic",
                ),
                description=canonical.description if canonical else None,
                formula=canonical.formula if canonical else None,
                usage_hint=canonical.usage_hint if canonical else None,
                definition=canonical.formula if canonical else None,
            )
        )
