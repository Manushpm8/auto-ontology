# SPDX-FileCopyrightText: Copyright (c) 2026, NVIDIA CORPORATION & AFFILIATES.
# All rights reserved.
# SPDX-License-Identifier: Apache-2.0

import json

from gsf.retrieval.text_to_sql.agents.result_health import (
    build_dead_column_error,
    build_superlative_cardinality_error,
    find_dead_result_columns,
    has_exact_duplicate_rows,
    is_singular_superlative_question,
    should_repair_superlative_cardinality,
)


def _payload(rows: list[dict]) -> list[str]:
    """Wrap rows the way SQLExecutionAgent hands results to the graph."""
    return [json.dumps(rows)]


def test_flags_aggregate_that_lost_all_its_rows() -> None:
    # Shape of the sf_local075 miss: the purchase count was filtered away by the
    # same WHERE clause that excluded the confirmation page, so it came back 0
    # for every product while views and cart adds counted fine.
    rows = [
        {"product": "Salmon", "views": 1559, "added": 938, "purchased": 0},
        {"product": "Kingfish", "views": 1559, "added": 920, "purchased": 0},
        {"product": "Oyster", "views": 1568, "added": 943, "purchased": 0},
    ]

    assert find_dead_result_columns(_payload(rows)) == ["purchased"]


def test_null_only_column_counts_as_dead() -> None:
    rows = [
        {"month": "2020-01", "balance": None},
        {"month": "2020-02", "balance": None},
        {"month": "2020-03", "balance": None},
    ]

    assert find_dead_result_columns(_payload(rows)) == ["balance"]


def test_single_row_zero_beside_populated_columns_is_left_alone() -> None:
    # sf_local064's shape: gold really does report 0 for the lowest month's
    # average balance, so one row of zeros is no evidence of a lost aggregate.
    rows = [
        {
            "highest_month": "2020-01",
            "highest_avg_balance": 557.704,
            "lowest_month": "2020-05",
            "lowest_avg_balance": 0.0,
        }
    ]

    assert find_dead_result_columns(_payload(rows)) == []


def test_scalar_zero_is_left_alone() -> None:
    # A single-column zero may well be the right answer, and there is no
    # populated sibling to contradict it.
    assert find_dead_result_columns(_payload([{"total": 0}])) == []


def test_all_blank_columns_are_left_to_the_empty_result_paths() -> None:
    rows = [{"a": 0, "b": None}]

    assert find_dead_result_columns(_payload(rows)) == []


def test_populated_result_is_not_flagged() -> None:
    rows = [{"product": "Salmon", "added": 938, "purchased": 711}]

    assert find_dead_result_columns(_payload(rows)) == []


def test_zero_among_nonzero_values_is_not_flagged() -> None:
    rows = [
        {"product": "Salmon", "purchased": 711},
        {"product": "Russian Caviar", "purchased": 0},
    ]

    assert find_dead_result_columns(_payload(rows)) == []


def test_all_false_flag_column_is_not_a_lost_aggregate() -> None:
    rows = [{"customer": 1, "is_vip": False}, {"customer": 2, "is_vip": False}]

    assert find_dead_result_columns(_payload(rows)) == []


def test_empty_and_malformed_results_are_ignored() -> None:
    assert find_dead_result_columns(_payload([])) == []
    assert find_dead_result_columns(None) == []
    assert find_dead_result_columns(["not json"]) == []
    assert find_dead_result_columns([json.dumps([1, 2, 3])]) == []


def test_error_names_every_dead_column() -> None:
    message = build_dead_column_error(["purchased", "abandoned"])

    assert "purchased, abandoned" in message
    assert "EXISTS" in message


def test_singular_superlative_question_detection() -> None:
    assert is_singular_superlative_question(
        'For the NXT title that had the shortest match, who were the wrestlers?'
    )
    assert is_singular_superlative_question(
        "Which store has the highest total number of orders?"
    )
    assert not is_singular_superlative_question(
        "Can you help me find the top 5 countries whose players have the highest average?"
    )
    assert not is_singular_superlative_question(
        "Provide the highest and lowest profits for Italian customers"
    )
    assert not is_singular_superlative_question(
        "Can you identify the hubs that saw more than a 20% increase?"
    )


def test_superlative_cardinality_flags_multi_row_singular_answer() -> None:
    question = (
        "For the NXT title that had the shortest match "
        '(excluding titles with "title change"), what were the names of the two wrestlers involved?'
    )
    row = {
        "title_name": "NXT Championship",
        "winner_name": "Bron Breakker",
        "loser_name": "Duke Hudson",
        "duration": "00:43",
    }
    result = _payload([row, row, row, row, row, row, row])
    sql = "SELECT * FROM matches ORDER BY duration_seconds"

    assert has_exact_duplicate_rows(result)
    assert should_repair_superlative_cardinality(question, result, sql)
    message = build_superlative_cardinality_error(7, has_duplicates=True)
    assert "7 rows" in message
    assert "LIMIT 1" in message
    assert "duplicates" in message


def test_superlative_cardinality_skips_limit_1_and_top_n() -> None:
    question = "Which store has the highest total number of orders?"
    rows = _payload(
        [
            {"store": "A", "orders": 10},
            {"store": "B", "orders": 9},
        ]
    )

    assert not should_repair_superlative_cardinality(
        question, rows, "SELECT store FROM t ORDER BY orders DESC LIMIT 1"
    )
    assert not should_repair_superlative_cardinality(
        "Find the top 5 drivers with the highest average deliveries",
        rows,
        "SELECT driver_id FROM t ORDER BY avg DESC",
    )
    assert not should_repair_superlative_cardinality(
        question, _payload([{"store": "A", "orders": 10}]), "SELECT store FROM t"
    )
