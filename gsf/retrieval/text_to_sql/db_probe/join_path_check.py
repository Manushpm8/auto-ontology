# SPDX-FileCopyrightText: Copyright (c) 2026, NVIDIA CORPORATION & AFFILIATES.
# All rights reserved.
# SPDX-License-Identifier: Apache-2.0

"""Join-path verification for the repair path.

Sibling of ``literal_check.py``/``jsonb_path_check.py``: those catch a
hallucinated filter value or JSONB key; this catches a hallucinated **join
column** — an ``ON a.col = b.col`` predicate between two tables that aren't
actually related that way. Unlike a bad literal or JSONB key, a fabricated
join is syntactically and semantically valid SQL (both columns exist, types
match) and returns rows, so nothing upstream (parse validation, execution)
ever errors on it — it just silently returns the wrong data.

Ground truth here is the same ``SEMANTIC_FK`` graph already used to build
``attribute_join_paths`` for SQL generation (see
``gsf/dal/attributes.py::find_join_path``), queried directly against the
*actual* join predicates the model wrote, not just the columns it was told
about up front. Three verdicts:

- a **different**, real join path connects the same two tables (either a
  direct edge on different columns, or only via intermediate bridge
  table(s)) — repairable: return the correct hops so reconstruction can be
  told exactly what to use, mirroring the "KNOWN JOIN KEYS" mechanism.
- both columns share a **hub**: no forward path either way, but each holds
  its own real, PK-anchored ``SEMANTIC_FK`` edge to the same attribute (see
  ``find_shared_hub_bridge``) — also repairable, routed through the hub.
- **no** path or shared hub is known in the graph at all. This does not, on
  its own, mean the join is wrong — the graph can be incomplete for
  relationships ingestion never resolved. A live value-overlap probe (same
  idea as ``semantic_fk.py``'s ingestion-time sample-value fallback) is used
  as a secondary, confirmatory signal: near-zero overlap between the two
  join columns' actual values is treated as a fabricated join; substantial
  overlap is treated as "probably a real, just-unmodeled relationship" and
  left alone, to avoid flagging legitimate business joins the graph was
  never meant to cover.

Only equality predicates inside a ``JOIN ... ON`` clause are considered, and
only when at least one side is a column already known to the FK graph in
some capacity (see ``column_participates_in_semantic_fk``) — this keeps the
check scoped to the FK-shaped join-hallucination failure mode it targets,
not arbitrary business-logic joins (date ranges, status matches) the graph
was never meant to model.
"""

from __future__ import annotations

import logging
from typing import Any, Optional

import sqlglot
from sqlglot import exp

from gsf.dal.attributes import (
    column_participates_in_semantic_fk,
    find_column_id_by_table_and_name,
    find_join_path,
    find_shared_hub_bridge,
)
from gsf.retrieval.text_to_sql.db_probe.executor import ProbeExecutor
from gsf.retrieval.text_to_sql.db_probe.literal_check import (
    _as_column,
    _candidate_tables,
    _sqlglot_dialect,
    _table_nodes,
)

logger = logging.getLogger(__name__)

# How much value-overlap between two join columns counts as "probably a real
# relationship, just not modeled in the graph" — below this, treat the join
# as fabricated. Deliberately generous: this only fires when the graph has
# nothing to say either way, so a false "leave it alone" is far cheaper than
# a false "flag a legitimate join".
_OVERLAP_KEEP_THRESHOLD = 0.2


def _join_equalities(tree: exp.Expression) -> list[tuple[exp.Column, exp.Column]]:
    """Column-to-column equality predicates found inside a ``JOIN ... ON`` clause.

    Only predicates where *both* sides are bare columns are considered — a
    join condition compares two identity columns, not a column against a
    literal or an expression (those are filters, not join topology).
    """
    out: list[tuple[exp.Column, exp.Column]] = []
    for join in tree.find_all(exp.Join):
        on = join.args.get("on")
        if on is None:
            continue
        for eq in on.find_all(exp.EQ):
            left = _as_column(eq.this)
            right = _as_column(eq.expression)
            if left is not None and right is not None:
                out.append((left, right))
    return out


def _resolve_table_name(
    col: exp.Column, all_nodes: list[exp.Table], by_key: dict[str, exp.Table]
) -> Optional[str]:
    """The real (unaliased) table name a column belongs to, per the SQL's own aliasing."""
    candidates = _candidate_tables(col, all_nodes, by_key)
    if len(candidates) == 1:
        return candidates[0].name
    return None


