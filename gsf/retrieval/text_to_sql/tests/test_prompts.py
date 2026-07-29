# SPDX-FileCopyrightText: Copyright (c) 2026, NVIDIA CORPORATION & AFFILIATES.
# All rights reserved.
# SPDX-License-Identifier: Apache-2.0

from string import Formatter

import pytest

from gsf.retrieval.text_to_sql.prompts import (
    create_sql_user_prompt,
    format_dialect_rules,
    format_projection_rules,
)

# Every slot the agents fill in ``create_sql_user_prompt``. A slot added to the
# template without a matching keyword at both call sites raises KeyError deep in
# a request, so pin the set here instead.
_EXPECTED_SLOTS = {
    "custom_analyses",
    "dialect",
    "dialect_rules",
    "join_paths",
    "main_question",
    "observation_block",
    "projection_rules",
    "qa_from_conversations",
    "queries",
    "tables",
}


def test_user_prompt_slots_match_agent_call_sites() -> None:
    slots = {
        name for _, name, _, _ in Formatter().parse(create_sql_user_prompt) if name
    }

    assert slots == _EXPECTED_SLOTS


def test_projection_defaults_to_narrow(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("TEXT_TO_SQL_BENCHMARK_PROJECTION", raising=False)

    rules = format_projection_rules()

    assert "SELECT only the columns explicitly asked" in rules


@pytest.mark.parametrize("value", ["1", "true", "YES", "on"])
def test_benchmark_projection_is_opt_in(
    value: str, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("TEXT_TO_SQL_BENCHMARK_PROJECTION", value)

    rules = format_projection_rules()

    assert "A missing column makes the answer wrong" in rules
    assert "SELECT only the columns explicitly asked" not in rules


def test_unset_and_falsey_values_keep_narrow_projection(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("TEXT_TO_SQL_BENCHMARK_PROJECTION", "0")

    assert "SELECT only the columns explicitly asked" in format_projection_rules()


def test_sqlite_rules_warn_about_silently_null_date_parses() -> None:
    rules = format_dialect_rules("sqlite")

    assert "strftime() returns NULL for a value it cannot parse" in rules
