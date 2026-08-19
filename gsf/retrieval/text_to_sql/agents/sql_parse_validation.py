# SPDX-FileCopyrightText: Copyright (c) 2026, NVIDIA CORPORATION & AFFILIATES.
# All rights reserved.
# SPDX-License-Identifier: Apache-2.0

"""
SQL Validation Agent

This agent validates SQL queries before execution.
Checks for logical correctness, not just syntax.

Responsibilities:
- Validate SQL logic (not just syntax)
- Check for common mistakes (self-comparisons, incorrect filters, etc.)
- Handle text-based answers (skip validation)
- Store validation result in path_state

Design Decisions:
- Uses LLM to validate logical correctness
- Sets connection data based on retrieved tables
- Returns decision: "valid_sql" or "invalid_sql"
"""

import logging
from typing import Dict, Any

import sqlglot
from sqlglot import expressions as exp

from nemo_retriever.tabular_data.ingestion.services.queries import parse_query_single
from gsf.dal.attributes import find_table_key_columns
from gsf.retrieval.text_to_sql.base import BaseAgent
from gsf.retrieval.text_to_sql.state import AgentState
from gsf.retrieval.data_access.custom_analyses import get_custom_analyses_ids
from gsf.retrieval.data_access.graph_schemas import (
    fetch_all_schema_ids,
    get_schemas_by_ids,
)

logger = logging.getLogger(__name__)

# sqlglot dialect names differ slightly from our connector dialect strings.
_SQLGLOT_DIALECTS = {
    "sqlite": "sqlite",
    "postgres": "postgres",
    "postgresql": "postgres",
    "snowflake": "snowflake",
    "duckdb": "duckdb",
    "mysql": "mysql",
    "heavydb": "postgres",
}


def _unwrap_projection(e: exp.Expression) -> exp.Expression:
    """Strip an alias wrapper so ``NULL AS x`` is seen as ``NULL``."""
    return e.this if isinstance(e, exp.Alias) else e


def _is_always_false(cond: exp.Expression | None) -> bool:
    """True for constant-false predicates like ``1=0``, ``0=1``, ``FALSE``."""
    if cond is None:
        return False
    if isinstance(cond, exp.Boolean):
        return cond.this is False
    if isinstance(cond, exp.EQ):
        left, right = cond.left, cond.right
        if (
            isinstance(left, exp.Literal)
            and isinstance(right, exp.Literal)
            and left.is_number
            and right.is_number
        ):
            return left.name != right.name
    return False


def detect_degenerate_sql(sql: str, dialect: str | None = None) -> str:
    """Return a human-readable reason when *sql* is a placeholder/no-op query.

    Flags queries that parse and execute fine but can never answer the question:
    ``SELECT NULL`` / constant-only projections, always-false ``WHERE`` clauses
    (``1=0``), and ``LIMIT 0``. Returns ``""`` when the SQL looks like real work.

    Inspects only the OUTERMOST SELECT, so legitimate ``EXISTS (SELECT 1 ...)``
    subqueries are not flagged.
    """
    if not sql or not sql.strip():
        return "the generated SQL is empty"

    read = _SQLGLOT_DIALECTS.get((dialect or "").strip().lower())
    try:
        parsed = sqlglot.parse_one(sql, read=read)
    except Exception:
        # Unparseable here → let the normal parse validator handle it.
        return ""
    if parsed is None:
        return ""

    select = parsed if isinstance(parsed, exp.Select) else parsed.find(exp.Select)
    if select is None:
        return ""

    projections = list(select.expressions or [])
    if projections and all(
        isinstance(_unwrap_projection(p), (exp.Null, exp.Literal, exp.Boolean))
        for p in projections
    ):
        return (
            "the query only selects constant/NULL values instead of real data "
            "from the tables (e.g. SELECT NULL)"
        )

    where = select.args.get("where")
    if where is not None and _is_always_false(where.this):
        return (
            "the query has an always-false WHERE condition (e.g. 1=0), so it can "
            "never return any rows"
        )

    limit = select.args.get("limit")
    if limit is not None:
        limit_expr = getattr(limit, "expression", None)
        if (
            isinstance(limit_expr, exp.Literal)
            and limit_expr.is_number
            and limit_expr.name == "0"
        ):
            return "the query uses LIMIT 0, so it always returns no rows"

    return ""


