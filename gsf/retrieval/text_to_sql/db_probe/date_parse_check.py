# SPDX-FileCopyrightText: Copyright (c) 2026, NVIDIA CORPORATION & AFFILIATES.
# All rights reserved.
# SPDX-License-Identifier: Apache-2.0

"""Date-handling faults on a text column, found by probing live rows.

Two failures show up repeatedly, and neither is visible to the literal check
because nothing is wrong with the literals:

**parse** — a query derives a date with string surgery
(``substr``/``instr``/``strftime``) and the parse silently yields NULL or a
nonsense year, so every comparison against it is false.

**comparison** — a query compares a ragged date column directly against an ISO
date. The comparison is then lexicographic, and ``'2020-8-31' <= '2020-06-15'``
is false because ``8`` sorts after ``0``, quietly excluding every row.

Both fail without erroring, which is what makes them expensive to find by hand.

Like the literal check, this assumes nothing about the incoming database. It
takes the derived expression straight from the SQL's own AST and *runs it* on a
handful of live rows, so the repair hint can show the model what its parse
actually produced next to the value that went in. A parse is only reported when
the output is provably broken — a NULL from a non-NULL input, or a year outside
any plausible range — so a legitimately empty answer is left alone.
"""

from __future__ import annotations

import logging
import re
from typing import Any, Optional

import sqlglot
from sqlglot import exp

from gsf.retrieval.text_to_sql.db_probe.executor import ProbeExecutor

logger = logging.getLogger(__name__)

# How many live rows to show the model per suspect expression.
_SAMPLE_LIMIT = 5

# Years outside this range are treated as a broken parse rather than real data.
_MIN_PLAUSIBLE_YEAR = 1900
_MAX_PLAUSIBLE_YEAR = 2100

_DATE_NAME_TOKENS = ("date", "time", "day", "month", "year", "dt")

_SQLGLOT_DIALECTS = {
    "postgresql": "postgres",
    "postgres": "postgres",
    "sqlite": "sqlite",
    "duckdb": "duckdb",
    "snowflake": "snowflake",
    "mysql": "mysql",
    "bigquery": "bigquery",
}

_LEADING_YEAR = re.compile(r"^\s*(\d{1,4})\s*-")

# A date every dialect can read directly: zero-padded ISO, optionally with time.
# This is what separates a usable sibling column ('2020-08-31') from a decoy
# that merely looks formatted ('2020-8-31').
_USABLE_ISO = re.compile(r"^\d{4}-\d{2}-\d{2}([ T]\d{2}:\d{2}(:\d{2})?)?$")

# Operands that mark a comparison as being against a calendar date: a whole
# date, or the month-day tail that gets concatenated onto a year column.
_ISO_DATE_LITERAL = re.compile(r"^\d{4}-\d{1,2}-\d{1,2}")
_ISO_SUFFIX_LITERAL = re.compile(r"^-\d{1,2}-\d{1,2}$")


def _sqlglot_dialect(dialect: Optional[str]) -> Optional[str]:
    return _SQLGLOT_DIALECTS.get((dialect or "").lower())


def _looks_date_named(column: exp.Column) -> bool:
    name = (column.name or "").lower()
    return any(token in name for token in _DATE_NAME_TOKENS)


def _derivation_root(column: exp.Column) -> Optional[exp.Expression]:
    """Highest scalar expression built around *column*, or ``None`` if bare.

    Climbs through function calls, casts, concatenation and parentheses, and
    stops at the first boolean/clause boundary so the result stays something we
    can put in a ``SELECT`` list.
    """
    node: exp.Expression = column
    parent = node.parent
    while isinstance(parent, (exp.Func, exp.Cast, exp.DPipe, exp.Paren)):
        node = parent
        parent = node.parent
    return node if node is not column else None


def _unqualify(expression: exp.Expression) -> exp.Expression:
    """Copy *expression* with table qualifiers dropped so it runs standalone."""
    clone = expression.copy()
    for column in clone.find_all(exp.Column):
        column.set("table", None)
    return clone


def _suspect_derivations(
    tree: exp.Expression,
) -> list[tuple[exp.Column, exp.Expression]]:
    """Date-named columns that the query feeds into string/date surgery."""
    seen: set[tuple[str, str]] = set()
    out: list[tuple[exp.Column, exp.Expression]] = []
    for column in tree.find_all(exp.Column):
        if not _looks_date_named(column):
            continue
        root = _derivation_root(column)
        if root is None:
            continue
        key = (column.name.lower(), root.sql())
        if key in seen:
            continue
        seen.add(key)
        out.append((column, root))
    return out


