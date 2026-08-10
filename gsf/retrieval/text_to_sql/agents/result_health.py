# SPDX-FileCopyrightText: Copyright (c) 2026, NVIDIA CORPORATION & AFFILIATES.
# All rights reserved.
# SPDX-License-Identifier: Apache-2.0

"""Health checks on a non-empty execution result.

An empty result set is obviously suspect and already has repair paths. A result
that came back full of rows can still be silently wrong in ways that are cheap
to spot:

* an output column that is 0 or NULL for *every* row while its neighbours are
  populated — the rows feeding that column were filtered away before they could
  be counted;
* a singular superlative question ("the shortest…", "which store has the
  highest…") that returned more than one row — the prompt already asks for
  ``LIMIT 1``, but the model often returns every tie or every join-fanout
  duplicate instead.
"""

from __future__ import annotations

import json
import re
from typing import Any

__all__ = [
    "build_dead_column_error",
    "build_superlative_cardinality_error",
    "count_result_rows",
    "find_dead_result_columns",
    "has_exact_duplicate_rows",
    "is_singular_superlative_question",
    "should_repair_superlative_cardinality",
]

# "Every row is zero" is only evidence of a lost aggregate when there are
# several rows to speak of. On a one- or two-row result a zero is just as likely
# to be the right answer — a minimum/lowest-of aggregate legitimately bottoms
# out at 0 — and flagging it would push a correct value into a needless repair.
_MIN_ROWS = 3

_SUPERLATIVE = (
    r"(?:shortest|longest|highest|lowest|largest|smallest|biggest|"
    r"best|worst|most|least|top|bottom|nearest|farthest|furthest|"
    r"earliest|latest|fastest|slowest|youngest|oldest|peak)"
)

# Explicit multi-row ask — leave these alone even if a superlative word appears.
_EXPLICIT_MULTI = re.compile(
    r"\b(?:top|bottom|first|last)\s+(?:\d+|one|two|three|four|five|six|seven|"
    r"eight|nine|ten|eleven|twelve)\b"
    r"|\b(?:\d+|one|two|three|four|five|six|seven|eight|nine|ten|eleven|twelve)"
    r"\s+(?:highest|lowest|best|worst|most|least|largest|smallest|biggest|"
    r"shortest|longest|top|bottom)\b"
    r"|\b(?:highest|lowest|best|worst|most|least|largest|smallest)\s+and\s+"
    r"(?:highest|lowest|best|worst|most|least|largest|smallest)\b",
    re.IGNORECASE,
)

# Singular cue: "the shortest", "which X has the highest", "what is the lowest".
_SINGULAR_SUPERLATIVE = re.compile(
    rf"\bthe\s+{_SUPERLATIVE}\b"
    rf"|\b(?:which|what|who)\b(?:\s+\w+){{0,8}}\b(?:has|have|had|with)\s+"
    rf"(?:the\s+)?{_SUPERLATIVE}\b",
    re.IGNORECASE,
)

_LIMIT_CLAUSE = re.compile(
    r"\blimit\s+(\d+)\b",
    re.IGNORECASE,
)


def _parse_result_records(result: Any) -> list[dict] | None:
    """Decode ``sql_response_from_db`` into row dicts, or ``None`` if not tabular."""
    if isinstance(result, list) and len(result) == 1 and isinstance(result[0], str):
        try:
            result = json.loads(result[0])
        except json.JSONDecodeError:
            return None
    if not isinstance(result, list) or not result:
        return None
    if not all(isinstance(row, dict) for row in result):
        return None
    return result


def count_result_rows(result: Any) -> int:
    """Number of tabular rows in ``sql_response_from_db``, or 0 if unparseable."""
    rows = _parse_result_records(result)
    return 0 if rows is None else len(rows)


def has_exact_duplicate_rows(result: Any) -> bool:
    """True when at least two returned rows are identical key/value maps."""
    rows = _parse_result_records(result)
    if not rows or len(rows) < 2:
        return False
    seen: set[tuple[tuple[str, str], ...]] = set()
    for row in rows:
        key = tuple(sorted((str(k), repr(v)) for k, v in row.items()))
        if key in seen:
            return True
        seen.add(key)
    return False