def _value_overlap(
    executor: ProbeExecutor,
    dialect: Optional[str],
    table_a: str,
    col_a: str,
    table_b: str,
    col_b: str,
) -> Optional[float]:
    """Fraction of *table_a*'s distinct ``col_a`` values also present in *table_b*'s ``col_b``.

    ``None`` when the probe couldn't run (no connector, budget exhausted,
    query failure) — callers must treat that as "can't confirm", not as a
    verdict either way.
    """
    d = _sqlglot_dialect(dialect)
    a_ref = exp.column(col_a).sql(dialect=d)
    b_ref = exp.column(col_b).sql(dialect=d)
    a_table_ref = exp.table_(table_a).sql(dialect=d)
    b_table_ref = exp.table_(table_b).sql(dialect=d)
    sql = (
        f"SELECT "
        f"COUNT(DISTINCT a.{a_ref}) AS total, "
        f"COUNT(DISTINCT CASE WHEN b.{b_ref} IS NOT NULL THEN a.{a_ref} END) AS matched "
        f"FROM {a_table_ref} a "
        f"LEFT JOIN {b_table_ref} b ON a.{a_ref} = b.{b_ref} "
        f"WHERE a.{a_ref} IS NOT NULL"
    )
    res = executor.run(sql, purpose="join_path_check_overlap")
    if not res["ok"] or not res["rows"]:
        return None
    row = res["rows"][0]
    total = row.get("total") or 0
    matched = row.get("matched") or 0
    if not total:
        return None
    return matched / total


def find_join_path_mismatches(
    executor: ProbeExecutor,
    dialect: Optional[str],
    sql: str,
    database_name: Optional[str],
) -> list[dict[str, Any]]:
    """Return join predicates that don't correspond to a known FK relationship.

    Each entry: ``{"table_a", "col_a", "table_b", "col_b", "verdict", "hops",
    "bridge_tables"}``. ``verdict`` is one of:

    - ``"wrong_column"``: same two tables, a different real edge exists — use
      ``hops[0]`` instead.
    - ``"missing_bridge"``: these two tables should not be joined directly at
      all — route through ``hops`` (which cross ``bridge_tables``).
    - ``"unverified"``: no known path either way, and (when a live probe was
      possible) value overlap was low — likely fabricated, but there is no
      known repair; ``hops``/``bridge_tables`` are empty.

    Empty list means every checked predicate matched a known edge, or (when
    the graph had nothing to say and no probe could run) nothing could be
    confirmed as wrong — never treated as "the whole query is fine" beyond
    what was actually checked.
    """
    try:
        tree = sqlglot.parse_one(sql, read=_sqlglot_dialect(dialect))
    except Exception as exc:  # noqa: BLE001 — never break the pipeline on a parse error
        logger.info("join_path_check: could not parse SQL (%s)", exc)
        return []
    if tree is None:
        return []

    all_nodes, by_key = _table_nodes(tree)
    if not all_nodes:
        return []

    mismatches: list[dict[str, Any]] = []
    checked_pairs: set[tuple[str, str, str, str]] = set()

    for left, right in _join_equalities(tree):
        table_a = _resolve_table_name(left, all_nodes, by_key)
        table_b = _resolve_table_name(right, all_nodes, by_key)
        if not table_a or not table_b or table_a.lower() == table_b.lower():
            continue  # self-join or unresolvable alias — not this check's job

        pair_key = tuple(
            sorted(
                [
                    (table_a.lower(), left.name.lower()),
                    (table_b.lower(), right.name.lower()),
                ]
            )
        )
        flat_key = (pair_key[0][0], pair_key[0][1], pair_key[1][0], pair_key[1][1])
        if flat_key in checked_pairs:
            continue
        checked_pairs.add(flat_key)

        col_a_id = find_column_id_by_table_and_name(table_a, left.name, database_name)
        col_b_id = find_column_id_by_table_and_name(table_b, right.name, database_name)
        if not col_a_id or not col_b_id:
            continue  # can't resolve — never flag on missing information

        if not (
            column_participates_in_semantic_fk(col_a_id)
            or column_participates_in_semantic_fk(col_b_id)
        ):
            continue  # neither side is FK-shaped — out of scope for this check

        hops = find_join_path(col_a_id, col_b_id)

        if len(hops) == 1:
            hop = hops[0]
            hop_pair = {
                (hop["source_table"].lower(), hop["source_column"].lower()),
                (hop["target_table"].lower(), hop["target_column"].lower()),
            }
            written_pair = {
                (table_a.lower(), left.name.lower()),
                (table_b.lower(), right.name.lower()),
            }
            if hop_pair == written_pair:
                continue  # verified — exactly what the graph says
            mismatches.append(
                {
                    "table_a": table_a,
                    "col_a": left.name,
                    "table_b": table_b,
                    "col_b": right.name,
                    "verdict": "wrong_column",
                    "hops": [hop],
                    "bridge_tables": [],
                }
            )
            continue

        if len(hops) > 1:
            bridge_tables = sorted(
                {h[side] for h in hops for side in ("source_table", "target_table")}
                - {table_a, table_b}
            )
            mismatches.append(
                {
                    "table_a": table_a,
                    "col_a": left.name,
                    "table_b": table_b,
                    "col_b": right.name,
                    "verdict": "missing_bridge",
                    "hops": hops,
                    "bridge_tables": bridge_tables,
                }
            )
            continue

        # No path at all via the forward-only traversal. Before falling
        # back to a live probe, check for a shared-identity hub: two
        # columns can each hold a real, PK-anchored forward SEMANTIC_FK to
        # the same attribute without either being reachable from the
        # other (find_join_path's guard against fabricating a join between
        # two coincidentally-shared-target columns also hides this
        # legitimate case). Trusted at the same confidence as the
        # multi-hop `missing_bridge` branch above — both sides already
        # have a real, ingested FK edge, so no extra probe is needed here.
        hub = find_shared_hub_bridge(col_a_id, col_b_id)
        if hub.get("hub_table") and hub.get("hub_column"):
            mismatches.append(
                {
                    "table_a": table_a,
                    "col_a": left.name,
                    "table_b": table_b,
                    "col_b": right.name,
                    "verdict": "missing_bridge",
                    "hops": [
                        {
                            "source_schema": "",
                            "source_table": table_a,
                            "source_column": left.name,
                            "target_schema": "",
                            "target_table": hub["hub_table"],
                            "target_column": hub["hub_column"],
                        },
                        {
                            "source_schema": "",
                            "source_table": table_b,
                            "source_column": right.name,
                            "target_schema": "",
                            "target_table": hub["hub_table"],
                            "target_column": hub["hub_column"],
                        },
                    ],
                    "bridge_tables": [hub["hub_table"]],
                }
            )
            continue

        # No shared hub either — the graph has nothing to confirm or deny
        # this join. Fall back to a live value-overlap probe as a
        # secondary signal before flagging, to avoid false-positiving on a
        # real relationship the graph never happened to model at all.
        overlap = None
        if executor.budget_left:
            overlap = _value_overlap(
                executor, dialect, table_a, left.name, table_b, right.name
            )
            if overlap is None:
                overlap = _value_overlap(
                    executor, dialect, table_b, right.name, table_a, left.name
                )
        if overlap is not None and overlap >= _OVERLAP_KEEP_THRESHOLD:
            logger.info(
                "join_path_check: no graph edge for %s.%s = %s.%s, but live "
                "overlap %.2f >= threshold — treating as a real, unmodeled "
                "relationship and leaving it alone",
                table_a,
                left.name,
                table_b,
                right.name,
                overlap,
            )
            continue

        mismatches.append(
            {
                "table_a": table_a,
                "col_a": left.name,
                "table_b": table_b,
                "col_b": right.name,
                "verdict": "unverified",
                "hops": [],
                "bridge_tables": [],
                "overlap": overlap,
            }
        )

    return mismatches