def _year_of(value: Any) -> Optional[int]:
    match = _LEADING_YEAR.match(str(value))
    return int(match.group(1)) if match else None


def _is_broken(stored: Any, derived: Any) -> bool:
    """Whether *derived* is provably not a usable date built from *stored*."""
    if stored is None:
        return False
    if derived is None:
        return True  # silent NULL parse — the classic strftime/date trap
    year = _year_of(derived)
    if year is None:
        return False
    return not (_MIN_PLAUSIBLE_YEAR <= year <= _MAX_PLAUSIBLE_YEAR)


def _find_usable_date_column(
    executor: ProbeExecutor,
    table: exp.Table,
    broken_column: str,
    dialect: Optional[str],
) -> Optional[tuple[str, Any]]:
    """A sibling column of *table* already storing a directly usable date.

    Samples the row shape and judges the format in Python rather than with a
    dialect-specific date function, so this works anywhere. Returns
    ``(column_name, example_value)`` or ``None``.
    """
    if not executor.budget_left:
        return None
    d = _sqlglot_dialect(dialect)
    result = executor.run(
        f"SELECT * FROM {table.sql(dialect=d)} LIMIT {_SAMPLE_LIMIT}",
        purpose="date_parse_alternative",
    )
    if not result["ok"] or not result["rows"]:
        return None

    rows = result["rows"]
    for name in rows[0]:
        if name.lower() == broken_column.lower():
            continue
        values = [row.get(name) for row in rows]
        present = [v for v in values if v is not None]
        if present and all(_USABLE_ISO.match(str(v)) for v in present):
            return name, present[0]
    return None


def _table_candidates(tree: exp.Expression, column: exp.Column) -> list[exp.Table]:
    tables = list(tree.find_all(exp.Table))
    qualifier = (column.table or "").lower()
    if not qualifier:
        return tables
    for table in tables:
        if qualifier in {(table.name or "").lower(), (table.alias or "").lower()}:
            return [table]
    return tables


def _iso_operand(node: exp.Expression) -> Optional[str]:
    """An ISO-date-shaped string the expression compares against, if any.

    Matches both a whole date (``'2020-06-15'``) and a month-day suffix that is
    concatenated onto a year (``calendar_year || '-06-15'``).
    """
    for literal in node.find_all(exp.Literal):
        if not literal.is_string:
            continue
        text = str(literal.this)
        if _ISO_DATE_LITERAL.match(text) or _ISO_SUFFIX_LITERAL.match(text):
            return text
    return None


def _bare_date_comparisons(
    tree: exp.Expression,
) -> list[tuple[exp.Column, str]]:
    """Date-named columns compared *directly* against an ISO-shaped operand.

    Only bare columns count: once a column is wrapped in a function the parse
    check owns it, and double-reporting the same column would be noise.
    """
    seen: set[str] = set()
    out: list[tuple[exp.Column, str]] = []
    comparisons = (exp.LT, exp.LTE, exp.GT, exp.GTE, exp.EQ, exp.Between)
    for node_type in comparisons:
        for node in tree.find_all(node_type):
            for side in (node.this, node.args.get("expression")):
                column = side if isinstance(side, exp.Column) else None
                if column is None or not _looks_date_named(column):
                    continue
                if _derivation_root(column) is not None:
                    continue
                if _iso_operand(node) is None or column.name.lower() in seen:
                    continue
                seen.add(column.name.lower())
                # Show the whole predicate: a concatenated operand renders as a
                # bare '-06-15' on its own, which reads like nonsense.
                out.append((column, node.sql()))
    return out


def _fetch_column_samples(
    executor: ProbeExecutor,
    table: exp.Table,
    column: exp.Column,
    dialect: Optional[str],
) -> Optional[list[Any]]:
    if not executor.budget_left:
        return None
    d = _sqlglot_dialect(dialect)
    probe = (
        f"SELECT {exp.column(column.this).sql(dialect=d)} AS value "
        f"FROM {table.sql(dialect=d)} LIMIT {_SAMPLE_LIMIT}"
    )
    result = executor.run(probe, purpose="date_comparison_check")
    if not result["ok"] or not result["rows"]:
        return None
    return [row.get("value") for row in result["rows"]]


