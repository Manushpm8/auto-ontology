# SPDX-FileCopyrightText: Copyright (c) 2026, NVIDIA CORPORATION & AFFILIATES.
# All rights reserved.
# SPDX-License-Identifier: Apache-2.0

"""
SQL Execution Agent

Executes validated SQL via the injected DB connector.
"""

import logging
import re
from typing import Any, Dict, Optional

from gsf.retrieval.text_to_sql.base import BaseAgent
from gsf.retrieval.text_to_sql.connector_routing import resolve_connector_from_tables
from gsf.retrieval.text_to_sql.chat_sql import execute_chat_sql
from gsf.retrieval.text_to_sql.state import AgentState
from gsf.connectors.base import SQLDatabase

logger = logging.getLogger(__name__)


class QueryResponse:
    def __init__(self, result: list[str], sliced: bool, error: Optional[str] = None):
        self.result = result
        self.sliced = sliced
        self.error = error


def _sanitize_sql_for_dialect(sql: str, dialect: str) -> str:
    """Strip invalid schema qualifiers for dialects that don't use them."""
    if (dialect or "").lower() == "sqlite":
        return re.sub(r"\bPUBLIC\.", "", sql, flags=re.IGNORECASE)
    return sql


def _dedupe_columns(df):
    """Suffix repeated column names so the frame can serialize to records.
    ``SELECT a.id, b.id`` is valid SQL and returns real data, but pandas cannot
    emit it as records while both columns are called ``id``. Renaming the repeats
    keeps the rows rather than discarding a successful query.
    """
    if not df.columns.duplicated().any():
        return df
    seen: dict[Any, int] = {}
    renamed = []
    for name in df.columns:
        count = seen.get(name, 0)
        seen[name] = count + 1
        renamed.append(name if count == 0 else f"{name}_{count}")
    df = df.copy()
    df.columns = renamed
    return df


def _run_sql(sql: str, connector: SQLDatabase | None) -> QueryResponse:
    """Execute SQL against the supplied ``connector``.

    Caller is responsible for picking the right connector for ``sql``
    (e.g. by matching ``relevant_tables[*].database_name`` against
    ``connector.database_name``).
    """
    if connector is None:
        return QueryResponse(
            result=None, sliced=False, error="No connector available to execute SQL."
        )
    try:
        dialect = getattr(connector, "dialect", "")
        sql = _sanitize_sql_for_dialect(sql, dialect)
        df = execute_chat_sql(connector, sql)
        # Serialization has to be inside the guard. A query that succeeds can
        # still fail to serialize — orient="records" rejects duplicate column
        # names — and an escaping exception takes down the whole agent, whose
        # wrapper then returns a state with no "decision" and strands the graph
        # router on KeyError(''). Losing the question that way is far worse than
        # reporting an execution error.
        payload = (
            _dedupe_columns(df).to_json(
                orient="records", date_format="iso", default_handler=str
            )
            if len(df)
            else "[]"
        )
    except Exception as e:
        logger.exception("SQL execution failed (injected connector)")
        return QueryResponse(result=None, sliced=False, error=str(e))

    return QueryResponse(result=[payload], sliced=False, error=None)


class SQLExecutionAgent(BaseAgent):
    """
    Agent that executes SQL.

    Input:
    - ``path_state["sql_code"]`` (from validation) or ``sql_generation_result.sql_code``
    - ``connectors``: list of injected DB connectors (the first is used to execute).

    Output:
    - ``path_state["sql_response_from_db"]``: :class:`QueryResponse`
    """

    def __init__(self):
        super().__init__("sql_execution")

    def validate_input(self, state: AgentState) -> bool:
        path_state = state.get("path_state", {})
        sql_code = path_state.get("sql_code")
        if not sql_code or not str(sql_code).strip():
            llm = path_state.get("sql_generation_result")
            sql_code = getattr(llm, "sql_code", None) if llm else None
        if not sql_code or not str(sql_code).strip():
            self.logger.warning("No SQL code found for execution")
            return False
        return True

    def execute(self, state: AgentState) -> Dict[str, Any]:
        path_state = state.get("path_state", {})
        sql_code = path_state.get("sql_code")
        if not sql_code or not str(sql_code).strip():
            llm = path_state.get("sql_generation_result")
            sql_code = getattr(llm, "sql_code", "") if llm else ""

        connectors = state.get("connectors") or []
        relevant_tables = path_state.get("relevant_tables", [])
        connector = resolve_connector_from_tables(relevant_tables, connectors)

        response_from_db = _run_sql(sql_code, connector)

        if response_from_db.error:
            self.logger.info("SQL execution error: %s", response_from_db.error)
            path_state["error"] = response_from_db.error
            return {"decision": "invalid_sql", "path_state": path_state}

        return {
            "decision": "valid_sql",
            "path_state": {
                **path_state,
                "sql_response_from_db": response_from_db.result,
            },
        }
