"""Per-table taxonomy compilation: FK detection, Term + ColumnAttributes."""

from __future__ import annotations

import json
import logging
import os
import threading
from collections import defaultdict
from typing import TYPE_CHECKING, Any

from gsf.connectors import get_connectors
from gsf.dal.attributes import merge_column_attribute
from gsf.dal.datasources import (
    store_column_cardinality,
    store_column_date_formats,
    store_column_exhaustiveness,
    store_column_sample_values,
    store_column_uniqueness,
    store_table_row_count,
)
from gsf.dal.terms import fetch_terms_and_attributes_for_table, merge_term
from gsf.semantic.constants import MAX_SAMPLE_VALUE_LEN
from gsf.semantic.date_format import infer_date_format, is_date_type
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

if TYPE_CHECKING:
    from nemo_retriever.tabular_data.sql_database import SQLDatabase

logger = logging.getLogger(__name__)

# Row cap for the per-column profiling sample.
_PROFILING_SAMPLE_LIMIT = 1000
# Most-common values kept per column.
_PROFILING_TOP_N = 5
# Declared data-type substrings whose sample values are not persisted.
_EXCLUDED_SAMPLE_TYPES = ("date", "time", "timestamp", "datetime", "uuid")
# A text column with at most this many distinct values is treated as
# categorical: we capture its full distinct value set (via a DISTINCT probe)
# instead of only the most-common values from the first-N-row sample. This
# ensures rare-but-meaningful enum values (e.g. 'Banned', 'Restricted') land in
# the embedded description even when the dominant value fills the row prefix.
_LOW_CARDINALITY_MAX = 25
# Declared data-type substrings treated as free/categorical text.
_TEXT_SAMPLE_TYPES = ("char", "text", "string", "clob", "enum")
# Declared data-type substrings that store whole numbers.
_INTEGRAL_SAMPLE_TYPES = ("int", "serial")
# Declared data-type substrings that store numbers. Coded flags and status
# codes are routinely stored as INTEGER, so these columns are as categorical as
# any text enum and get the same low-cardinality treatment.
_NUMERIC_SAMPLE_TYPES = (
    "int",
    "serial",
    "real",
    "float",
    "double",
    "numeric",
    "decimal",
)

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


def _resolve_connector(database_name: str | None) -> "SQLDatabase | None":
    """Return the loaded connector whose ``database_name`` matches, or None."""
    if not database_name:
        return None
    key = database_name.casefold()
    for connector in get_connectors():
        db = getattr(connector, "database_name", None)
        if db is not None and db.casefold() == key:
            return connector
    return None


def _is_excluded_sample_type(data_type: str | None) -> bool:
    """Whether a column's declared type disqualifies it from sample storage."""
    if not data_type:
        return False
    lowered = data_type.lower()
    return any(token in lowered for token in _EXCLUDED_SAMPLE_TYPES)


def _is_text_sample_type(data_type: str | None) -> bool:
    """Whether a column's declared type is free/categorical text."""
    if not data_type:
        return False
    lowered = data_type.lower()
    return any(token in lowered for token in _TEXT_SAMPLE_TYPES)


def _is_numeric_sample_type(data_type: str | None) -> bool:
    """Whether a column's declared type stores numbers."""
    if not data_type:
        return False
    lowered = data_type.lower()
    return any(token in lowered for token in _NUMERIC_SAMPLE_TYPES)


def _is_integral_type(data_type: str | None) -> bool:
    """Whether a column's declared type stores whole numbers."""
    if not data_type:
        return False
    lowered = data_type.lower()
    return any(token in lowered for token in _INTEGRAL_SAMPLE_TYPES)


def _stringify(value: Any, integral: bool) -> str:
    """Render a profiled value, keeping integer columns free of a ``.0`` tail.

    pandas widens an integer column that contains NULLs to ``float64``, so a
    stored ``418`` arrives as ``418.0``. Sampling that verbatim would put a
    float literal in the generation prompt for a column whose values are
    integers, misrepresenting the type the model has to write predicates against.
    """
    if integral and isinstance(value, float) and value.is_integer():
        return str(int(value))
    return str(value)


