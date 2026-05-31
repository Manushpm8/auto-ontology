"""Phase 2 — Behavioral analysis of SQL query patterns.

Parses SQL queries with sqlglot to extract:
1. JOIN paths -> inferred ObjectProperty edges
2. Aggregation patterns (SUM, COUNT, AVG, etc.) -> Metric objects
"""

from __future__ import annotations

import logging
import re
from typing import Any

import sqlglot
from sqlglot import exp

from gsf.ontology.rigor.models import (
    AggregationType,
    CoreOntology,
    Metric,
    ObjectProperty,
    Provenance,
)

logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# JOIN analysis
# ---------------------------------------------------------------------------


def _extract_join_pairs(sql_text: str) -> list[tuple[str, str]]:
    """Parse a SQL query and extract (source_table, target_table) JOIN pairs."""
    pairs: list[tuple[str, str]] = []
    try:
        parsed = sqlglot.parse_one(sql_text, dialect="postgres")
    except sqlglot.errors.ParseError:
        logger.debug("Failed to parse SQL: %.100s", sql_text)
        return pairs

    for join in parsed.find_all(exp.Join):
        join_table = join.find(exp.Table)
        if join_table is None:
            continue
        target = join_table.name

        parent = join.parent
        if parent is None:
            continue
        from_table = parent.find(exp.From)
        if from_table is None:
            continue
        source_tbl = from_table.find(exp.Table)
        if source_tbl is None:
            continue
        source = source_tbl.name

        if source and target and source != target:
            pairs.append((source, target))

    return pairs


def _to_term_name(table_name: str) -> str:
    """Convert table name to CamelCase business term name."""
    parts = re.split(r"[_\s]+", table_name)
    return "".join(p.capitalize() for p in parts if p)


def extract_join_edges(
    sql_texts: list[dict[str, Any]],
    ontology: CoreOntology,
) -> list[ObjectProperty]:
    """Extract ObjectProperty edges from JOIN patterns in SQL queries.

    Only creates edges not already in the ontology.
    """
    seen_pairs: set[tuple[str, str]] = set()
    new_edges: list[ObjectProperty] = []

    for item in sql_texts:
        sql = item.get("sql_text", "")
        if not sql:
            continue

        pairs = _extract_join_pairs(sql)
        for source, target in pairs:
            src_term = _to_term_name(source)
            tgt_term = _to_term_name(target)

            pair_key = (src_term, tgt_term)
            reverse_key = (tgt_term, src_term)

            if pair_key in seen_pairs or reverse_key in seen_pairs:
                continue
            if ontology.has_edge(src_term, tgt_term):
                continue
            if ontology.has_edge(tgt_term, src_term):
                continue

            seen_pairs.add(pair_key)
            new_edges.append(
                ObjectProperty(
                    name=f"joinedWith{tgt_term}",
                    source_term=src_term,
                    target_term=tgt_term,
                    provenance=Provenance(
                        source_table=source,
                        derivation="sql_join_inferred",
                    ),
                )
            )

    logger.info(
        "[phase2] Extracted %d new JOIN-inferred edges from %d queries",
        len(new_edges),
        len(sql_texts),
    )
    return new_edges


# ---------------------------------------------------------------------------
# Aggregation / Metric extraction
# ---------------------------------------------------------------------------

_AGG_FUNCTIONS = {
    "SUM": AggregationType.SUM,
    "COUNT": AggregationType.COUNT,
    "AVG": AggregationType.AVG,
    "MAX": AggregationType.MAX,
    "MIN": AggregationType.MIN,
}


def _extract_aggregations(
    sql_text: str,
) -> list[tuple[AggregationType, str, str]]:
    """Extract (agg_type, column_name, expression) from a SQL query."""
    results: list[tuple[AggregationType, str, str]] = []
    try:
        parsed = sqlglot.parse_one(sql_text, dialect="postgres")
    except sqlglot.errors.ParseError:
        return results

    for func_node in parsed.find_all(exp.Func):
        func_name = type(func_node).__name__.upper()
        if func_name == "ANONYMOUS":
            func_name = (func_node.name or "").upper()

        agg_type = _AGG_FUNCTIONS.get(func_name)
        if agg_type is None:
            continue

        col_refs = list(func_node.find_all(exp.Column))
        col_name = col_refs[0].name if col_refs else "?"
        expression = func_node.sql(dialect="postgres")

        results.append((agg_type, col_name, expression))

    return results


