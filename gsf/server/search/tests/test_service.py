# SPDX-FileCopyrightText: Copyright (c) 2024-2026, NVIDIA CORPORATION & AFFILIATES.
# All rights reserved.
# SPDX-License-Identifier: Apache-2.0

"""Unit tests for discovery search orchestration."""

from __future__ import annotations

from unittest.mock import MagicMock, patch

import pytest

from gsf.server.search.service import (
    SearchValidationError,
    discovery_count,
    discovery_search,
    rank_key,
    resolve_object_types,
)


def test_resolve_object_types_defaults_to_all() -> None:
    types = resolve_object_types(None)
    assert "term" in types
    assert "column" in types
    assert "view" in types


def test_resolve_object_types_empty_list_is_all() -> None:
    assert resolve_object_types([]) == resolve_object_types(None)


def test_resolve_object_types_rejects_unknown() -> None:
    with pytest.raises(SearchValidationError, match="metric"):
        resolve_object_types(["term", "metric"])


def test_rank_key_prefers_names_containing_the_term_then_shorter() -> None:
    items = [
        {"name": "other"},
        {"name": "annual_revenue_total"},
        {"name": "revenue"},
        {"name": "Revenue_id"},
    ]
    items.sort(key=rank_key("revenue"))
    assert [row["name"] for row in items] == [
        "revenue",
        "Revenue_id",
        "annual_revenue_total",
        "other",
    ]


def test_rank_key_orders_synonym_after_name_before_description_only() -> None:
    items = [
        {"name": "other"},
        {"name": "Cluster Passport", "synonyms": ["Customer"]},
        {"name": "Customer"},
    ]
    items.sort(key=rank_key("Customer"))
    assert [row["name"] for row in items] == [
        "Customer",
        "Cluster Passport",
        "other",
    ]


def test_short_query_skips_neo4j_and_returns_empty() -> None:
    with (
        patch("gsf.server.search.service.search_dal.ensure_search_indexes") as ensure,
        patch("gsf.server.search.service.search_dal.fetch_discovery") as fetch,
    ):
        result = discovery_search(
            search_term="a",
            text_match_option="contains",
            objects=None,
            include_description=True,
        )
    assert result == {"data": [], "count": 0}
    ensure.assert_not_called()
    fetch.assert_not_called()


def test_specials_only_query_is_treated_as_empty() -> None:
    with patch("gsf.server.search.service.search_dal.fetch_discovery") as fetch:
        result = discovery_count(
            search_term="**",
            text_match_option="contains",
            objects=None,
            include_description=False,
        )
    assert result == {"data": {}}
    fetch.assert_not_called()


def test_unsupported_match_option_raises() -> None:
    with pytest.raises(SearchValidationError, match="starts_with"):
        discovery_search(
            search_term="revenue",
            text_match_option="starts_with",
            objects=None,
            include_description=False,
        )


@patch("gsf.server.search.service.search_dal.ensure_search_indexes")
@patch("gsf.server.search.service.search_dal.fetch_discovery")
def test_discovery_search_normalizes_and_ranks(
    fetch: MagicMock, _ensure: MagicMock
) -> None:
    fetch.return_value = [
        {
            "id": "1",
            "name": "zz_revenue",
            "type": "table",
            "description": None,
            "certified": None,
            "parent_id": None,
            "breadcrumbs": [
                {"name": "sales", "type": "db"},
                {"name": None, "type": "schema"},
            ],
        },
        {
            "id": "2",
            "name": "revenue",
            "type": "term",
            "description": "money",
            "certified": "pending",
            "parent_id": None,
            "breadcrumbs": [],
        },
    ]

    result = discovery_search(
        search_term="revenue",
        text_match_option="contains",
        objects=["term", "table"],
        include_description=True,
    )

    assert [item["id"] for item in result["data"]] == ["2", "1"]
    assert result["data"][1]["breadcrumbs"] == [{"name": "sales", "type": "db"}]
    assert result["count"] == 2
    fetch.assert_called_once()
    kwargs = fetch.call_args
    assert kwargs.args[0] == "*revenue*"
    assert kwargs.args[1] == {"term", "table"}
    assert kwargs.kwargs["include_description"] is True
    assert kwargs.kwargs["synonym_tokens"] == ["revenue"]


