# SPDX-FileCopyrightText: Copyright (c) 2026, NVIDIA CORPORATION & AFFILIATES.
# All rights reserved.
# SPDX-License-Identifier: Apache-2.0

"""Repair a query that executed cleanly but returned no rows.

An empty result is always wrong on BIRD — 0 of 1534 gold queries return zero rows
— so a candidate that returns nothing has already lost, and replacing it cannot
score worse. That makes this repair monotone: at worst the answer stays wrong, at
best it becomes right.

Resampling does not reach these. Measured over the recorded pools, 43 questions
had *every* candidate come back empty, and an independent second run of the same
generators failed on them again. Isolating the cause by ablation explains why: 34
of the 43 become non-empty by relaxing a single predicate, and the culprits are
overwhelmingly literal-formatting errors — ``status = 'legal'`` against stored
``'Legal'``, ``date < '970101'`` against stored ``'1995-03-24'``, a stray leading
space, a backtick where an apostrophe belongs. The model repeats them on every
draw because nothing in the prompt contradicts them.

So rather than asking for another sample, this finds the predicate that empties
the result and shows the model what is actually stored in that column.
"""

from __future__ import annotations

import logging
import re
from typing import Any, Optional

from langchain_core.messages import HumanMessage, SystemMessage
from pydantic import BaseModel, Field

from gsf import flags

logger = logging.getLogger(__name__)

_MAX_CONJUNCTS = 12
_SAMPLE_LIMIT = 8


class _RepairedSQL(BaseModel):
    """Structured rewrite of a query that returned no rows."""

    sql: str = Field(
        default="",
        description=(
            "The corrected, complete, executable SQL. Empty string when the "
            "original query cannot be salvaged."
        ),
    )
    diagnosis: str = Field(
        default="",
        description="One short sentence naming what was wrong with the filter.",
    )


def enabled() -> bool:
    """Whether the empty-result repair runs. Env ``BIRD_EMPTY_REPAIR``."""
    return flags.EMPTY_REPAIR()


# --------------------------------------------------------------------------
# Top-level SQL surgery
# --------------------------------------------------------------------------
def _depths(sql: str) -> list[int]:
    """Paren depth per character; characters inside string literals get ``-1``."""
    depth = 0
    out: list[int] = []
    in_str = False
    i = 0
    while i < len(sql):
        ch = sql[i]
        if in_str:
            out.append(-1)
            if ch == "'":
                if i + 1 < len(sql) and sql[i + 1] == "'":
                    out.append(-1)
                    i += 2
                    continue
                in_str = False
            i += 1
            continue
        if ch == "'":
            in_str = True
            out.append(-1)
            i += 1
            continue
        if ch == "(":
            depth += 1
        out.append(depth)
        if ch == ")":
            depth -= 1
        i += 1
    return out


_TAIL = re.compile(
    r"\b(group\s+by|order\s+by|limit|having|union|intersect|except)\b", re.I
)


def split_where(sql: str) -> Optional[tuple[str, list[str], str]]:
    """``(prefix, top-level AND conjuncts, suffix)`` around the outermost WHERE."""
    depths = _depths(sql)
    where = next(
        (m for m in re.finditer(r"\bwhere\b", sql, re.I) if depths[m.start()] == 0),
        None,
    )
    if where is None:
        return None
    start = where.end()
    end = next(
        (
            m.start()
            for m in _TAIL.finditer(sql)
            if m.start() > start and depths[m.start()] == 0
        ),
        len(sql),
    )
    body, body_depths = sql[start:end], depths[start:end]
    parts: list[str] = []
    last = 0
    for match in re.finditer(r"\band\b", body, re.I):
        if body_depths[match.start()] == 0:
            parts.append(body[last : match.start()])
            last = match.end()
    parts.append(body[last:])
    parts = [p.strip() for p in parts if p.strip()]
    if not parts or len(parts) > _MAX_CONJUNCTS:
        return None
    return sql[:start], parts, sql[end:]


