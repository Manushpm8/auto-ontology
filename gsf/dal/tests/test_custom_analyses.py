# SPDX-FileCopyrightText: Copyright (c) 2026, NVIDIA CORPORATION & AFFILIATES.
# All rights reserved.
# SPDX-License-Identifier: Apache-2.0

"""Tests for custom-analysis graph reads."""

from unittest.mock import MagicMock, patch

from gsf.dal.custom_analyses import fetch_custom_analyses


@patch("gsf.dal.custom_analyses.graph")
def test_domain_rules_are_scoped_through_the_database_node(
    graph: MagicMock,
) -> None:
    connection = graph.return_value
    connection.query_read.return_value = [
        {"name": "Revenue", "description": "", "sql_code": "SELECT 1"}
    ]

    rules = fetch_custom_analyses("analytics")

    assert rules == [{"name": "Revenue", "description": "SQL: SELECT 1"}]
    query = connection.query_read.call_args.kwargs["query"]
    assert "(:Database {name: $database_name})" in query
    assert connection.query_read.call_args.kwargs["parameters"] == {
        "database_name": "analytics"
    }