def _sorted_numerically(values: list[str]) -> list[str]:
    """Sort stringified numbers by value, leaving the list alone if any is not one."""
    try:
        return sorted(values, key=float)
    except (TypeError, ValueError):
        return values


def _quote_identifier(name: str) -> str:
    """Double-quote a schema/table/column name for interpolation into SQL.

    Table names used to be interpolated raw, which made every profiling query
    against a table named after a reserved word — ``order``, ``group``,
    ``table`` — a syntax error, silently costing that table its sample values
    and uniqueness flags.
    """
    return '"' + str(name).replace('"', '""') + '"'


def _qualified_table(schema_name: str | None, table_name: str) -> str:
    """``"schema"."table"`` when a schema is known, else ``"table"``."""
    quoted_table = _quote_identifier(table_name)
    if not schema_name:
        return quoted_table
    return f"{_quote_identifier(schema_name)}.{quoted_table}"


def _distinct_values_if_low_cardinality(
    connector: "SQLDatabase",
    qualified: str,
    col_name: str,
    cap: int,
    integral: bool = False,
) -> list[str] | None:
    """Return the full distinct value set for a low-cardinality column.

    Runs ``SELECT DISTINCT <col> ... LIMIT cap + 1``. Returns the distinct
    values (as strings) when the column has at most *cap* distinct non-null
    values; returns ``None`` for high-cardinality columns (more than *cap*
    distinct values) or on any error, so the caller falls back to the
    most-common-values behaviour. The ``LIMIT`` keeps the probe cheap even on
    huge, high-cardinality columns (the scan stops after cap + 1 distinct rows).
    """
    quoted = _quote_identifier(col_name)
    try:
        df = connector.execute(
            f"SELECT DISTINCT {quoted} FROM {qualified} "
            f"WHERE {quoted} IS NOT NULL LIMIT {cap + 1}"
        )
    except Exception:
        # Not fatal — the caller falls back to most-common-values from the row
        # sample it already holds. Logged so a systematically failing probe is
        # discoverable rather than silently degrading every description.
        logger.debug(
            "distinct-value probe failed for %s.%s", qualified, col_name, exc_info=True
        )
        return None
    if df is None or df.empty:
        return None
    values = [_stringify(v, integral) for v in df.iloc[:, 0].tolist()]
    if len(values) > cap:
        return None
    return values


def exact_cardinality_enabled() -> bool:
    """Whether to probe every column's true distinct count.

    Off by default: it costs one ``COUNT(DISTINCT)`` per column over the whole
    column, which is cheap on the BIRD databases (~25s for 798 columns) but not
    on a large warehouse table. Read per call so a run can set it without regard
    to import order.
    """
    return (os.environ.get("SEMANTIC_EXACT_CARDINALITY") or "").strip().lower() in {
        "1",
        "true",
        "yes",
    }


def _table_row_count(connector: "SQLDatabase", qualified: str) -> int | None:
    """Total rows in a table, or ``None`` when the count cannot be taken."""
    try:
        df = connector.execute(f"SELECT COUNT(*) AS n FROM {qualified}")
    except Exception:
        logger.debug("row-count query failed for %s", qualified, exc_info=True)
        return None
    if df is None or df.empty:
        return None
    try:
        return int(df.iloc[0]["n"])
    except (KeyError, TypeError, ValueError):
        return None


def _exact_cardinality(
    connector: "SQLDatabase", qualified: str, col_name: str
) -> tuple[int, int] | None:
    """``(non_null_count, distinct_count)`` for a column, or ``None`` on failure.

    Unlike the row-sample profile, this sees the whole column, which is what
    makes it safe to tell the model a column is a key: of 148 columns the
    1000-row sample called unique, 25 were not unique over the full table — one
    of them an 11-value enum that happened to arrive distinct in the sample.
    """
    quoted = _quote_identifier(col_name)
    try:
        df = connector.execute(
            f"SELECT COUNT({quoted}) AS n, COUNT(DISTINCT {quoted}) AS nd "
            f"FROM {qualified}"
        )
    except Exception:
        logger.debug(
            "cardinality probe failed for %s.%s", qualified, col_name, exc_info=True
        )
        return None
    if df is None or df.empty:
        return None
    row = df.iloc[0]
    try:
        return int(row["n"]), int(row["nd"])
    except (KeyError, TypeError, ValueError):
        return None


