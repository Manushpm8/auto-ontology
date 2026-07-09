"""Per-table taxonomy compilation: FK detection, Term + ColumnAttributes."""

from __future__ import annotations

import logging
import threading
from collections import defaultdict
from typing import Any

from gsf.dal.attributes import merge_column_attribute
from gsf.dal.terms import fetch_terms_and_attributes_for_table, merge_term
from gsf.semantic.constants import LABEL_TERM, REL_REPRESENTS, SEMANTIC_SOURCE
from gsf.semantic.deterministic import column_attribute_specs
from gsf.semantic.domain import DomainSummary
from gsf.semantic.embed import SemanticEmbedder
from gsf.semantic.fk_suggester import suggest_potential_foreign_keys
from gsf.semantic.models import ColumnAttributeSpec, ProcessTableResult
from gsf.semantic.sql_attribute_extractor import extract_sql_attributes
from gsf.semantic.term_extractor import apply_display_names_to_specs, extract_term
from gsf.server.sql_attributes.service import (
    SqlAttributeNameConflict,
    create_sql_attribute,
)

logger = logging.getLogger(__name__)


def _term_exists(term_name: str) -> dict | None:
    """Return the existing Term's schema context if it exists, else None."""
    from nemo_retriever.tabular_data.neo4j import get_neo4j_conn
    from nemo_retriever.tabular_data.ingestion.model.reserved_words import (
        Edges as NEdges,
        Labels as NLabels,
    )

    rows = get_neo4j_conn().query_read(
        f"MATCH (t:{NLabels.TABLE})-[:{REL_REPRESENTS}]->"
        f"(term:{LABEL_TERM} {{name: $name, source: $src}}) "
        f"OPTIONAL MATCH (t)<-[:{NEdges.CONTAINS}]-(sch:{NLabels.SCHEMA}) "
        f"RETURN term.id AS id, t.name AS table_name, "
        f"sch.name AS schema_name LIMIT 1",
        {"name": term_name, "src": SEMANTIC_SOURCE},
    )
    return dict(rows[0]) if rows else None


from gsf.semantic.constants import LABEL_COLUMN_ATTRIBUTE

def _existing_attr_names(term_name: str, exclude_table_id: str) -> dict[str, str]:
    """Return ``{attr_name: table_name}`` for attrs under *term_name* from other tables."""
    from nemo_retriever.tabular_data.neo4j import get_neo4j_conn
    from nemo_retriever.tabular_data.ingestion.model.reserved_words import (
        Labels as NLabels,
    )

    rows = get_neo4j_conn().query_read(
        f"MATCH (attr:{LABEL_COLUMN_ATTRIBUTE} "
        f"{{term_name: $tn, source: $src}}) "
        f"WHERE attr.table_id <> $tid "
        f"MATCH (t:{NLabels.TABLE} {{id: attr.table_id}}) "
        f"RETURN attr.name AS name, t.name AS table_name",
        {"tn": term_name, "src": SEMANTIC_SOURCE, "tid": exclude_table_id},
    )
    return {r["name"]: r["table_name"] for r in rows if r.get("name")}


def _rename_attr(term_name: str, old_name: str, new_name: str) -> None:
    """Rename an existing ColumnAttribute under *term_name*."""
    from nemo_retriever.tabular_data.neo4j import get_neo4j_conn

    get_neo4j_conn().query_write(
        f"MATCH (attr:{LABEL_COLUMN_ATTRIBUTE} "
        f"{{name: $old, term_name: $tn, source: $src}}) "
        f"SET attr.name = $new",
        {"old": old_name, "tn": term_name, "src": SEMANTIC_SOURCE, "new": new_name},
    )


_VARIANT_SUFFIXES = ("_archive", "_hist", "_history", "_staging", "_backup", "_old")

_VARIANT_LABELS = {
    "_archive": "Archived",
    "_hist": "Historical",
    "_history": "Historical",
    "_staging": "Staging",
    "_backup": "Backup",
    "_old": "Legacy",
}


def _tables_are_variants(a: str, b: str) -> bool:
    """True when one table name is a known variant of the other."""
    a_low, b_low = a.lower(), b.lower()
    if a_low == b_low:
        return False
    for suffix in _VARIANT_SUFFIXES:
        if a_low == b_low + suffix or b_low == a_low + suffix:
            return True
    return False


def _variant_label(variant_table: str, base_table: str) -> str:
    """Return a human-readable prefix like 'Archived' for the variant table."""
    v, b = variant_table.lower(), base_table.lower()
    for suffix, label in _VARIANT_LABELS.items():
        if v == b + suffix:
            return label
    return "Variant"


