# SPDX-FileCopyrightText: Copyright (c) 2026, NVIDIA CORPORATION & AFFILIATES.
# All rights reserved.
# SPDX-License-Identifier: Apache-2.0

"""Post-execution result repair.

Runs right after execution. When the query came back empty it checks the filter
literals against the columns' actual distinct values and, when a literal is a
near-miss of a real value (wrong case / spelling), routes once to the existing
reconstruction node with a targeted hint. Failing that it probes the joins, since
a join whose two sides share no values empties the result no matter how sound the
filters are. The date-parse check runs either way, because a date that fails to
parse yields a result full of NULLs at least as often as an empty one. When the
query returned rows it also looks for:

* a singular superlative question that returned more than one row (missing
  ``LIMIT 1`` / uncollapsed duplicates);
* a column that is 0/NULL in every row beside populated ones, which signals an
  aggregate whose rows were filtered away.

Every check routes at most once, and the non-empty path costs a single scan —
no probes, no LLM call.
"""

from __future__ import annotations

from typing import Any, Dict

from gsf.retrieval.text_to_sql.agents.empty_like_result_check import (
    _get_sql_code,
    _is_empty_db_result,
)
from gsf.retrieval.text_to_sql.base import BaseAgent
from gsf.retrieval.text_to_sql.connector_routing import resolve_connector_from_tables
from gsf.retrieval.text_to_sql.db_probe.date_parse_check import (
    build_date_repair_error,
    find_date_faults,
)
from gsf.retrieval.text_to_sql.db_probe.executor import ProbeExecutor
from gsf.retrieval.text_to_sql.db_probe.join_overlap_check import (
    build_join_repair_error,
    find_empty_joins,
)
from gsf.retrieval.text_to_sql.db_probe.literal_check import (
    build_value_repair_error,
    find_literal_mismatches,
)
from gsf.retrieval.text_to_sql.agents.result_health import (
    build_dead_column_error,
    build_superlative_cardinality_error,
    count_result_rows,
    find_dead_result_columns,
    has_exact_duplicate_rows,
    should_repair_superlative_cardinality,
)
from gsf.retrieval.text_to_sql.state import AgentState, get_question_for_processing



