# SPDX-FileCopyrightText: Copyright (c) 2026, NVIDIA CORPORATION & AFFILIATES.
# All rights reserved.
# SPDX-License-Identifier: Apache-2.0

"""Tests for the shared table/column prompt formatting in sql_from_semantic.py."""

from __future__ import annotations

from auto_ontology.retrieval.text_to_sql.formatters_util import format_tables_for_prompt
from auto_ontology.retrieval.text_to_sql.prompts import (
    create_sql_from_candidates_prompt,
    create_sql_user_prompt,
    format_projection_rules,
)


def test_prompt_renders_date_format_when_present() -> None:
    rendered = format_tables_for_prompt(
        [
            {
                "name": "matches",
                "database_name": "cricket",
                "schema_name": "public",
                "columns": [
                    {
                        "name": "Match_Date",
                        "data_type": "text",
                        "description": "Date the match was played.",
                        "format": "YYMMDD",
                    }
                ],
            }
        ]
    )
    assert "format: YYMMDD" in rendered


def test_prompt_omits_format_when_absent() -> None:
    rendered = format_tables_for_prompt(
        [
            {
                "name": "matches",
                "schema_name": "public",
                "columns": [
                    {"name": "team_name", "data_type": "text", "description": ""}
                ],
            }
        ]
    )
    assert "format:" not in rendered


def test_prompt_forbids_unused_joins() -> None:
    assert "menu of valid options" in create_sql_user_prompt
    assert "If removing a join would not change the answer, omit it" in (
        create_sql_user_prompt
    )
    assert "Never join a table solely because its path is listed" in (
        create_sql_user_prompt
    )


def test_semantic_sql_prompt_does_not_reaggregate_aggregated_columns() -> None:
    prompt = create_sql_from_candidates_prompt(dialect="sqlite")

    assert "Never apply an aggregate function to a column" in prompt
    assert "name or description" in prompt
    assert "Use the stored aggregate directly" in prompt


def test_projection_strictness_is_configured_per_request() -> None:
    strict_rule = "- Return exactly the requested output fields and NO others."

    assert strict_rule not in format_projection_rules(shorten_answer=False)
    assert strict_rule in format_projection_rules(shorten_answer=True)
    projection_rules = format_projection_rules(shorten_answer=False)
    assert (
        "Ignore confidence when deciding which outputs to project" in projection_rules
    )
    assert (
        "the question determines the output fields and their order" in projection_rules
    )
    assert "prefer a name/label/title when available" in projection_rules
    strict_projection_rules = format_projection_rules(shorten_answer=True)
    assert "use its ID unless the question explicitly asks" in strict_projection_rules
    assert (
        "Generic 'who', 'which', 'what', or 'list' wording" in strict_projection_rules
    )
    assert "Never return both ID and name" in strict_projection_rules


def test_prompt_uses_inline_semantic_metadata_without_duplicate_section() -> None:
    assert "## Semantically Important Columns" not in create_sql_user_prompt
    assert "{important_columns}" not in create_sql_user_prompt
    assert "Higher confidence means a stronger direct match" in create_sql_user_prompt
    assert "verified semantic join path" in create_sql_user_prompt
    assert "0 means neither signal applies" in create_sql_user_prompt
    assert "A semantic match does not itself make a column an output" in (
        create_sql_user_prompt
    )