# Tables are processed in parallel (ThreadPoolExecutor in pipeline.py), but
# the commit phase must be serial: VDB search → judge → Neo4j merge → VDB embed.
# Without the lock, two threads could simultaneously propose the same Term,
# both find zero VDB hits (the first hasn't embedded yet), and create duplicates.
_term_commit_lock = threading.Lock()


def _terms_with_assignments(
    term_result: Any,
    spec_by_column: dict[str, ColumnAttributeSpec],
) -> list[tuple[Any, list[Any]]]:
    """Terms that have at least one resolvable column attribute."""
    persisted = []
    for term in term_result.terms:
        assignments = [a for a in term.attributes if a.source_column in spec_by_column]
        if assignments:
            persisted.append((term, assignments))
    return persisted


def _extract_sql_attributes_for_table(
    table: dict,
    columns: list[dict],
    schema_name: str | None,
    term_id: str,
    term_name: str,
    database_name: str,
) -> list[str]:
    """Run LLM extraction + persistence for one term's columns. Returns created attr names."""
    term = {
        "name": term_name,
        "description": table.get("description", ""),
    }

    proposals = extract_sql_attributes(table, columns, schema_name, term, database_name)
    created_names: list[str] = []

    for proposal in proposals:
        try:
            row = create_sql_attribute(
                name=proposal.name,
                description=proposal.description,
                expression=proposal.expression,
                term_id=term_id,
                connector=database_name,
                source="table",
            )
            created_names.append(row["name"])
            logger.info("  Created SqlAttribute %r", proposal.name)
        except SqlAttributeNameConflict:
            logger.debug("SqlAttribute %r already exists — skipping", proposal.name)
        except Exception:
            logger.warning(
                "  Failed to persist SqlAttribute %r",
                proposal.name,
                exc_info=True,
            )

    return created_names