def extract_metrics(
    sql_texts: list[dict[str, Any]],
    evidence: list[str] | None = None,
) -> list[Metric]:
    """Extract Metric objects from aggregation patterns in SQL queries."""
    agg_counts: dict[str, dict[str, Any]] = {}

    for item in sql_texts:
        sql = item.get("sql_text", "")
        if not sql:
            continue

        tables = _extract_table_names(sql)
        aggs = _extract_aggregations(sql)

        for agg_type, col_name, expression in aggs:
            key = f"{agg_type.value}({col_name})"
            if key not in agg_counts:
                agg_counts[key] = {
                    "agg_type": agg_type,
                    "col_name": col_name,
                    "expression": expression,
                    "tables": set(),
                    "count": 0,
                }
            agg_counts[key]["tables"].update(tables)
            agg_counts[key]["count"] += 1

    metrics: list[Metric] = []
    for key, info in sorted(
        agg_counts.items(), key=lambda x: x[1]["count"], reverse=True
    ):
        name = _name_metric(info["agg_type"], info["col_name"], evidence)
        metrics.append(
            Metric(
                name=name,
                expression=info["expression"],
                source_tables=sorted(info["tables"]),
                aggregation_type=info["agg_type"],
            )
        )

    logger.info("[phase2] Extracted %d metrics from SQL aggregations", len(metrics))
    return metrics


def _extract_table_names(sql_text: str) -> list[str]:
    """Extract all table names referenced in a SQL query."""
    try:
        parsed = sqlglot.parse_one(sql_text, dialect="postgres")
    except sqlglot.errors.ParseError:
        return []

    tables: list[str] = []
    for table in parsed.find_all(exp.Table):
        if table.name:
            tables.append(table.name)
    return tables


def _name_metric(
    agg_type: AggregationType,
    col_name: str,
    evidence: list[str] | None = None,
) -> str:
    """Generate a business-friendly metric name.

    Tries to find a name from evidence strings, falls back to
    a generated name from the aggregation pattern.
    """
    if evidence:
        col_lower = col_name.lower()
        for ev in evidence:
            ev_lower = ev.lower()
            if col_lower in ev_lower and any(
                kw in ev_lower
                for kw in ("average", "total", "count", "sum", "ratio", "rate")
            ):
                words = ev.split("=")[0].strip() if "=" in ev else ev
                cleaned = re.sub(r"[^a-zA-Z0-9\s]", "", words).strip()
                if cleaned and len(cleaned) < 50:
                    parts = cleaned.split()
                    return "".join(p.capitalize() for p in parts[:5])

    prefix_map = {
        AggregationType.SUM: "Total",
        AggregationType.COUNT: "CountOf",
        AggregationType.AVG: "Average",
        AggregationType.MAX: "Max",
        AggregationType.MIN: "Min",
        AggregationType.OTHER: "",
    }
    prefix = prefix_map.get(agg_type, "")
    col_parts = re.split(r"[_\s]+", col_name)
    col_camel = "".join(p.capitalize() for p in col_parts if p)
    return f"{prefix}{col_camel}"


# ---------------------------------------------------------------------------
# Combined Phase 2 entry point
# ---------------------------------------------------------------------------


def analyze_sql_behavior(
    sql_texts: list[dict[str, Any]],
    ontology: CoreOntology,
    evidence: list[str] | None = None,
) -> tuple[list[ObjectProperty], list[Metric]]:
    """Run Phase 2 behavioral analysis: JOIN edges + metric extraction."""
    join_edges = extract_join_edges(sql_texts, ontology)
    metrics = extract_metrics(sql_texts, evidence)
    return join_edges, metrics