def is_singular_superlative_question(question: str) -> bool:
    """Whether *question* asks for a single superlative item (not top-N / both ends)."""
    text = (question or "").strip()
    if not text:
        return False
    if _EXPLICIT_MULTI.search(text):
        return False
    return _SINGULAR_SUPERLATIVE.search(text) is not None


def _sql_limit(sql: str | None) -> int | None:
    if not sql:
        return None
    match = _LIMIT_CLAUSE.search(sql)
    if not match:
        return None
    return int(match.group(1))


def should_repair_superlative_cardinality(
    question: str,
    result: Any,
    sql: str | None = None,
) -> bool:
    """True when a singular superlative question returned more than one row.

    Skips questions that already ask for multiple rows (``top 5``, ``highest and
    lowest``) and queries that already carry any ``LIMIT`` (``LIMIT 1`` has
    nothing left to add; ``LIMIT N>1`` is an explicit multi-row budget).
    """
    if not is_singular_superlative_question(question):
        return False
    if count_result_rows(result) <= 1:
        return False
    # Any existing LIMIT means the model already chose a row budget; do not
    # second-guess it here (LIMIT 1 has nothing left to add; LIMIT N>1 is an
    # explicit multi-row ask).
    if _sql_limit(sql) is not None:
        return False
    return True


def build_superlative_cardinality_error(
    n_rows: int,
    *,
    has_duplicates: bool = False,
) -> str:
    """Reconstruction hint for a singular superlative that returned many rows."""
    dup_note = (
        " The rows are exact duplicates of each other, so also collapse them "
        "with DISTINCT or by fixing the join fan-out that multiplied them."
        if has_duplicates
        else ""
    )
    return (
        f"SQL executed successfully, but the question asks for a single "
        f"superlative answer and the result has {n_rows} rows.{dup_note} "
        "Reconstruct the SQL so it returns exactly one row: add LIMIT 1 for a "
        "singular superlative (the shortest/highest/lowest/…), and if several "
        "rows are identical collapse the exact duplicates. Keep the rest of "
        "the query as it is."
    )


def _is_blank_metric(value: Any) -> bool:
    """Whether *value* reads as "nothing was counted here".

    Booleans are excluded deliberately: ``False`` equals 0 in Python, but an
    all-``False`` flag column is a legitimate answer rather than a lost
    aggregate.
    """
    if value is None:
        return True
    if isinstance(value, bool):
        return False
    return isinstance(value, (int, float)) and value == 0


def find_dead_result_columns(result: Any) -> list[str]:
    """Return columns that are 0/NULL in every row while some sibling is not.

    Returns ``[]`` for results too short to judge (see ``_MIN_ROWS``), when every
    column is blank (that is an empty-ish result, and the empty-result paths own
    it), and when the result is a single column (a scalar zero can be the right
    answer).
    """
    rows = _parse_result_records(result)
    if not rows or len(rows) < _MIN_ROWS:
        return []

    dead: list[str] = []
    has_live_column = False
    for column in rows[0]:
        if all(_is_blank_metric(row.get(column)) for row in rows):
            dead.append(column)
        else:
            has_live_column = True
    return dead if has_live_column else []


def build_dead_column_error(columns: list[str]) -> str:
    """Reconstruction hint naming the columns that came back empty."""
    listed = ", ".join(columns)
    return (
        "SQL executed successfully, but these output columns are 0 or NULL for "
        f"every single row while other columns are populated: {listed}. That "
        "almost always means the rows those columns aggregate were filtered out "
        "before they could be counted. Check whether a WHERE clause excludes "
        "the rows that carry the value, and whether a join drops them. If the "
        "value comes from an event, status, or flag stored on a different row "
        "than the one being grouped, test for it with EXISTS over the shared "
        "key instead of counting it on the grouped rows. Reconstruct the SQL so "
        "these columns are computed correctly, and keep the rest of the query "
        "as it is."
    )
