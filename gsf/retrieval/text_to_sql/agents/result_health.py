# SPDX-FileCopyrightText: Copyright (c) 2026, NVIDIA CORPORATION & AFFILIATES.
# All rights reserved.
# SPDX-License-Identifier: Apache-2.0

"""Health checks on a non-empty execution result.

An empty result set is obviously suspect and already has repair paths. A result
that came back full of rows can still be silently wrong in one specific way that
is cheap to spot: an output column that is 0 or NULL for *every* row while its
neighbours are populated. That shape means the rows feeding that column were
removed before they could be counted — a WHERE clause that also excludes the
rows carrying the value, or a join that matches nothing — and it executes
cleanly, so nothing else downstream flags it.
"""

from __future__ import annotations

import json
from typing import Any

__all__ = ["build_dead_column_error", "find_dead_result_columns"]

# "Every row is zero" is only evidence of a lost aggregate when there are
# several rows to speak of. On a one- or two-row result a zero is just as likely
# to be the right answer — a minimum/lowest-of aggregate legitimately bottoms
# out at 0 — and flagging it would push a correct value into a needless repair.
_MIN_ROWS = 3


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
