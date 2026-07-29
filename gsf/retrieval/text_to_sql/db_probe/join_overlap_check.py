# SPDX-FileCopyrightText: Copyright (c) 2026, NVIDIA CORPORATION & AFFILIATES.
# All rights reserved.
# SPDX-License-Identifier: Apache-2.0

"""Joins whose two sides share no values at all, found by probing live rows.

An empty result usually traces to a filter, and the literal and date checks own
those cases. This covers the remaining one: a join key that looks perfectly
reasonable — same name, same format, same type — but whose two columns hold
disjoint value sets, so the join matches nothing and every filter downstream is
irrelevant.

The classic shape is a date dimension that does not span the fact table's
period. ``cities.insert_date`` covers 2021-2023 while
``legislation_date_dim.date`` stops at 1999, so ``ON c.insert_date = d.date``
matches zero of 44k rows. Nothing errors, no literal is wrong, and both columns
are well-formed ISO dates, which is exactly why this is expensive to spot by
reading the query.

The test deliberately drops every filter and probes the ``ON`` condition alone:
a join that matches nothing even unfiltered cannot be rescued by changing a
``WHERE``, so this is evidence of a wrong table or a wrong key rather than a
legitimately empty answer.
"""

from __future__ import annotations

import logging
from typing import Any, Optional

import sqlglot
from sqlglot import exp

from gsf.retrieval.text_to_sql.db_probe.executor import ProbeExecutor

logger = logging.getLogger(__name__)

# Probing costs 1 query per join plus 2 for the per-side ranges, so cap how many
# distinct joins are judged in one query.
_MAX_JOINS_CHECKED = 4

# Aliases for the probe's own sides; unlikely to collide with model-authored SQL.
_LEFT = "__probe_l"
_RIGHT = "__probe_r"

_SQLGLOT_DIALECTS = {
    "postgresql": "postgres",
    "postgres": "postgres",
    "sqlite": "sqlite",
    "duckdb": "duckdb",
    "snowflake": "snowflake",
    "mysql": "mysql",
    "bigquery": "bigquery",
}


def _sqlglot_dialect(dialect: Optional[str]) -> Optional[str]:
    return _SQLGLOT_DIALECTS.get((dialect or "").lower())


def _cte_names(tree: exp.Expression) -> set[str]:
    """Names bound by ``WITH``, which parse as tables but cannot be probed."""
    return {
        name.lower()
        for cte in tree.find_all(exp.CTE)
        if (name := cte.alias_or_name)
    }


def _base_tables(tree: exp.Expression, cte_names: set[str]) -> dict[str, exp.Table]:
    """Map alias and name to the real tables of *tree*, excluding CTE references."""
    by_key: dict[str, exp.Table] = {}
    for table in tree.find_all(exp.Table):
        if (table.name or "").lower() in cte_names:
            continue
        if table.name:
            by_key.setdefault(table.name.lower(), table)
        if table.alias:
            by_key[table.alias.lower()] = table
    return by_key


def _resolve(column: exp.Column, by_key: dict[str, exp.Table]) -> Optional[exp.Table]:
    """The base table *column* is qualified against, or ``None`` if unresolvable.

    An unqualified column is skipped on purpose: without a qualifier it cannot be
    attributed to one side of the join, and guessing would probe the wrong pair.
    """
    qualifier = (column.table or "").lower()
    return by_key.get(qualifier) if qualifier else None


def _table_sql(table: exp.Table, dialect: Optional[str]) -> str:
    """Render *table* without its alias so the probe can bind its own."""
    clone = table.copy()
    clone.set("alias", None)
    return clone.sql(dialect=dialect)


def _column_sql(column: exp.Column, dialect: Optional[str]) -> str:
    """Render the bare column name, dropping the query's own qualifier."""
    return exp.column(column.this).sql(dialect=dialect)


def _join_key_pairs(
    tree: exp.Expression, by_key: dict[str, exp.Table]
) -> list[dict[str, Any]]:
    """Group each join's column equalities by the pair of tables they connect.

    A composite key (several ANDed equalities) is probed as one unit, since any
    single equality on its own may well overlap while the combination does not.
    """
    grouped: dict[tuple[str, str], dict[str, Any]] = {}
    for join in tree.find_all(exp.Join):
        on = join.args.get("on")
        if on is None:
            continue
        for eq in on.find_all(exp.EQ):
            left, right = eq.this, eq.expression
            if not (isinstance(left, exp.Column) and isinstance(right, exp.Column)):
                continue
            left_table, right_table = _resolve(left, by_key), _resolve(right, by_key)
            if left_table is None or right_table is None:
                continue
            if left_table is right_table:  # self-join; overlap is trivially non-empty
                continue
            # Order the pair so the same two tables always land in one group,
            # keeping the columns aligned with the side they came from.
            if left_table.name.lower() > right_table.name.lower():
                left, right = right, left
                left_table, right_table = right_table, left_table
            key = (left_table.name.lower(), right_table.name.lower())
            entry = grouped.setdefault(
                key,
                {"left_table": left_table, "right_table": right_table, "columns": []},
            )
            pair = (left.name, right.name)
            if pair not in [(a.name, b.name) for a, b in entry["columns"]]:
                entry["columns"].append((left, right))
    return list(grouped.values())