@patch("gsf.server.search.service.search_dal.ensure_search_indexes")
@patch("gsf.server.search.service.search_dal.fetch_discovery")
def test_discovery_search_keeps_breadcrumb_ids(
    fetch: MagicMock, _ensure: MagicMock
) -> None:
    fetch.return_value = [
        {
            "id": "col-1",
            "name": "customer_id",
            "type": "column",
            "description": None,
            "certified": None,
            "parent_id": "tbl-1",
            "breadcrumbs": [
                {"id": "db-1", "name": "sales", "type": "db"},
                {"id": "sch-1", "name": "public", "type": "schema"},
                {"id": "tbl-1", "name": "customers", "type": "table"},
            ],
        },
    ]
    result = discovery_search(
        search_term="customer",
        text_match_option="contains",
        objects=["column"],
        include_description=True,
    )
    assert result["data"][0]["breadcrumbs"] == [
        {"id": "db-1", "name": "sales", "type": "db"},
        {"id": "sch-1", "name": "public", "type": "schema"},
        {"id": "tbl-1", "name": "customers", "type": "table"},
    ]


@patch("gsf.server.search.service.search_dal.ensure_search_indexes")
@patch("gsf.server.search.service.search_dal.count_discovery")
def test_discovery_count_passes_object_filter(
    count: MagicMock, _ensure: MagicMock
) -> None:
    count.return_value = {"term": 2, "column": 4}
    result = discovery_count(
        search_term="id",
        text_match_option="contains",
        objects=["column"],
        include_description=False,
    )
    assert result == {"data": {"term": 2, "column": 4}}
    assert count.call_args.args[1] == {"column"}
    assert count.call_args.kwargs["synonym_tokens"] == ["id"]


@patch("gsf.server.search.service.search_dal.ensure_search_indexes")
@patch("gsf.server.search.service.search_dal.fetch_discovery")
def test_discovery_search_keeps_matching_synonyms_only(
    fetch: MagicMock, _ensure: MagicMock
) -> None:
    fetch.return_value = [
        {
            "id": "term-1",
            "name": "Business Unit",
            "type": "term",
            "description": None,
            "certified": "pending",
            "parent_id": None,
            "breadcrumbs": [],
            "synonyms": ["BU", "Org"],
        },
    ]
    result = discovery_search(
        search_term="BU",
        text_match_option="contains",
        objects=["term"],
        include_description=False,
    )
    assert result["data"][0]["synonyms"] == ["BU"]
    assert fetch.call_args.kwargs["synonym_tokens"] == ["bu"]


@patch("gsf.server.search.service.search_dal.LIST_LIMIT", 3)
@patch("gsf.server.search.service.search_dal.ensure_search_indexes")
@patch("gsf.server.search.service.search_dal.fetch_discovery")
def test_synonym_only_term_survives_list_cap(
    fetch: MagicMock, _ensure: MagicMock
) -> None:
    fetch.return_value = [
        {
            "id": "col-1",
            "name": "customer_a",
            "type": "column",
            "description": None,
            "certified": None,
            "parent_id": None,
            "breadcrumbs": [],
            "synonyms": [],
        },
        {
            "id": "col-2",
            "name": "customer_b",
            "type": "column",
            "description": None,
            "certified": None,
            "parent_id": None,
            "breadcrumbs": [],
            "synonyms": [],
        },
        {
            "id": "col-3",
            "name": "customer_c",
            "type": "column",
            "description": None,
            "certified": None,
            "parent_id": None,
            "breadcrumbs": [],
            "synonyms": [],
        },
        {
            "id": "term-syn",
            "name": "Cluster Passport",
            "type": "term",
            "description": None,
            "certified": "pending",
            "parent_id": None,
            "breadcrumbs": [],
            "synonyms": ["Customer"],
        },
    ]
    result = discovery_search(
        search_term="Customer",
        text_match_option="contains",
        objects=None,
        include_description=True,
    )
    ids = [item["id"] for item in result["data"]]
    assert "term-syn" in ids
    assert len(result["data"]) == 3
    assert result["data"][0]["id"] != "term-syn"