def calculate_columns_profiling(
    table: dict[str, Any],
    columns: list[dict[str, Any]],
    connector: "SQLDatabase",
) -> dict[str, dict[str, Any]]:
    """Profile a table's columns from a live sample of up to 1000 rows.

    Runs ``SELECT * ... LIMIT 1000`` and, for every column, computes an
    ``is_unique`` flag (all non-null values distinct) and the 5 most-common
    values.

    Persists to Neo4j Column nodes: ``is_unique`` and ``exhaustive`` for every
    column, and ``sample_values`` for every column except those whose declared
    type is a date/time/uuid (individual values longer than
    ``MAX_SAMPLE_VALUE_LEN`` are dropped).

    Returns ``{column_name: {"sample_values": [...], "is_unique": bool,
    "exhaustive": bool}}`` for *all* columns (values unfiltered — includes dates,
    uuids and long strings). ``exhaustive`` is True when the values are the
    column's complete distinct set rather than its most common few.
    """
    schema_name = table.get("schema_name")
    table_name = table["name"]
    qualified = _qualified_table(schema_name, table_name)

    # Taken before the sample, so an empty table still records its size — that a
    # table holds nothing is exactly what stops the model building a query around
    # it. Cheap even on a large table: one COUNT(*), no per-column work, so it is
    # not gated behind the exact-cardinality flag.
    n_rows = _table_row_count(connector, qualified)
    if n_rows is not None:
        store_table_row_count(table["id"], n_rows)

    try:
        df = connector.execute(
            f"SELECT * FROM {qualified} LIMIT {_PROFILING_SAMPLE_LIMIT}"
        )
    except Exception:
        logger.warning(
            "[%s] column profiling query failed — skipping", table_name, exc_info=True
        )
        return {}

    if df is None or df.empty:
        return {}

    type_by_column = {
        col.get("name"): col.get("data_type") for col in columns if col.get("name")
    }

    profiling: dict[str, dict[str, Any]] = {}
    sample_values: dict[str, list] = {}
    uniqueness: dict[str, bool] = {}
    exhaustiveness: dict[str, bool] = {}
    cardinality: dict[str, int] = {}
    date_formats: dict[str, str] = {}
    exact = exact_cardinality_enabled()

    for column in df.columns:
        col_name = str(column)
        declared_type = type_by_column.get(col_name)
        integral = _is_integral_type(declared_type)
        try:
            # Cast to string first: some columns hold unhashable values (e.g.
            # Postgres array columns come back as Python lists, JSON/JSONB as
            # dict/list), and both is_unique and value_counts hash values.
            series = df[column].dropna().map(lambda v: _stringify(v, integral))

            is_unique = bool(len(series) > 0 and series.is_unique)
            top5 = list(series.value_counts().head(_PROFILING_TOP_N).index)
        except Exception:
            logger.warning(
                "[%s] profiling failed for column %r — skipping column",
                table_name,
                col_name,
                exc_info=True,
            )
            continue

        # A date column stores no sample values, so its notation is the only
        # thing that can tell the model what a predicate has to compare against.
        # Inferred from the whole sampled series rather than the five most
        # common values, since a day past the 12th may well be uncommon and it
        # is what rules out a month-first reading.
        date_format = infer_date_format(series) if is_date_type(declared_type) else None
        if date_format:
            date_formats[col_name] = date_format

        # An exact count supersedes the sampled flag rather than sitting beside
        # it: a description may only claim uniqueness it can prove. It is taken
        # before the probe below because it also decides whether to run it.
        n_distinct: int | None = None
        if exact:
            counts = _exact_cardinality(connector, qualified, col_name)
            if counts is not None:
                n_non_null, n_distinct = counts
                is_unique = n_non_null > 0 and n_distinct == n_non_null
                cardinality[col_name] = n_distinct

        # For categorical columns, prefer the full distinct value set over the
        # most-common values from the first-N-row sample. Rare enum values (e.g.
        # 'Banned', or a boolean flag whose 1s all sit past the sampled row
        # prefix) otherwise never make it into the embedded description.
        col_values = top5
        # A low-cardinality probe returns *every* distinct value, which makes the
        # list a closed enumeration rather than a handful of examples. Recording
        # which it is lets a description state "one of: Active, Closed" instead of
        # "samples: Active, Closed", turning a hint into a constraint.
        is_exhaustive = False
        categorical_candidate = _is_text_sample_type(
            declared_type
        ) or _is_numeric_sample_type(declared_type)
        # The true distinct count decides this when it is known. Sampled
        # uniqueness is a poor stand-in twice over: a column that is almost
        # entirely NULL within the sampled row prefix holds one or two values
        # there and is trivially "unique", which used to disqualify the very
        # columns whose domain the sample fails to show; and a small dimension
        # table's key genuinely is unique, yet its handful of values are exactly
        # the literals a WHERE clause has to match. Uniqueness only stands in
        # when no exact count was taken, preserving the old behaviour there.
        if n_distinct is not None:
            worth_probing = n_distinct <= _LOW_CARDINALITY_MAX
        else:
            worth_probing = not is_unique
        if worth_probing and categorical_candidate:
            distinct_vals = _distinct_values_if_low_cardinality(
                connector, qualified, col_name, _LOW_CARDINALITY_MAX, integral
            )
            if distinct_vals is not None:
                merged = list(top5)
                for value in distinct_vals:
                    if value not in merged:
                        merged.append(value)
                # Text keeps most-common-first ordering, which carries which
                # values dominate. A numeric enum reads as a range instead, so
                # "one of: 1, 2, ... 20" beats the frequency order.
                col_values = (
                    _sorted_numerically(merged)
                    if _is_numeric_sample_type(declared_type)
                    else merged
                )
                is_exhaustive = True

        uniqueness[col_name] = is_unique
        profiling[col_name] = {
            "sample_values": col_values,
            "is_unique": is_unique,
            "exhaustive": is_exhaustive,
            "n_distinct": n_distinct,
            "date_format": date_format,
        }

        if _is_excluded_sample_type(declared_type):
            exhaustiveness[col_name] = False
            continue
        filtered = [v for v in col_values if len(v) <= MAX_SAMPLE_VALUE_LEN]
        # Dropping an over-length value leaves the stored list incomplete, so the
        # persisted flag must stop claiming it is the column's full domain.
        exhaustiveness[col_name] = is_exhaustive and len(filtered) == len(col_values)
        if filtered:
            sample_values[col_name] = filtered

    table_id = table["id"]
    store_column_sample_values(table_id, sample_values)
    store_column_uniqueness(table_id, uniqueness)
    store_column_exhaustiveness(table_id, exhaustiveness)
    store_column_cardinality(table_id, cardinality)
    store_column_date_formats(table_id, date_formats)

    return profiling