class EmptyResultValueRepairAgent(BaseAgent):
    """Detect filter literals that don't exist in the DB and route to repair."""

    def __init__(self) -> None:
        super().__init__("empty_result_value_repair")

    def validate_input(self, state: AgentState) -> bool:
        path_state = state.get("path_state", {})
        if path_state.get("sql_response_from_db") is None:
            self.logger.warning("No SQL execution result found for value repair")
            return False
        return True

    def _check_superlative_cardinality(
        self,
        state: AgentState,
        path_state: Dict[str, Any],
        db_result: Any,
        sql_code: str,
    ) -> Dict[str, Any] | None:
        if path_state.get("superlative_cardinality_repair_attempted"):
            self.logger.info(
                "Superlative-cardinality repair already attempted — continuing"
            )
            return None

        question = get_question_for_processing(state)
        if not should_repair_superlative_cardinality(question, db_result, sql_code):
            return None

        n_rows = count_result_rows(db_result)
        duplicates = has_exact_duplicate_rows(db_result)
        path_state["superlative_cardinality_repair_attempted"] = True
        path_state["error"] = build_superlative_cardinality_error(
            n_rows, has_duplicates=duplicates
        )
        self.logger.info(
            "Singular superlative returned %d row(s)%s — routing to reconstruction",
            n_rows,
            " with exact duplicates" if duplicates else "",
        )
        return {"decision": "invalid_sql", "path_state": path_state}

    def _check_dead_columns(
        self, path_state: Dict[str, Any], db_result: Any
    ) -> Dict[str, Any]:
        dead_columns = find_dead_result_columns(db_result)
        if not dead_columns:
            return {"decision": "valid_sql", "path_state": path_state}

        if path_state.get("dead_column_repair_attempted"):
            self.logger.info(
                "Dead-column repair already attempted — passing through result"
            )
            return {"decision": "valid_sql", "path_state": path_state}

        path_state["dead_column_repair_attempted"] = True
        path_state["error"] = build_dead_column_error(dead_columns)
        self.logger.info(
            "Result has %d all-zero/NULL column(s) beside populated ones — "
            "routing to reconstruction: %s",
            len(dead_columns),
            dead_columns,
        )
        return {"decision": "invalid_sql", "path_state": path_state}

    def _route_broken_dates(
        self, path_state: Dict[str, Any], broken_dates: list
    ) -> Dict[str, Any]:
        path_state["value_repair_attempted"] = True
        path_state["error"] = build_date_repair_error(broken_dates)
        self.logger.info(
            "Routing to reconstruction to fix %d date fault(s): %s",
            len(broken_dates),
            [f"{f['kind']} on {f['table']}.{f['column']}" for f in broken_dates],
        )
        return {"decision": "invalid_sql", "path_state": path_state}

    def execute(self, state: AgentState) -> Dict[str, Any]:
        path_state = dict(state.get("path_state", {}))
        sql_code = _get_sql_code(path_state)
        db_result = path_state.get("sql_response_from_db")

        empty = _is_empty_db_result(db_result)
        already_tried = bool(path_state.get("value_repair_attempted"))

        connectors = state.get("connectors") or []
        relevant_tables = list(path_state.get("relevant_tables") or [])
        connector = resolve_connector_from_tables(relevant_tables, connectors)
        dialect = getattr(connector, "dialect", None)

        # A broken date parse is provably wrong whether or not rows came back:
        # its usual symptom is a full result set of NULLs, not an empty one. The
        # scan costs nothing on a query without date arithmetic, since it probes
        # only once it finds such an expression in the SQL.
        if not empty:
            if not already_tried:
                with ProbeExecutor(connector) as executor:
                    broken_dates = find_date_faults(executor, dialect, sql_code)
                if broken_dates:
                    return self._route_broken_dates(path_state, broken_dates)
            cardinality_repair = self._check_superlative_cardinality(
                state, path_state, db_result, sql_code
            )
            if cardinality_repair is not None:
                return cardinality_repair
            return self._check_dead_columns(path_state, db_result)

        if already_tried:
            self.logger.info("Value repair already attempted — passing through")
            return {"decision": "valid_sql", "path_state": path_state}

        # A bad literal is the cheaper and more common cause, so look for that
        # first and only probe the date derivations when the literals are clean.
        # An unmatched join is checked last: it is the most expensive probe and
        # the rarest cause, but it is the only one that explains an empty result
        # whose filters and dates are all sound.
        with ProbeExecutor(connector) as executor:
            mismatches = find_literal_mismatches(executor, dialect, sql_code)
            broken_dates = (
                [] if mismatches else find_date_faults(executor, dialect, sql_code)
            )
            empty_joins = (
                []
                if mismatches or broken_dates
                else find_empty_joins(executor, dialect, sql_code)
            )

        if mismatches:
            path_state["value_repair_attempted"] = True
            path_state["error"] = build_value_repair_error(mismatches)
            self.logger.info(
                "Empty result — routing to reconstruction to fix %d literal(s): %s",
                len(mismatches),
                [f"{m['table']}.{m['column']}='{m['used']}'" for m in mismatches],
            )
            return {"decision": "invalid_sql", "path_state": path_state}

        if broken_dates:
            return self._route_broken_dates(path_state, broken_dates)

        if empty_joins:
            path_state["value_repair_attempted"] = True
            path_state["error"] = build_join_repair_error(empty_joins)
            self.logger.info(
                "Empty result — routing to reconstruction to fix %d unmatched "
                "join(s): %s",
                len(empty_joins),
                [
                    f"{k['left']['table']}.{k['left']['column']}="
                    f"{k['right']['table']}.{k['right']['column']}"
                    for f in empty_joins
                    for k in f["keys"]
                ],
            )
            return {"decision": "invalid_sql", "path_state": path_state}

        self.logger.info(
            "Empty result but no literal, date-parse or join fault — passing through"
        )
        return {"decision": "valid_sql", "path_state": path_state}


__all__ = ["EmptyResultValueRepairAgent"]