def process_table(
    table: dict[str, Any],
    ctx: dict[str, Any],
    *,
    domain_summary: DomainSummary | None,
    embedder: SemanticEmbedder | None = None,
    database_name: str | None = None,
) -> ProcessTableResult:
    """Build taxonomy nodes for one table: Term and ColumnAttributes."""
    table_id = table["id"]
    table_name = table["name"]

    # --- FK detection (LLM + declared); results not written to Neo4j ---
    declared_fks = ctx.get("fks", [])
    fk_suggestions = suggest_potential_foreign_keys(table, ctx)
    suggested_fk_names = {s.column_name for s in fk_suggestions.suggestions}
    declared_fk_names = {
        fk["source_column"] for fk in declared_fks if fk.get("source_column")
    }
    all_fk_names = declared_fk_names | suggested_fk_names

    # --- Build attribute specs for non-FK columns ---
    specs = column_attribute_specs(
        ctx.get("columns", []),
        declared_fks,
        suggested_fk_columns=suggested_fk_names,
    )
    if not specs:
        logger.warning("[%s] no non-FK columns — skipping Term creation", table_name)
        return ProcessTableResult()

    # --- LLM: propose Term(s) and display names ---
    term_result = extract_term(table, ctx, specs, domain_summary=domain_summary)
    apply_display_names_to_specs(term_result, specs)
    spec_by_column = {spec.source_column: spec for spec in specs}
    persisted_terms = _terms_with_assignments(term_result, spec_by_column)

    if not persisted_terms:
        logger.warning(
            "[%s] LLM assigned no columns to any Term (%d candidates)",
            table_name,
            len(specs),
        )
        return ProcessTableResult()

    # Serialize: dedup check + Neo4j writes + VDB embed must be atomic
    # so the next thread's VDB search sees this thread's newly embedded terms.
    result_term_names: list[str] = []
    result_attr_names: list[str] = []
    terms: list = []
    attrs_by_term: dict[str, list[dict]] = defaultdict(list)

    with _term_commit_lock:
        if embedder is not None:
            for term, _ in persisted_terms:
                try:
                    candidates = embedder.search_similar_terms(
                        term.name, term.description
                    )
                    if candidates:
                        from gsf.semantic.term_judge import judge_term_overlap

                        merge_into = judge_term_overlap(
                            term.name, term.description, candidates
                        )
                        if merge_into:
                            target = _term_exists(merge_into)
                            target_table = (
                                target.get("table_name", "") if target else ""
                            )
                            if _tables_are_variants(table_name, target_table):
                                logger.info(
                                    "[%s] Skipping merge of %r into %r "
                                    "— tables are naming variants (%s / %s)",
                                    table_name,
                                    term.name,
                                    merge_into,
                                    table_name,
                                    target_table,
                                )
                            else:
                                logger.info(
                                    "[%s] Merging proposed Term %r "
                                    "into existing %r",
                                    table_name,
                                    term.name,
                                    merge_into,
                                )
                                term.name = merge_into
                except Exception:
                    logger.warning(
                        "[%s] Term dedup check failed for %r — proceeding as-is",
                        table_name,
                        term.name,
                        exc_info=True,
                    )

        schema_name = table.get("schema_name", "")

        for term, assignments in persisted_terms:
            existing = _term_exists(term.name)
            if existing:
                ex_table = existing.get("table_name", "")
                ex_schema = existing.get("schema_name", "")
                if (
                    ex_table == table_name
                    and ex_schema
                    and schema_name
                    and ex_schema != schema_name
                ):
                    qualified = f"{schema_name.title()} {term.name}"
                    logger.info(
                        "[%s] Term qualified as %r (same table in schema %s "
                        "already owns %r)",
                        table_name,
                        qualified,
                        ex_schema,
                        existing.get("id", "?"),
                    )
                    term.name = qualified
                elif _tables_are_variants(table_name, ex_table):
                    suffix = _variant_label(table_name, ex_table)
                    qualified = f"{suffix} {term.name}"
                    logger.info(
                        "[%s] Term qualified as %r (variant of %s)",
                        table_name,
                        qualified,
                        ex_table,
                    )
                    term.name = qualified

            term_id = merge_term(
                term.name, term.description, table_id, synonyms=term.synonyms
            )
            result_term_names.append(term.name)

            existing_attrs = _existing_attr_names(term.name, table_id)

            for assignment in assignments:
                spec = spec_by_column[assignment.source_column]
                attr_name = spec.display_name
                if attr_name in existing_attrs:
                    ex_table = existing_attrs[attr_name]
                    ex_qualified = f"{attr_name} ({ex_table})"
                    _rename_attr(term.name, attr_name, ex_qualified)
                    logger.info(
                        "[%s] Existing attribute %r renamed to %r",
                        table_name,
                        attr_name,
                        ex_qualified,
                    )
                    del existing_attrs[attr_name]
                    existing_attrs[ex_qualified] = ex_table

                    attr_name = f"{attr_name} ({table_name})"
                    logger.info(
                        "[%s] Incoming attribute %r qualified as %r",
                        table_name,
                        spec.display_name,
                        attr_name,
                    )
                existing_attrs[attr_name] = table_name

                merge_column_attribute(
                    term_name=term.name,
                    table_id=table_id,
                    source_column=spec.source_column,
                    attr_name=attr_name,
                    datatype=spec.datatype,
                    description=spec.description,
                )
                result_attr_names.append(attr_name)

        logger.info(
            "[%s] → Terms %s (%d attrs, %d suspected FKs)",
            table_name,
            result_term_names,
            len(result_attr_names),
            len(all_fk_names),
        )

        # Fetch persisted terms — used for embedding and SQL attribute extraction
        try:
            terms, attrs = fetch_terms_and_attributes_for_table(table_id)
            for attr in attrs:
                if attr.get("term_name"):
                    attrs_by_term[attr["term_name"]].append(attr)
        except Exception:
            logger.warning("[%s] failed to fetch persisted terms", table_name)

        if embedder is not None and terms:
            try:
                for term in terms:
                    embedder.embed_term(term, attrs_by_term.get(term["name"], []))
            except Exception:
                logger.warning("[%s] inline embed failed", table_name)

    # --- LLM: propose SqlAttributes (outside the lock — Term writes are complete) ---
    result_sql_attr_names: list[str] = []
    if database_name is not None and terms:
        try:
            col_by_name = {c["name"]: c for c in ctx.get("columns", [])}
            schema_name = table.get("schema_name")

            for term in terms:
                term_id = term.get("id")
                term_name_str = term.get("name")
                term_col_names = [
                    a["source_column"]
                    for a in attrs_by_term.get(term_name_str, [])
                    if a.get("source_column")
                ]
                filtered_cols = [
                    col_by_name[n] for n in term_col_names if n in col_by_name
                ]
                if len(filtered_cols) < 2:
                    continue
                sql_names = _extract_sql_attributes_for_table(
                    table,
                    filtered_cols,
                    schema_name,
                    term_id,
                    term_name_str,
                    database_name,
                )
                result_sql_attr_names.extend(sql_names)
        except Exception:
            logger.warning(
                "[%s] SqlAttribute extraction failed", table_name, exc_info=True
            )

    return ProcessTableResult(
        term_names=result_term_names,
        attr_names=result_attr_names,
        sql_attr_names=result_sql_attr_names,
    )