_ALIAS = re.compile(
    r"\b(?:from|join)\s+[`\"\[]?([A-Za-z_]\w*)[`\"\]]?"
    r"(?:\s+(?:as\s+)?[`\"\[]?([A-Za-z_]\w*)[`\"\]]?)?",
    re.I,
)
_ALIAS_STOPWORDS = {
    "on",
    "where",
    "group",
    "order",
    "inner",
    "left",
    "right",
    "join",
    "outer",
    "cross",
    "using",
    "limit",
    "having",
    "select",
}

# ``''`` escapes an apostrophe inside a SQL string, so a plain ``[^']*`` would cut
# "Ancestor''s Chosen" short at "Ancestor".
_COL_LIT = re.compile(
    r"([A-Za-z_]\w*(?:\.[A-Za-z_]\w*)?)\s*(?:=|like|<|>|<=|>=|<>|!=)\s*"
    r"'((?:[^']|'')*)'",
    re.I,
)


def alias_map(sql: str) -> dict[str, str]:
    """``{alias_or_table_name: real_table}`` taken from FROM/JOIN clauses."""
    out: dict[str, str] = {}
    for table, alias in _ALIAS.findall(sql):
        out[table.lower()] = table
        if alias and alias.lower() not in _ALIAS_STOPWORDS:
            out[alias.lower()] = table
    return out


# --------------------------------------------------------------------------
def _row_count(sql: str, connector, run_sql) -> Optional[int]:
    """Row count for *sql*, or ``None`` when it does not execute."""
    import json as _json

    response = run_sql(sql, connector)
    if response.error or not response.result:
        return None
    try:
        rows = _json.loads(response.result[0])
    except (TypeError, ValueError):
        return None
    return len(rows) if isinstance(rows, list) else None


def isolate_culprit(
    sql: str, connector, run_sql
) -> tuple[Optional[str], list[str], dict[str, Any]]:
    """Find the single predicate whose removal makes the result non-empty.

    Returns ``(culprit_text, all_conjuncts, stats)``. ``culprit_text`` is None
    when no single relaxation helps — either the join itself yields nothing, or
    several predicates must go, in which case a literal fix is not the answer.
    """
    stats: dict[str, Any] = {"n_conjuncts": 0, "rows_without_where": None}
    parsed = split_where(sql)
    if parsed is None:
        stats["reason"] = "no_top_level_where"
        return None, [], stats
    prefix, conjuncts, suffix = parsed
    stats["n_conjuncts"] = len(conjuncts)

    def rebuild(parts: list[str]) -> str:
        return f"{prefix} {' AND '.join(parts)} {suffix}"

    without = _row_count(rebuild(["1=1"]), connector, run_sql)
    stats["rows_without_where"] = without
    if not without:
        stats["reason"] = "join_or_shape_empty" if without == 0 else "rewrite_failed"
        return None, conjuncts, stats

    culprits: list[str] = []
    for i, conjunct in enumerate(conjuncts):
        kept = ["1=1" if j == i else c for j, c in enumerate(conjuncts)]
        rows = _row_count(rebuild(kept), connector, run_sql)
        if rows:
            culprits.append(conjunct)
    stats["n_culprits"] = len(culprits)
    if len(culprits) != 1:
        stats["reason"] = (
            "needs_multiple_relaxations" if not culprits else "several_candidates"
        )
        return None, conjuncts, stats
    stats["reason"] = "single_predicate"
    return culprits[0], conjuncts, stats