def _profiling_from_columns(
    columns: list[dict[str, Any]],
) -> dict[str, dict[str, Any]]:
    """Rebuild the profiling dict from Column properties written at ingest.

    Returns ``{}`` when the table carries no ``exhaustive`` property at all,
    which means the graph predates it and the caller must profile live.

    The values read back are the *stored* ones, so they omit what
    ``calculate_columns_profiling`` declines to persist: date/time/uuid columns
    and individual values over ``MAX_SAMPLE_VALUE_LEN``.
    """
    if not any(col.get("exhaustive") is not None for col in columns):
        return {}
    profiling: dict[str, dict[str, Any]] = {}
    for col in columns:
        name = col.get("name")
        if not name:
            continue
        raw = col.get("sample_values")
        try:
            values = json.loads(raw) if isinstance(raw, str) else (raw or [])
        except ValueError:
            values = []
        n_distinct = col.get("n_distinct")
        profiling[name] = {
            "sample_values": [str(v) for v in values],
            "is_unique": bool(col.get("is_unique")),
            "exhaustive": bool(col.get("exhaustive")),
            # Absent on a graph ingested without the exact probe, which is what
            # keeps the description from claiming a cardinality it never measured.
            "n_distinct": int(n_distinct) if n_distinct is not None else None,
            "date_format": col.get("date_format"),
        }
    return profiling


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

    # Column profiles come from the Column nodes, where ingest already stored
    # them. Only a graph built before those properties existed needs the live
    # fallback, which costs a table scan plus a DISTINCT probe per categorical
    # column. Either way the result maps each column to {"sample_values": [...],
    # "is_unique": bool, "exhaustive": bool} for FK detection and descriptions.
    columns_profiling_samples = _profiling_from_columns(ctx.get("columns", []))
    if not columns_profiling_samples:
        connector = _resolve_connector(database_name)
        if connector is not None:
            try:
                columns_profiling_samples = calculate_columns_profiling(
                    table, ctx.get("columns", []), connector
                )
            except Exception:
                logger.warning(
                    "[%s] column profiling failed — continuing without it",
                    table_name,
                    exc_info=True,
                )

    # --- FK detection (LLM + declared); results not written to Neo4j ---
    declared_fks = ctx.get("fks", [])
    fk_suggestions = suggest_potential_foreign_keys(
        table, ctx, columns_profiling_samples
    )
    suggested_fk_names = {s.column_name for s in fk_suggestions.suggestions}
    declared_fk_names = {
        fk["source_column"] for fk in declared_fks if fk.get("source_column")
    }
    all_fk_names = declared_fk_names | suggested_fk_names

    # --- Build attribute specs for non-FK columns ---
    specs = column_attribute_specs(
        ctx.get("columns", []),
        declared_fks,
        suggested_fk_columns=all_fk_names,
        columns_profiling_samples=columns_profiling_samples,
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
                            logger.info(
                                "[%s] Merging proposed Term %r into existing %r",
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

        for term, assignments in persisted_terms:
            merge_term(term.name, term.description, table_id, synonyms=term.synonyms)
            result_term_names.append(term.name)
            for assignment in assignments:
                spec = spec_by_column[assignment.source_column]
                merge_column_attribute(
                    term_name=term.name,
                    table_id=table_id,
                    source_column=spec.source_column,
                    attr_name=spec.display_name,
                    datatype=spec.datatype,
                    description=spec.description,
                )
                result_attr_names.append(spec.display_name)

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
    # DISABLED for the BIRD ingest: `BIRD_PROMPT_SQL_ATTRS=0` already keeps these
    # out of the generation prompt, so proposing them costs one LLM call per term
    # for something the prompt never shows. Note this is not purely a cost saving:
    # retrieved SqlAttributes still contribute tables to `relevant_tables` in
    # candidates_preparation (fetch_tables_from_sql_attributes), so an empty set
    # also changes which tables retrieval offers. Uncomment to restore.
    result_sql_attr_names: list[str] = []
    # if database_name is not None and terms:
    #     try:
    #         col_by_name = {c["name"]: c for c in ctx.get("columns", [])}
    #         schema_name = table.get("schema_name")
    #
    #         for term in terms:
    #             term_id = term.get("id")
    #             term_name_str = term.get("name")
    #             term_col_names = [
    #                 a["source_column"]
    #                 for a in attrs_by_term.get(term_name_str, [])
    #                 if a.get("source_column")
    #             ]
    #             filtered_cols = [
    #                 col_by_name[n] for n in term_col_names if n in col_by_name
    #             ]
    #             if len(filtered_cols) < 2:
    #                 continue
    #             sql_names = _extract_sql_attributes_for_table(
    #                 table,
    #                 filtered_cols,
    #                 schema_name,
    #                 term_id,
    #                 term_name_str,
    #                 database_name,
    #             )
    #             result_sql_attr_names.extend(sql_names)
    #     except Exception:
    #         logger.warning(
    #             "[%s] SqlAttribute extraction failed", table_name, exc_info=True
    #         )

    return ProcessTableResult(
        term_names=result_term_names,
        attr_names=result_attr_names,
        sql_attr_names=result_sql_attr_names,
    )
