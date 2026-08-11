"""Oracle column pruning — a DIAGNOSTIC ONLY measurement of context noise.

Keeps every column the gold query references and throws away most of the rest,
so a run can be scored against the same run at full width. Because the gold
query decides what survives, this **cannot ship**: any EX it produces is an
upper bound on what a real, gold-blind pruner could reach, not a gain.

Why measure it. A v15 prompt renders ~58 columns and the gold answer uses ~6 of
them — an 11% median signal ratio, inside a 37k-character user prompt. Nothing
we have tested says whether that dilution costs accuracy. Pruning to gold plus a
handful of distractors lifts signal to ~55% while leaving every fact needed to
answer in place, so the arm varies noise and holds information constant. A null
result rules context volume out and closes off pruning, tighter retrieval and
smaller schemas in one run; a positive result sizes the prize for building a
real pruner.

Enabled by ``BIRD_ORACLE_PRUNE_COLS=<n>``: keep gold columns plus *n* random
non-gold columns per table. Unset disables it entirely.
"""

from __future__ import annotations

import logging
import os
import random
import re
from typing import Any

logger = logging.getLogger(__name__)

_WARNED = False


def oracle_prune_enabled() -> bool:
    """Whether oracle pruning is on. Read per call so tests can toggle it."""
    raw = os.environ.get("BIRD_ORACLE_PRUNE_COLS", "").strip()
    if not raw:
        return False
    try:
        return int(raw) >= 0
    except ValueError:
        return False


def _keep_distractors() -> int:
    try:
        return max(0, int(os.environ.get("BIRD_ORACLE_PRUNE_COLS", "5").strip()))
    except ValueError:
        return 5


def _warn_once() -> None:
    """Say loudly that the run is contaminated, once per process."""
    global _WARNED
    if _WARNED:
        return
    _WARNED = True
    logger.warning(
        "BIRD_ORACLE_PRUNE_COLS is set: the schema shown to the model is being "
        "filtered USING THE GOLD QUERY. This run is a diagnostic ceiling and its "
        "EX is NOT a legitimate score. Unset the variable for real runs."
    )


def gold_column_names(gold_sql: str) -> set[str]:
    """Lowercased identifiers appearing in *gold_sql*.

    Deliberately loose: matching is later done per column name against this text,
    so quoted names with spaces (``\u0060Charter School (Y/N)\u0060``) and bare
    identifiers are both handled by the caller's word-boundary search.
    """
    return {t.lower() for t in re.findall(r"[A-Za-z_][A-Za-z_0-9]*", gold_sql or "")}


def _mentions(name: str, gold_lower: str) -> bool:
    """Whether *name* occurs in the gold text as a whole identifier.

    Word boundaries rather than ``in``, so a column called ``id`` does not match
    inside ``CustomerID``. Column names containing spaces or punctuation match on
    their quoted form, which this same pattern covers.
    """
    if not name:
        return False
    return re.search(
        r"(?<![A-Za-z0-9_])" + re.escape(name.lower()) + r"(?![A-Za-z0-9_])",
        gold_lower,
    ) is not None


def prune_tables_to_gold(
    tables: list[dict],
    gold_sql: str,
    keep_distractors: int | None = None,
    seed: int = 0,
) -> list[dict]:
    """Return *tables* with non-gold columns thinned to *keep_distractors* each.

    Tables are all kept, including ones gold never reads: the arm varies column
    noise only, so removing tables too would confound it with the table-retrieval
    question that scripts/info_availability_audit.py already answered.

    Distractors are sampled deterministically from *seed* so a rerun of the same
    question sees the same schema.
    """
    if not tables or not gold_sql:
        return tables
    _warn_once()
    n_keep = _keep_distractors() if keep_distractors is None else keep_distractors
    gold_lower = (gold_sql or "").lower()
    rng = random.Random(seed)

    pruned: list[dict] = []
    for table in tables:
        cols = table.get("columns")
        if not isinstance(cols, list) or not cols:
            pruned.append(table)
            continue

        def _name(c: Any) -> str:
            return str(c.get("name", "") if isinstance(c, dict) else c)

        keep = [c for c in cols if _mentions(_name(c), gold_lower)]
        rest = [c for c in cols if c not in keep]
        if n_keep and rest:
            keep = keep + rng.sample(rest, min(n_keep, len(rest)))

        # Original order, so pruning cannot be confused with reordering.
        order = {id(c): i for i, c in enumerate(cols)}
        keep.sort(key=lambda c: order.get(id(c), 0))

        new_table = dict(table)
        new_table["columns"] = keep
        pruned.append(new_table)
    return pruned
