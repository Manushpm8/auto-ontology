# SPDX-FileCopyrightText: Copyright (c) 2026, NVIDIA CORPORATION & AFFILIATES.
# All rights reserved.
# SPDX-License-Identifier: Apache-2.0

"""Stop candidates that differ only in column order from splitting the vote.

``sql_selection._result_signature`` keeps projected column order significant, so
``SELECT Phone, Ext, School`` and ``SELECT School, Phone, Ext`` — the same row,
the same values — form rival clusters. The vote then splits between them and the
lowest index wins, which is arbitrary. Observed on california_schools: BIRD
compares tuples positionally, so the arbitrary half is simply wrong, and two
questions were lost that way with gold sitting in their own pools.

This module supplies the two pieces that fix it:

* :func:`merge_permuted` folds clusters whose results are row-wise permutations
  of each other into one, so the vote concentrates rather than splits. Merging is
  allowed only when the members project the *same set* of columns, which is what
  keeps unrelated queries whose values happen to coincide apart.
* :func:`preferred_index` then breaks the tie by meaning rather than by index:
  the winner is the candidate whose projection follows the order the columns are
  named in the evidence, falling back to the question. The evidence wins because
  it is the authoritative gloss — BIRD's gold for q37 follows the evidence's
  "Street, City, State, Zip" even though the question says "Street, City, Zip
  and State".

Scoped by database through ``BIRD_PROJECTION_ORDER_DBS`` (comma-separated
db_ids, ``*`` for all), so it can be measured on one schema before it is trusted
everywhere.
"""

from __future__ import annotations

import logging
import re
from typing import Iterable, Optional, Sequence

from gsf import flags

logger = logging.getLogger(__name__)

_WORD = re.compile(r"[A-Za-z][A-Za-z0-9]*")


def enabled_for(db_id: str | None) -> bool:
    """Whether projection-order handling applies to *db_id*."""
    wanted = flags.PROJECTION_ORDER_DBS()
    if not wanted:
        return False
    return "*" in wanted or (bool(db_id) and str(db_id) in wanted)


def projection_columns(sql: str) -> Optional[tuple[str, ...]]:
    """Normalized projection of *sql*'s outermost SELECT, in order.

    Returns ``None`` when the SQL cannot be parsed or projects ``*``: without a
    known column list there is no basis for calling two candidates permutations
    of one another, and guessing would merge queries that differ in substance.
    """
    try:
        import sqlglot
        from sqlglot import exp
    except ImportError:  # pragma: no cover - sqlglot ships with the stack
        return None

    try:
        tree = sqlglot.parse_one(sql or "", read="sqlite")
    except Exception:
        return None
    if tree is None:
        return None

    select = tree if isinstance(tree, exp.Select) else tree.find(exp.Select)
    if select is None:
        return None

    out: list[str] = []
    for item in select.expressions:
        if isinstance(item, exp.Star):
            return None
        inner = item.unalias() if isinstance(item, exp.Alias) else item
        if isinstance(inner, exp.Star) or inner.find(exp.Star):
            return None
        if isinstance(inner, exp.Column):
            out.append(inner.name.strip().strip('`"[]').lower())
        else:
            # Aggregates and expressions: compare their text, so COUNT(x) and
            # COUNT(y) stay distinct while COUNT(x) matches COUNT(x).
            out.append(re.sub(r"\s+", "", inner.sql(dialect="sqlite")).lower())
    return tuple(out) if out else None


def _permutation_key(signature: Sequence[tuple]) -> tuple:
    """Order-insensitive form of a result signature.

    A signature is a sorted tuple of rows, each row a tuple of one value per
    projected column *in projection order*. Sorting within the row erases that
    order, so two permutations of the same result collapse to one key.
    """
    return tuple(sorted(tuple(sorted(row)) for row in signature))


def merge_permuted(
    clusters: dict[tuple, list[int]],
    columns_by_index: dict[int, Optional[tuple[str, ...]]],
) -> dict[tuple, list[int]]:
    """Fold clusters that differ only in projected column order.

    Members must project the same column *set*; a cluster whose projection could
    not be parsed is left alone. The surviving key is the signature of the
    lowest-indexed member, so ``len(sig) == 0`` still means "empty result" for
    every caller that reads cluster keys that way.
    """
    groups: dict[tuple, list[tuple[tuple, list[int]]]] = {}
    passthrough: dict[tuple, list[int]] = {}

    for sig, idxs in clusters.items():
        cols = {columns_by_index.get(i) for i in idxs}
        # One parse failure, or members that disagree about their own column
        # list, means there is nothing trustworthy to compare.
        if len(cols) != 1 or None in cols:
            passthrough[sig] = list(idxs)
            continue
        column_set = frozenset(next(iter(cols)) or ())
        groups.setdefault((_permutation_key(sig), column_set), []).append(
            (sig, list(idxs))
        )

    merged: dict[tuple, list[int]] = dict(passthrough)
    for members in groups.values():
        if len(members) == 1:
            sig, idxs = members[0]
            merged[sig] = idxs
            continue
        best_sig, _ = min(members, key=lambda m: min(m[1]))
        union = sorted(i for _sig, idxs in members for i in idxs)
        merged[best_sig] = union
        logger.info(
            "projection_order: merged %d permuted cluster(s) into one, slots %s",
            len(members),
            union,
        )
    return merged


def _mention_position(column: str, text: str) -> Optional[int]:
    """Where *column* is first named in *text*, or ``None``.

    Matching is word-prefix based in both directions so ``Ext`` finds
    "extension" and ``GSserved`` is not mistaken for "served" alone.
    """
    if not column or not text:
        return None
    target = re.sub(r"[^a-z0-9]", "", column.lower())
    if not target:
        return None
    for match in _WORD.finditer(text):
        word = match.group(0).lower()
        if word == target:
            return match.start()
        if (
            len(word) >= 3
            and len(target) >= 3
            and (word.startswith(target) or target.startswith(word))
        ):
            return match.start()
    return None


def _desired_order(
    columns: Iterable[str], evidence: str, question: str
) -> Optional[tuple[str, ...]]:
    """The projection order implied by the evidence, else by the question."""
    for text in (evidence or "", question or ""):
        positions = {c: _mention_position(c, text) for c in columns}
        found = {c: p for c, p in positions.items() if p is not None}
        # Every column must be located, or the ordering is only partly known and
        # would rank the unlocated ones arbitrarily.
        if len(found) == len(positions) and len(found) > 1:
            return tuple(sorted(found, key=lambda c: found[c]))
    return None


def preferred_index(
    candidate_indices: Sequence[int],
    columns_by_index: dict[int, Optional[tuple[str, ...]]],
    evidence: str,
    question: str,
) -> Optional[int]:
    """Index within *candidate_indices* whose projection order is preferred.

    ``None`` when the members do not disagree about order, or when the question
    and evidence give no order to follow — in which case the caller keeps its
    existing tie-break rather than inventing one.
    """
    orders = {i: columns_by_index.get(i) for i in candidate_indices}
    usable = {i: cols for i, cols in orders.items() if cols}
    if len(set(usable.values())) < 2:
        return None

    columns = set(next(iter(usable.values())))
    desired = _desired_order(columns, evidence, question)
    if not desired:
        return None
    for i in sorted(usable):
        if tuple(usable[i]) == desired:
            logger.info(
                "projection_order: preferring slot %d, projection %s matches %s",
                i,
                list(usable[i]),
                "evidence/question order",
            )
            return i
    return None
