# SPDX-FileCopyrightText: Copyright (c) 2026, NVIDIA CORPORATION & AFFILIATES.
# All rights reserved.
# SPDX-License-Identifier: Apache-2.0

"""Tests for database-scoped domain-rule preparation."""

from unittest.mock import patch

from gsf.retrieval.text_to_sql.agents.candidates_preparation import (
    _refresh_domain_rules,
)


@patch("gsf.retrieval.text_to_sql.agents.candidates_preparation.fetch_custom_analyses")
def test_domain_rules_are_loaded_after_database_selection(fetch_analyses) -> None:
    fetch_analyses.return_value = [{"name": "Revenue", "description": "SQL"}]
    state = {"glossary": [{"name": "ARR", "description": "Annual revenue"}]}

    _refresh_domain_rules(state, "analytics")

    fetch_analyses.assert_called_once_with("analytics")
    assert state["domain_rules"] == [
        {"name": "Revenue", "description": "SQL"},
        {"name": "ARR", "description": "Annual revenue"},
    ]
