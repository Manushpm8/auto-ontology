# SPDX-FileCopyrightText: Copyright (c) 2026, NVIDIA CORPORATION & AFFILIATES.
# All rights reserved.
# SPDX-License-Identifier: Apache-2.0

"""SQL generation node for the rerank flow.

Uses the (non-reasoning) LLM to build — but not execute — a single read-only
SQL query grounded in the context that ``column_resolution`` put on the state:
the normalized question, resolved/relative tables (with their join paths), and
the per-entity column/value mappings. ``terms`` are filtered by their exact
resolved values, ``search_for`` entities are fuzzy-matched, and
``numeric_concepts`` columns are returned.
"""

from typing import Any, Dict

from langchain_core.messages import SystemMessage
from pydantic import BaseModel, ConfigDict, Field

from gsf.retrieval.rerank.prompts import create_sql_generation_prompt
from gsf.retrieval.rerank.state import RerankState, get_original_question
from gsf.retrieval.text_to_sql.agents.sql_from_semantic import format_tables_for_prompt
from gsf.retrieval.text_to_sql.base import BaseAgent
from gsf.retrieval.text_to_sql.connector_routing import resolve_connector_from_tables
from gsf.utils.llm_invoke import invoke_with_structured_output


class SqlGenerationModel(BaseModel):
    """A single generated SQL query."""

    model_config = ConfigDict(extra="forbid")

    sql: str = Field(
        ...,
        description=(
            "A single read-only SQL SELECT query answering the request, in the "
            "requested dialect. No DDL/DML, no trailing commentary."
        ),
    )


def _qualified_column(mapping: Dict[str, Any]) -> str:
    """Build ``schema.table.column`` (or ``table.column``) from an entity mapping."""
    schema = mapping.get("schema")
    table = mapping.get("table")
    column = mapping.get("column")
    prefix = f"{schema}.{table}" if schema else table
    return f"{prefix}.{column}"


def _build_term_filters(mappings: list[dict]) -> str:
    """Authoritative WHERE predicates for resolved ``terms`` (exact values)."""
    lines: list[str] = []
    for m in mappings:
        if not (m.get("table") and m.get("column")):
            continue
        col = _qualified_column(m)
        value = m.get("value")
        if value is not None:
            escaped = str(value).replace("'", "''")
            lines.append(f"- {m.get('entity')}: {col} = '{escaped}'")
        else:
            lines.append(
                f"- {m.get('entity')}: LOWER({col}) LIKE LOWER('%{m.get('entity')}%') "
                "(no exact value resolved)"
            )
    return "\n".join(lines) if lines else "(none)"


def _build_search_for_hints(mappings: list[dict]) -> str:
    """Column hints for the ``search_for`` core item (words AND-ed in the SQL)."""
    lines: list[str] = []
    for m in mappings:
        if not (m.get("table") and m.get("column")):
            continue
        lines.append(f"- {m.get('entity')}: column {_qualified_column(m)}")
    return "\n".join(lines) if lines else "(none)"


def _build_search_for_details_hints(mappings: list[dict]) -> str:
    """Column hints for ``search_for_details`` (words OR-ed in the SQL)."""
    lines: list[str] = []
    for m in mappings:
        if not (m.get("table") and m.get("column")):
            continue
        lines.append(f"- {m.get('entity')}: column {_qualified_column(m)}")
    return "\n".join(lines) if lines else "(none)"


def _build_numeric_hints(mappings: list[dict]) -> str:
    """SELECT-only columns for ``numeric_concepts`` entities."""
    lines: list[str] = []
    for m in mappings:
        if not (m.get("table") and m.get("column")):
            continue
        lines.append(f"- {m.get('entity')}: SELECT {_qualified_column(m)}")
    return "\n".join(lines) if lines else "(none)"


def _build_join_paths_section(relative_tables: list[dict]) -> str:
    """Render the semantic join conditions linking resolved and relative tables."""
    lines: list[str] = []
    for table in relative_tables:
        for jp in table.get("join_paths") or []:
            src_table = jp.get("source_table") or ""
            src_col = jp.get("source_column") or ""
            tgt_table = jp.get("target_table") or ""
            tgt_col = jp.get("target_column") or ""
            if src_table and src_col and tgt_table and tgt_col:
                lines.append(f"- {src_table}.{src_col} = {tgt_table}.{tgt_col}")
    return "\n".join(lines) if lines else "(none — do not join tables)"


class SqlGenerationAgent(BaseAgent):
    """Generate (without executing) a SQL query from the resolved context."""

    def __init__(self):
        super().__init__("sql_generation")

    def validate_input(self, state: RerankState) -> bool:
        """Validate that a question is available."""
        if not get_original_question(state):
            self.logger.warning("No question in state, skipping SQL generation")
            return False
        return True

    def execute(self, state: RerankState) -> Dict[str, Any]:
        """Build the SQL query and store it on ``path_state['sql']``."""
        path_state = state.get("path_state", {})
        result: Dict[str, Any] = {"path_state": path_state}

        llm = state["llm"]
        question = get_original_question(state)
        entity_mappings = path_state.get("entity_mappings", {}) or {}
        resolved_tables = path_state.get("resolved_tables", []) or []
        relative_tables = path_state.get("relative_tables", []) or []

        connectors = state.get("connectors") or []
        connector = resolve_connector_from_tables(
            resolved_tables + relative_tables, connectors
        )
        dialect = getattr(connector, "dialect", None)

        tables_section = format_tables_for_prompt(resolved_tables + relative_tables)
        join_paths_section = _build_join_paths_section(
            resolved_tables + relative_tables
        )
        term_filters = _build_term_filters(entity_mappings.get("terms") or [])
        search_for_hints = _build_search_for_hints(
            entity_mappings.get("search_for") or []
        )
        search_for_details_hints = _build_search_for_details_hints(
            entity_mappings.get("search_for_details") or []
        )
        numeric_hints = _build_numeric_hints(
            entity_mappings.get("numeric_concepts") or []
        )

        prompt = create_sql_generation_prompt(
            dialect=dialect,
            question=question,
            tables_section=tables_section,
            join_paths_section=join_paths_section,
            term_filters=term_filters,
            search_for_hints=search_for_hints,
            search_for_details_hints=search_for_details_hints,
            numeric_hints=numeric_hints,
        )

        response = invoke_with_structured_output(
            llm,
            [SystemMessage(content=prompt)],
            SqlGenerationModel,
        )

        if response is None:
            self.logger.warning("SQL generation returned None, storing empty SQL")
            path_state["sql"] = ""
            return result

        sql = (response.sql or "").strip()
        path_state["sql"] = sql
        self.logger.info("Generated SQL:\n%s", sql)
        return result