def _column_profile(
    executor: ProbeExecutor,
    table: exp.Table,
    column: exp.Column,
    dialect: Optional[str],
) -> Optional[dict[str, Any]]:
    """Row count and value range for *column*, used to explain a failed join."""
    if not executor.budget_left:
        return None
    col = _column_sql(column, dialect)
    probe = (
        f"SELECT COUNT(*) AS row_count, MIN({col}) AS min_value, "
        f"MAX({col}) AS max_value FROM {_table_sql(table, dialect)}"
    )
    result = executor.run(probe, purpose="join_overlap_profile")
    if not result["ok"] or not result["rows"]:
        return None
    row = result["rows"][0]
    return {
        "rows": row.get("row_count"),
        "min": row.get("min_value"),
        "max": row.get("max_value"),
    }


def _has_overlap(
    executor: ProbeExecutor,
    candidate: dict[str, Any],
    dialect: Optional[str],
) -> Optional[bool]:
    """Whether the join's ``ON`` matches any row at all. ``None`` if unprobeable."""
    if not executor.budget_left:
        return None
    conditions = " AND ".join(
        f"{_LEFT}.{_column_sql(left, dialect)} = {_RIGHT}.{_column_sql(right, dialect)}"
        for left, right in candidate["columns"]
    )
    probe = (
        f"SELECT 1 AS matched "
        f"FROM {_table_sql(candidate['left_table'], dialect)} AS {_LEFT} "
        f"JOIN {_table_sql(candidate['right_table'], dialect)} AS {_RIGHT} "
        f"ON {conditions} LIMIT 1"
    )
    result = executor.run(probe, purpose="join_overlap_check")
    if not result["ok"]:
        return None
    return bool(result["rows"])


def find_empty_joins(
    executor: ProbeExecutor,
    dialect: Optional[str],
    sql: str,
) -> list[dict[str, Any]]:
    """Return joins that live rows prove match nothing, unfiltered.

    Each entry carries ``left``/``right`` (``{table, column, rows, min, max}``)
    for every key column pair. An empty list means every join in the query does
    match rows, so the empty result came from somewhere else.
    """
    d = _sqlglot_dialect(dialect)
    try:
        tree = sqlglot.parse_one(sql, read=d)
    except Exception as exc:  # noqa: BLE001 — never break the pipeline on a parse error
        logger.info("join_overlap_check: could not parse SQL (%s)", exc)
        return []
    if tree is None:
        return []

    by_key = _base_tables(tree, _cte_names(tree))
    if len(by_key) < 2:
        return []

    findings: list[dict[str, Any]] = []
    for candidate in _join_key_pairs(tree, by_key)[:_MAX_JOINS_CHECKED]:
        if _has_overlap(executor, candidate, dialect) is not False:
            continue
        keys = []
        for left, right in candidate["columns"]:
            left_profile = _column_profile(
                executor, candidate["left_table"], left, dialect
            )
            right_profile = _column_profile(
                executor, candidate["right_table"], right, dialect
            )
            keys.append(
                {
                    "left": {
                        "table": candidate["left_table"].name,
                        "column": left.name,
                        **(left_profile or {}),
                    },
                    "right": {
                        "table": candidate["right_table"].name,
                        "column": right.name,
                        **(right_profile or {}),
                    },
                }
            )
        findings.append({"keys": keys})
    return findings


def _describe(side: dict[str, Any]) -> str:
    name = f"{side['table']}.{side['column']}"
    if side.get("rows") is None:
        return name
    span = ""
    if side.get("min") is not None or side.get("max") is not None:
        span = f", values {side.get('min')!r}..{side.get('max')!r}"
    return f"{name} ({side['rows']} rows{span})"


def build_join_repair_error(findings: list[dict[str, Any]]) -> str:
    """Render empty joins into a targeted reconstruction instruction."""
    blocks = []
    for finding in findings:
        for key in finding["keys"]:
            blocks.append(
                f"- {_describe(key['left'])} joined to {_describe(key['right'])} "
                "matches no rows at all, even with every filter removed."
            )
    body = "\n".join(blocks)
    return (
        "The query returns nothing because one of its joins matches no rows. "
        "Probing the join condition on its own, with all filters removed, "
        "shows:\n"
        f"{body}\n\n"
        "The two sides hold no values in common, so no WHERE clause can rescue "
        "this join — the table or the key is wrong. Compare the value ranges "
        "above against the range the question asks about: a table that does not "
        "cover that period cannot contribute to the answer. If one table only "
        "supplied the join key, drop it and read the columns you need directly "
        "from the table that actually holds the matching values; otherwise join "
        "through a key the two tables genuinely share. Keep the rest of the "
        "query's intent as it is."
    )


__all__ = ["find_empty_joins", "build_join_repair_error"]