def sample_values(
    predicate: str, sql: str, connector, run_sql
) -> tuple[str, list[str]]:
    """``(column, real stored values)`` for the literal comparison in *predicate*.

    The qualifier is resolved through the query's own alias map; picking the
    first table that merely has a column of this name can silently return values
    from an unrelated table.
    """
    import json as _json

    match = _COL_LIT.search(predicate)
    if not match:
        return "", []
    column = match.group(1)
    bare = column.split(".")[-1]
    qualifier = column.split(".")[0].lower() if "." in column else None

    aliases = alias_map(sql)
    tables: list[str] = []
    if qualifier and qualifier in aliases:
        tables.append(aliases[qualifier])
    tables.extend(t for t in aliases.values() if t not in tables)

    for table in tables:
        response = run_sql(
            f'SELECT DISTINCT "{bare}" FROM "{table}" '
            f'WHERE "{bare}" IS NOT NULL LIMIT {_SAMPLE_LIMIT}',
            connector,
        )
        if response.error or not response.result:
            continue
        try:
            rows = _json.loads(response.result[0])
        except (TypeError, ValueError):
            continue
        values = [
            str(list(r.values())[0])[:60] for r in rows if isinstance(r, dict) and r
        ]
        if values:
            return f"{table}.{bare}", values
    return column, []


_SYSTEM = (
    "You repair a SQL query that executed without error but returned ZERO rows. "
    "On this benchmark an empty result is always wrong, so the query must be "
    "fixed. One predicate has been identified as the cause: removing it makes "
    "the query return rows. The real values stored in that column are given "
    "below. Almost always the predicate's literal does not match the stored "
    "format — wrong capitalisation, a date written differently, a stray leading "
    "or trailing space, a backtick or curly quote instead of an apostrophe, or a "
    "value that belongs to a different column. Rewrite the query so the filter "
    "expresses the question's intent against the values as they are actually "
    "stored. Keep the rest of the query unchanged, preserve the original "
    "projection and grain, and never satisfy the query by simply deleting the "
    "filter. Return complete executable SQL with no commentary."
)


def repair(
    *,
    question: str,
    evidence: Optional[str],
    sql: str,
    connector,
    run_sql,
) -> tuple[Optional[str], dict[str, Any]]:
    """Return ``(repaired_sql_or_None, stats)`` for a query that returned no rows.

    A repair is only returned when it executes and returns at least one row, so
    the caller can adopt it unconditionally: it replaces a guaranteed-wrong
    answer with one that is at least plausible.
    """
    from gsf.utils.llm_invoke import get_llm_client, invoke_with_structured_output

    stats: dict[str, Any] = {"triggered": True, "applied": False, "reason": ""}
    culprit, conjuncts, isolation = isolate_culprit(sql, connector, run_sql)
    stats.update(isolation)
    if culprit is None:
        return None, stats

    column, values = sample_values(culprit, sql, connector, run_sql)
    stats["column"] = column
    stats["n_sampled_values"] = len(values)

    human = (
        f"Question:\n{question}\n\n"
        f"Evidence (authoritative if present):\n{evidence or '(none)'}\n\n"
        f"SQL that returned zero rows:\n{sql}\n\n"
        f"Predicate responsible (removing it yields "
        f"{isolation.get('rows_without_where')} rows or more):\n{culprit}\n\n"
        f"Values actually stored in {column or 'that column'}:\n"
        + ("\n".join(f"  {v!r}" for v in values) if values else "  (could not sample)")
        + "\n\nRewrite the query so this filter matches the stored format while "
        "still expressing the question's intent."
    )
    try:
        llm = get_llm_client(temperature=0.0, max_tokens=4096)
        verdict = invoke_with_structured_output(
            llm,
            [SystemMessage(content=_SYSTEM), HumanMessage(content=human)],
            _RepairedSQL,
        )
    except Exception as exc:
        stats["reason"] = f"llm_error:{type(exc).__name__}"
        return None, stats

    candidate = ((verdict.sql if verdict else "") or "").strip().rstrip(";")
    stats["diagnosis"] = ((verdict.diagnosis if verdict else "") or "")[:200]
    stats["repaired_sql"] = candidate[:400]
    if not candidate or candidate == sql.strip().rstrip(";"):
        stats["reason"] = "no_rewrite"
        return None, stats

    rows = _row_count(candidate, connector, run_sql)
    stats["repaired_rows"] = rows
    if not rows:
        stats["reason"] = "repair_still_empty" if rows == 0 else "repair_failed"
        return None, stats

    stats["applied"] = True
    stats["reason"] = "applied"
    return candidate, stats