def build_join_path_repair_error(mismatches: list[dict[str, Any]]) -> str:
    """Render mismatches into a targeted reconstruction instruction."""
    lines = []
    for m in mismatches:
        written = f"{m['table_a']}.{m['col_a']} = {m['table_b']}.{m['col_b']}"
        if m["verdict"] == "wrong_column":
            hop = m["hops"][0]
            correct = (
                f"{hop['source_table']}.{hop['source_column']} = "
                f"{hop['target_table']}.{hop['target_column']}"
            )
            lines.append(
                f"- You joined {written}, but the real relationship between "
                f"these tables uses {correct}. Use the correct column pair."
            )
        elif m["verdict"] == "missing_bridge":
            chain = " -> ".join(
                f"{h['source_table']}.{h['source_column']} = "
                f"{h['target_table']}.{h['target_column']}"
                for h in m["hops"]
            )
            lines.append(
                f"- You joined {written} directly, but these two tables are "
                f"not directly related. Route the join through "
                f"{', '.join(m['bridge_tables'])} instead, using: {chain}."
            )
        else:
            lines.append(
                f"- You joined {written}, but no relationship between these "
                f"columns could be verified and their actual values barely "
                f"overlap. This join is very likely wrong — find the correct "
                f"way to connect these tables, adding any table needed."
            )
    body = "\n".join(lines)
    return (
        "One or more JOIN conditions in the generated SQL do not match a "
        "real relationship between the tables, so the query silently joins "
        "the wrong rows instead of erroring:\n"
        f"{body}\n\n"
        "Rewrite the SQL using the correct join condition(s) above. Change "
        "ONLY the mismatched join(s); keep all other joins, columns, "
        "grouping, and filters exactly as they are."
    )


__all__ = [
    "find_join_path_mismatches",
    "build_join_path_repair_error",
]