def detect_vacuous_group_by(
    sql: str, dialect: str | None, database_name: str | None
) -> str:
    """Return a human-readable reason when *sql* groups/partitions by a column
    that's already unique per row — e.g. ``GROUP BY`` (or a window function's
    ``PARTITION BY``) on the FROM table's own primary key, or on a column
    known unique from observed-data profiling. Since such a column has
    exactly one distinct value per row, every group/partition contains
    exactly one row and any aggregate over it (``AVG``, ``SUM``, a window
    ``COUNT`` etc.) is a silent no-op — it parses and executes fine but never
    computes what "average per <thing>" actually means.

    Scoped to the narrow, always-true case only: a single-table SELECT block
    (no JOIN *within that block*) grouping/partitioning by that same table's
    own key. A JOIN can legitimately re-introduce multiple rows per key (e.g.
    aggregating a child table's rows per parent id), so this intentionally
    does not flag a block once a JOIN is present in it — that needs
    join-cardinality reasoning this check doesn't attempt.

    Checked on *every* SELECT block in the parsed tree — the outermost query,
    every subquery, every CTE — not just the outermost one. A subquery that
    itself vacuously groups a table by its own key is just as much a no-op as
    if it were written at the top level; wrapping it in an outer JOIN back to
    the same table (e.g. ``t JOIN (SELECT k, AVG(x) FROM t GROUP BY k) s ON
    t.k = s.k``) makes the *outer* query's FROM/JOIN shape look fine while
    the actual aggregation inside the subquery is still averaging exactly one
    row per group — a real pattern seen in practice once reconstruction was
    pushed away from the bare single-table form.

    Returns ``""`` when nothing looks wrong, including whenever the SQL
    doesn't contain "group by"/"partition by" at all (checked before any
    parsing, so the common case costs nothing) or isn't parseable (left to
    the normal parse validator).
    """
    sql_lower = (sql or "").lower()
    if "group by" not in sql_lower and "partition by" not in sql_lower:
        return ""

    read = _SQLGLOT_DIALECTS.get((dialect or "").strip().lower())
    try:
        parsed = sqlglot.parse_one(sql, read=read)
    except Exception:
        return ""
    if parsed is None:
        return ""

    for select in parsed.find_all(exp.Select):
        reason = _check_select_block_vacuous(select, database_name)
        if reason:
            return reason
    return ""


def _check_select_block_vacuous(select: exp.Select, database_name: str | None) -> str:
    """Single-SELECT-block half of :func:`detect_vacuous_group_by` — see there
    for the full rationale. Returns ``""`` when this block looks fine."""
    # Only the single-table, no-JOIN case — see docstring.
    if select.args.get("joins"):
        return ""
    # exp.From, not select.args.get("from") — sqlglot's internal arg key for
    # this has changed across versions ("from" vs "from_"); searching by node
    # type is stable regardless. .find() (not find_all/recursion into nested
    # selects) stays scoped to this block's own FROM since a nested SELECT
    # would be inside a subquery, not a sibling of this block's FROM clause.
    from_clause = select.find(exp.From)
    table_expr = from_clause.this if from_clause is not None else None
    if not isinstance(table_expr, exp.Table) or not table_expr.name:
        return ""
    table_name = table_expr.name
    table_alias = table_expr.alias_or_name

    grouping_cols: set[str] = set()
    group = select.args.get("group")
    if group is not None:
        for e in group.expressions or []:
            col = e.this if isinstance(e, exp.Ordered) else e
            if isinstance(col, exp.Column):
                grouping_cols.add(col.name.lower())
    for window in select.find_all(exp.Window):
        for col in window.args.get("partition_by") or []:
            if isinstance(col, exp.Column):
                grouping_cols.add(col.name.lower())
    if not grouping_cols:
        return ""

    keys = find_table_key_columns(table_name, database_name)
    unique_cols = {c.lower() for c in (keys["pk"] + keys["unique"])}
    hit = grouping_cols & unique_cols
    if not hit:
        return ""

    culprit = next(iter(hit))
    return (
        f'the query groups/partitions by "{culprit}", which is already unique '
        f'per row in "{table_name}" (its primary key or a column confirmed '
        f"unique from the data) — with no JOIN bringing in additional rows in "
        f"that part of the query, every group/partition has exactly one row, "
        f"so any aggregate over it (AVG, SUM, a window function, etc.) is a "
        f'no-op and does not compute a real "per group" result — even if it '
        f"is subsequently joined back to the same table, since the "
        f"aggregation was already trivial before that join. Group/partition "
        f"by the actual dimension the question is asking to aggregate over "
        f"instead (e.g. a foreign key or category column shared by multiple "
        f"rows), or remove the grouping/partitioning if the question wants "
        f'one row per "{table_alias or table_name}" record.'
    )


