# SPDX-FileCopyrightText: Copyright (c) 2026, NVIDIA CORPORATION & AFFILIATES.
# All rights reserved.
# SPDX-License-Identifier: Apache-2.0

"""Proactive (pre-execution) JSONB key-path check.

Sibling of ``proactive_value_check.py``, checking ``->``/``->>`` JSONB key
paths instead of filter literals. Runs before execution so it also catches
paths that would return non-empty-but-wrong-because-NULL columns, not just
paths that happen to empty out the whole result. Gated by
``DB_PROBE_JSONB_PATH_CHECK`` since it costs a few cheap probes on every query
that navigates JSONB. Runs at most once per request (guarded) to avoid
reconstruction loops.
"""

from __future__ import annotations

from typing import Any, Dict

from gsf.retrieval.text_to_sql.agents.empty_like_result_check import _get_sql_code
from gsf.retrieval.text_to_sql.base import BaseAgent
from gsf.retrieval.text_to_sql.connector_routing import resolve_connector_from_tables
from gsf.retrieval.text_to_sql.db_probe.executor import ProbeExecutor
from gsf.retrieval.text_to_sql.db_probe.jsonb_path_check import (
    build_jsonb_path_repair_error,
    find_jsonb_path_mismatches,
)
from gsf.retrieval.text_to_sql.state import AgentState


class JsonbPathCheckAgent(BaseAgent):
    """Check JSONB key paths against real DB keys before executing the query."""

    def __init__(self) -> None:
        super().__init__("jsonb_path_check")

    def validate_input(self, state: AgentState) -> bool:
        return True

    def execute(self, state: AgentState) -> Dict[str, Any]:
        path_state = dict(state.get("path_state", {}))
        sql_code = _get_sql_code(path_state)

        # One-shot: never re-check after a repair, so we can't loop here.
        if not sql_code.strip() or path_state.get("jsonb_path_repair_attempted"):
            return {"decision": "valid_sql", "path_state": path_state}

        connectors = state.get("connectors") or []
        relevant_tables = list(path_state.get("relevant_tables") or [])
        connector = resolve_connector_from_tables(relevant_tables, connectors)
        dialect = getattr(connector, "dialect", None)

        # Cheap, already-fetched — no extra DB/graph round-trip. Lets the
        # check distinguish "wrong key" from "not a JSON column at all" when
        # the live probe itself can't tell (see db_probe/jsonb_path_check.py).
        known_types: dict[tuple[str, str], str] = {}
        for table in relevant_tables:
            table_name = table.get("name")
            if not table_name:
                continue
            for col in table.get("columns") or []:
                if not isinstance(col, dict):
                    continue
                col_name = col.get("name")
                data_type = col.get("data_type")
                if col_name and data_type:
                    known_types[(table_name.lower(), col_name.lower())] = data_type

        with ProbeExecutor(connector) as executor:
            mismatches = find_jsonb_path_mismatches(
                executor, dialect, sql_code, known_types=known_types
            )

        if not mismatches:
            return {"decision": "valid_sql", "path_state": path_state}

        path_state["jsonb_path_repair_attempted"] = True
        path_state["error"] = build_jsonb_path_repair_error(mismatches)
        # This error was derived from a live probe against the query's own
        # already-joined tables — the fix is always "use the real key/
        # container we just found there," never "go search for a new
        # table." Skip reconstruction's LLM error-classification for it
        # (see sql_reconstruction.py) so it can't be misread as missing_data.
        path_state["error_known_fixable"] = True
        self.logger.info(
            "JSONB path check — routing to reconstruction to fix %d path(s): %s",
            len(mismatches),
            [
                f"{m['table']}.{m['column']}->'{m['container']}'->>'{m['used_key']}'"
                if m["container"]
                else f"{m['table']}.{m['column']}->>'{m['used_key']}'"
                for m in mismatches
            ],
        )
        return {"decision": "invalid_sql", "path_state": path_state}


__all__ = ["JsonbPathCheckAgent"]
