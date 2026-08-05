# SPDX-FileCopyrightText: Copyright (c) 2026, NVIDIA CORPORATION & AFFILIATES.
# All rights reserved.
# SPDX-License-Identifier: Apache-2.0

"""alt_table_set strategy is a first-class slot plan tag."""

from gsf.retrieval.text_to_sql.agents.sql_from_semantic import (
    _ALT_TABLE_SET_STRATEGY,
    _CANDIDATE_STRATEGY_TAGS,
    _slot_plan,
)


def test_alt_table_set_in_strategy_tags():
    assert "alt_table_set" in _CANDIDATE_STRATEGY_TAGS
    assert "DIFFERENT primary table" in _ALT_TABLE_SET_STRATEGY


def test_slot_plan_accepts_alt_table_set(monkeypatch):
    monkeypatch.setenv(
        "BIRD_SLOT_PLAN", "baseline,query_plan,decomposition,alt_table_set"
    )
    assert _slot_plan() == (
        "baseline",
        "query_plan",
        "decomposition",
        "alt_table_set",
    )
