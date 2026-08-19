# SPDX-FileCopyrightText: Copyright (c) 2026, NVIDIA CORPORATION & AFFILIATES.
# All rights reserved.
# SPDX-License-Identifier: Apache-2.0

"""Proactive (pre-execution) join-path check.

Sibling of ``jsonb_path_check.py``, checking ``JOIN ... ON`` predicates
against the same ``SEMANTIC_FK`` graph that seeds ``attribute_join_paths``,
instead of checking JSONB key paths. A hallucinated join between two
plausible-looking identifier columns (e.g. ``actuation_data.actrecref =
robot_details.botdetreg`` when the real relationship routes through
``robot_record``) is syntactically and semantically valid SQL — it executes
and returns rows — so nothing else in the pipeline catches it: syntax
validation only checks the SQL parses, and ``validate_intent`` reasons over
the same join-path data the generator had, so it shares the same blind spot
when that data is what led the model astray in the first place.

Gated by ``DB_PROBE_JOIN_PATH_CHECK`` since, when the graph has no edge for a
predicate at all, this falls back to a live value-overlap probe (a couple of
extra DB round-trips). Runs at most ``_MAX_REPAIR_ATTEMPTS`` times per
request — unlike the JSONB check's single-shot guard, a join fix sometimes
needs a second pass (the model can apply a partial fix, e.g. correcting one
predicate but not the table set), but an unbounded loop risks repeating the
same wrong join forever if reconstruction doesn't converge.
"""

from __future__ import annotations

from typing import Any, Dict

from gsf.dal.attributes import find_table_id_by_name
from gsf.dal.datasources import fetch_tables_by_ids
from gsf.retrieval.text_to_sql.agents.empty_like_result_check import _get_sql_code
from gsf.retrieval.text_to_sql.base import BaseAgent
from gsf.retrieval.text_to_sql.connector_routing import resolve_connector_from_tables
from gsf.retrieval.text_to_sql.db_probe.executor import ProbeExecutor
from gsf.retrieval.text_to_sql.db_probe.join_path_check import (
    build_join_path_repair_error,
    find_join_path_mismatches,
)
from gsf.retrieval.text_to_sql.state import AgentState

_MAX_REPAIR_ATTEMPTS = 2


class JoinPathCheckAgent(BaseAgent):
    """Check JOIN predicates against the semantic FK graph before executing."""

    def __init__(self) -> None:
        super().__init__("join_path_check")

    def validate_input(self, state: AgentState) -> bool:
        return True

    def execute(self, state: AgentState) -> Dict[str, Any]:
        path_state = dict(state.get("path_state", {}))
        sql_code = _get_sql_code(path_state)

        attempts = path_state.get("join_path_repair_attempts", 0)
        if not sql_code.strip() or attempts >= _MAX_REPAIR_ATTEMPTS:
            return {"decision": "valid_sql", "path_state": path_state}

        connectors = state.get("connectors") or []
        relevant_tables = list(path_state.get("relevant_tables") or [])
        connector = resolve_connector_from_tables(relevant_tables, connectors)
        dialect = getattr(connector, "dialect", None)
        database_name = path_state.get("target_db")

        with ProbeExecutor(connector) as executor:
            mismatches = find_join_path_mismatches(
                executor, dialect, sql_code, database_name
            )

        if not mismatches:
            return {"decision": "valid_sql", "path_state": path_state}

        path_state["join_path_repair_attempts"] = attempts + 1

        repairable = [m for m in mismatches if m["verdict"] != "unverified"]
        if repairable:
            # We already know the exact fix from the graph (either the real
            # column pair, or the bridge table(s) plus the join keys through
            # them) — merge it in ourselves rather than making reconstruction
            # re-discover it via its own LLM-driven search.
            new_hops = [m["hops"] for m in repairable]
            attribute_join_paths = list(path_state.get("attribute_join_paths") or [])
            attribute_join_paths.extend({"path": hops} for hops in new_hops)
            path_state["attribute_join_paths"] = attribute_join_paths

            known_names = {(t.get("name") or "").lower() for t in relevant_tables}
            missing_names = {
                name
                for m in repairable
                for name in m["bridge_tables"]
                if name.lower() not in known_names
            }
            self._merge_missing_bridge_tables(
                path_state, relevant_tables, repairable, missing_names, database_name
            )

        self.logger.info(
            "Join path check — routing to reconstruction to fix %d join(s): %s",
            len(mismatches),
            [
                f"{m['table_a']}.{m['col_a']} = {m['table_b']}.{m['col_b']} "
                f"({m['verdict']})"
                for m in mismatches
            ],
        )
        path_state["error"] = build_join_path_repair_error(mismatches)
        if repairable and len(repairable) == len(mismatches):
            # Every mismatch has a known, already-merged-in fix — this can
            # never be a missing_data situation from reconstruction's own
            # point of view (see sql_reconstruction.py's error_known_fixable
            # handling): the tables/join keys it would otherwise have to go
            # searching for are already sitting in relevant_tables and
            # attribute_join_paths by the time it runs.
            path_state["error_known_fixable"] = True
        # else: at least one "unverified" mismatch with no known repair —
        # leave error_known_fixable unset so reconstruction's normal LLM
        # error-classification (and its own VDB table-discovery fallback)
        # gets a chance to find something this check couldn't.

        return {"decision": "invalid_sql", "path_state": path_state}

    @staticmethod
    def _merge_missing_bridge_tables(
        path_state: Dict[str, Any],
        relevant_tables: list[dict],
        repairable: list[dict],
        missing_names: set[str],
        database_name: str | None,
    ) -> None:
        """Fetch and merge bridge tables named in *repairable* but not yet in relevant_tables."""
        if not missing_names:
            path_state["relevant_tables"] = relevant_tables
            return

        # find_join_path's hop dicts carry table *names* only, not ids, so
        # resolve each bridge table name back to an id — scoped to
        # database_name to avoid matching a same-named table in a different
        # co-resident BIRD database (see find_table_id_by_name).
        ids: list[str] = []
        for name in missing_names:
            table_id = find_table_id_by_name(name, database_name)
            if table_id:
                ids.append(table_id)

        if not ids:
            path_state["relevant_tables"] = relevant_tables
            return

        bridge_tables = fetch_tables_by_ids(ids)
        existing_ids = {t.get("id") for t in relevant_tables}
        for tbl in bridge_tables:
            if tbl.get("id") not in existing_ids:
                relevant_tables.append(tbl)
                existing_ids.add(tbl.get("id"))
        path_state["relevant_tables"] = relevant_tables


__all__ = ["JoinPathCheckAgent"]