def _find_comparison_faults(
    executor: ProbeExecutor,
    tree: exp.Expression,
    dialect: Optional[str],
) -> list[dict[str, Any]]:
    d = _sqlglot_dialect(dialect)
    findings: list[dict[str, Any]] = []
    for column, predicate in _bare_date_comparisons(tree):
        for table in _table_candidates(tree, column):
            values = _fetch_column_samples(executor, table, column, dialect)
            if values is None:
                continue
            present = [v for v in values if v is not None]
            if present and not all(_USABLE_ISO.match(str(v)) for v in present):
                findings.append(
                    {
                        "kind": "comparison",
                        "table": table.name,
                        "column": column.name,
                        "predicate": predicate,
                        "samples": present,
                        "alternative": _find_usable_date_column(
                            executor, table, column.name, dialect
                        ),
                    }
                )
            break  # column resolved against this table
    _ = d
    return findings


def find_date_faults(
    executor: ProbeExecutor,
    dialect: Optional[str],
    sql: str,
) -> list[dict[str, Any]]:
    """Return date-handling faults that live rows prove are real.

    Each entry carries a ``kind`` of ``"parse"`` or ``"comparison"`` plus the
    ``table``, ``column``, sampled evidence, and an ``alternative`` column when
    one already stores a usable date. An empty list means nothing to repair.
    """
    d = _sqlglot_dialect(dialect)
    try:
        tree = sqlglot.parse_one(sql, read=d)
    except Exception as exc:  # noqa: BLE001 — never break the pipeline on a parse error
        logger.info("date_parse_check: could not parse SQL (%s)", exc)
        return []
    if tree is None:
        return []

    findings: list[dict[str, Any]] = []
    for column, root in _suspect_derivations(tree):
        for table in _table_candidates(tree, column):
            if not executor.budget_left:
                return findings
            probe = (
                f"SELECT {exp.column(column.this).sql(dialect=d)} AS stored_value, "
                f"{_unqualify(root).sql(dialect=d)} AS derived_value "
                f"FROM {table.sql(dialect=d)} LIMIT {_SAMPLE_LIMIT}"
            )
            result = executor.run(probe, purpose="date_parse_check")
            if not result["ok"] or not result["rows"]:
                continue
            samples = [
                (row.get("stored_value"), row.get("derived_value"))
                for row in result["rows"]
            ]
            if any(_is_broken(stored, derived) for stored, derived in samples):
                findings.append(
                    {
                        "kind": "parse",
                        "table": table.name,
                        "column": column.name,
                        "expression": root.sql(dialect=d),
                        "samples": samples,
                        "alternative": _find_usable_date_column(
                            executor, table, column.name, dialect
                        ),
                    }
                )
            break  # the column resolved against this table; don't try the rest

    return findings + _find_comparison_faults(executor, tree, dialect)


def build_date_repair_error(findings: list[dict[str, Any]]) -> str:
    """Render date faults into a targeted reconstruction instruction."""
    blocks = []
    for finding in findings:
        if finding.get("kind") == "comparison":
            stored = ", ".join(repr(v) for v in dict.fromkeys(finding["samples"]))
            block = (
                f"- Column {finding['table']}.{finding['column']} is compared to a "
                f"date without being parsed, in:\n    {finding['predicate']}\n"
                f"  It stores values like [{stored}], which are not zero-padded "
                "ISO dates, so this comparison is made on text: a month written "
                "'8' sorts after '06', silently excluding rows that should match."
            )
        else:
            pairs = "\n".join(
                f"    {stored!r} -> {derived!r}"
                for stored, derived in finding["samples"]
            )
            block = (
                f"- Column {finding['table']}.{finding['column']} parsed by:\n"
                f"    {finding['expression']}\n"
                f"  On real rows this produces:\n{pairs}"
            )
        alternative = finding.get("alternative")
        if alternative:
            name, example = alternative
            block += (
                f"\n  Use {finding['table']}.{name} instead: it already stores a "
                f"directly usable date (e.g. {example!r}), so it needs no parsing."
            )
        blocks.append(block)
    body = "\n".join(blocks)
    return (
        "The query mishandles a date stored as text, so comparisons against it "
        "are false and values built from it are wrong. Probing real rows shows:\n"
        f"{body}\n\n"
        "Fix the date handling. Match the parse to the format actually stored "
        "(a two-digit year needs a century added, not zero-padding to four "
        "digits; a month like '8' is not the same as '08'). Prefer a column that "
        "already holds a usable date over parsing one yourself — a name "
        "containing 'formatted' does not mean the format is usable, so judge it "
        "by the stored value. Change ONLY the date handling; keep the joins, "
        "columns, grouping, and other filters as they are."
    )


__all__ = [
    "build_date_repair_error",
    "find_date_faults",
]