class SQLValidationAgent(BaseAgent):
    """
    Agent that validates SQL queries before execution.

    This agent performs logical validation of SQL queries, checking for
    common mistakes like self-comparisons, incorrect filters, etc.

    Input Requirements:
    - path_state["sql_generation_result"]: SQL response to validate
    - path_state["relevant_tables"]: Relevant tables used

    Output:
    - path_state["sql_response_from_db"]: None (will be set after execution)
    - path_state["sql_columns"]: Column IDs from SQL
    - path_state["custom_analyses_used"]: Semantic entity IDs used
    - decision: "valid_sql" or "invalid_sql"
    """

    def __init__(self):
        super().__init__("sql_validation")

    def validate_input(self, state: AgentState) -> bool:
        """Validate that SQL response is available."""
        path_state = state.get("path_state", {})
        if state.get("decision") == "unconstructable":
            # Skip validation if SQL couldn't be constructed
            return False
        if not path_state.get("sql_generation_result"):
            self.logger.warning("No SQL response found for validation")
            return False
        return True

    def execute(self, state: AgentState) -> Dict[str, Any]:
        """
        Validate SQL query.

        Performs logical validation using LLM and query_validation function.
        Sets connection data and extracts columns from SQL.

        Args:
            state: Current agent state

        Returns:
            Dictionary with:
            - path_state: Contains validation result and extracted data
            - decision: "valid_sql" or "invalid_sql"
        """
        path_state = state.get("path_state", {})
        response = path_state.get("sql_generation_result")
        connectors = state.get("connectors") or []
        dialects = [c.dialect for c in connectors if getattr(c, "dialect", None)]
        schemas_ids = fetch_all_schema_ids()
        schemas = get_schemas_by_ids(schemas_ids)

        validation_result = self._sql_parse_validation(
            schemas, response.sql_code, dialects
        )

        if validation_result.get("error"):
            error_msg = validation_result["error"]
            self.logger.info(f"SQL validation failed: {error_msg}")
            path_state["error"] = error_msg
            return {
                "decision": "invalid_sql",
                "path_state": path_state,
            }

        degenerate_dialect = dialects[0] if dialects else None
        degenerate_reason = detect_degenerate_sql(response.sql_code, degenerate_dialect)

        if degenerate_reason:
            self.logger.info("Degenerate SQL rejected: %s", degenerate_reason)
            path_state["error"] = (
                f"The generated SQL is a placeholder that does not answer the "
                f"question: {degenerate_reason}. Rewrite a real query that selects "
                f"the requested data from the available tables. Do NOT use SELECT "
                f"NULL, constant-only projections, always-false conditions such as "
                f"WHERE 1=0, or LIMIT 0."
            )
            return {
                "decision": "invalid_sql",
                "path_state": path_state,
            }

        vacuous_reason = detect_vacuous_group_by(
            response.sql_code, degenerate_dialect, path_state.get("target_db")
        )
        if vacuous_reason:
            self.logger.info("Vacuous GROUP BY/PARTITION BY rejected: %s", vacuous_reason)
            path_state["error"] = (
                f"The generated SQL's aggregation is a no-op: {vacuous_reason}"
            )
            # Deterministic, self-contained diagnosis — never a missing_data
            # situation (no new table/column could fix a query that's grouping
            # by a column already unique per row). Skip reconstruction's LLM
            # error-classification call so it can't misread "join"/"aggregate"
            # in the message and go searching for tables that don't help here.
            path_state["error_known_fixable"] = True
            return {
                "decision": "invalid_sql",
                "path_state": path_state,
            }

        sql_columns = validation_result.get("sql_columns") or []
        custom_analyses_used = []
        if hasattr(response, "custom_analyses_used"):
            custom_analyses_used = get_custom_analyses_ids(
                response.custom_analyses_used
            )

        # Store connection_data in the format expected by execute_sql_query
        # execute_sql_query expects connections as a list
        updated_path_state = {
            **path_state,
            "sql_response_from_db": None,  # Will be set after execution
            "sql_columns": sql_columns,
            "custom_analyses_used": custom_analyses_used,
            "sql_code": response.sql_code,  # Store SQL code for execution
        }

        self.logger.info(f"SQL validation passed, columns: {len(sql_columns)}")

        return {
            "decision": "valid_sql",
            "path_state": updated_path_state,
        }

    @staticmethod
    def _sql_parse_validation(schemas, sql: str, dialects: list[str]) -> dict:
        result: dict = {}
        try:
            parse_query_single(
                sql=sql,
                schemas=schemas,
                dialects=dialects,
            )
            result["success"] = True
        except Exception as error:
            result.update({"error": str(error), "another_try": 1})
        return result
