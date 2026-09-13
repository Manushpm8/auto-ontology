# SPDX-FileCopyrightText: Copyright (c) 2026, NVIDIA CORPORATION & AFFILIATES.
# All rights reserved.
# SPDX-License-Identifier: Apache-2.0

"""Single-field join default in SQL generation and validation prompts."""

from gsf.retrieval.text_to_sql.formatters_util import format_semantic_context
from gsf.retrieval.text_to_sql.prompts import (
    INTENT_VALIDATION_SYSTEM_PROMPT,
    INTENT_VALIDATION_SYSTEM_PROMPT_JOINS_VALIDATED_ELSEWHERE,
    create_intent_validation_prompt,
    create_sql_from_candidates_prompt,
    create_sql_user_prompt,
)


def test_sql_system_prompt_joins_on_single_field_by_default() -> None:
    prompt = create_sql_from_candidates_prompt(dialect="sqlite")

    assert "From the question's intent, decide which joins to place" in prompt
    assert "Write that join plan in" in prompt
    assert "Then write sql_code from that plan" in prompt
    assert "Drop extra equalities from a suggested hop unless required" in prompt
    assert "A value used only to identify an entity" in prompt
    assert "lookup table" in prompt
    assert "do not copy it onto later joins" in prompt
    assert "When a key is composite" in prompt
    assert "join on the entity identity column only" in prompt
    assert "unless the question scopes the requested" in prompt
    assert "year/period in ON" not in prompt
    assert "season" not in prompt
    assert "coach" not in prompt.lower()
    assert "FDA" not in prompt
    assert "employee of the year" not in prompt


def test_sql_user_prompt_joins_on_single_field_by_default() -> None:
    assert (
        "Join on a single identity field unless the question or evidence"
        in create_sql_user_prompt
    )
    assert (
        "Drop extra equalities from a suggested hop unless required"
        in create_sql_user_prompt
    )
    assert "A value used only to identify an entity" in create_sql_user_prompt
    assert "do not copy it onto later joins" in create_sql_user_prompt
    assert (
        "When a key is composite, join on the entity identity column only"
        in create_sql_user_prompt
    )
    assert "year/period in ON" not in create_sql_user_prompt
    assert "season" not in create_sql_user_prompt
    assert "coach" not in create_sql_user_prompt.lower()
    assert "employee of the year" not in create_sql_user_prompt


def test_join_path_blurb_prefers_single_field() -> None:
    text = format_semantic_context(
        {
            "schema_name": "",
            "table_name": "awards",
            "col_name": "person_id",
            "attr_name": "Awardee",
        },
        [
            {
                "attr_name": "Project",
                "col_name": "name",
                "schema_name": "",
                "table_name": "projects",
                "path": [
                    {
                        "source_table": "people",
                        "source_column": "id",
                        "target_table": "projects",
                        "target_column": "lead_id",
                    }
                ],
            }
        ],
    )

    assert "Use the identity-field equalities" in text
    assert "belongs in WHERE on that lookup table" in text
    assert "do not copy it onto later joins" in text
    assert "When a key is composite, join on the entity identity" in text
    assert "year/period in ON" not in text
    assert "season" not in text
    assert "coach" not in text.lower()


def test_intent_validation_can_flag_extra_join_equalities() -> None:
    for prompt in (
        INTENT_VALIDATION_SYSTEM_PROMPT,
        INTENT_VALIDATION_SYSTEM_PROMPT_JOINS_VALIDATED_ELSEWHERE,
    ):
        assert "Do flag extra ON equalities" in prompt
        assert "beyond a single identity field" in prompt
        assert "belongs in WHERE on that lookup table" in prompt
        assert "copied onto later joins" in prompt
        assert "A composite key does not license a" in prompt
        assert "season" not in prompt
        assert "coach" not in prompt.lower()

    user = create_intent_validation_prompt(
        "q",
        "q",
        "q",
        "SELECT 1",
        join_paths="JOIN PATHS (AUTHORITATIVE)",
    )
    assert "single-field identity hop" in user
    assert "did not require them" in user
    assert "copied onto later joins" in user
    assert "A composite key does not license a period equality in ON" in user
    assert "match those columns" not in user
