# SPDX-FileCopyrightText: Copyright (c) 2024-2026, NVIDIA CORPORATION & AFFILIATES.
# All rights reserved.
# SPDX-License-Identifier: Apache-2.0

"""Unit tests for global-search Lucene escaping and object-type flags."""

from __future__ import annotations

from gsf.dal.search import (
    SEARCH_OBJECT_TYPES,
    _fulltext_source,
    _list_hit_source,
    _search_params,
    _synonym_term_source,
    build_lucene_query,
    filter_special_characters,
    synonym_matches_tokens,
    synonym_word_tokens,
)


def test_filter_special_characters_replaces_lucene_ops_with_spaces() -> None:
    assert filter_special_characters("foo/bar*baz") == "foo bar baz"


def test_build_lucene_query_wraps_a_single_token() -> None:
    assert build_lucene_query("revenue") == "*revenue*"


def test_build_lucene_query_ands_multi_word_tokens() -> None:
    assert build_lucene_query("annual revenue") == "*annual* AND *revenue*"


def test_build_lucene_query_collapses_stripped_specials() -> None:
    assert build_lucene_query("rev/enue") == "*rev* AND *enue*"


def test_build_lucene_query_empty_after_strip() -> None:
    assert build_lucene_query("!!!") == ""


def test_search_params_pass_requested_types() -> None:
    params = _search_params(set(SEARCH_OBJECT_TYPES))
    assert "Term" in params["object_types"]
    assert "View" in params["object_types"]
    assert "Table" in params["object_types"]
    assert "view" in params["view_types"]
    assert "Term" in params["search_labels"]


def test_search_params_narrow_to_requested_types() -> None:
    params = _search_params({"Term", "Column"})
    assert set(params["object_types"]) == {"Term", "Column"}
    assert "Term" in params["search_labels"]


def test_search_object_type_uses_graph_labels_and_view_table_type() -> None:
    from gsf.dal.search import SEARCH_TYPE_VIEW, search_object_type

    assert search_object_type("Term") == "Term"
    assert search_object_type("ColumnAttribute") == "ColumnAttribute"
    assert search_object_type("CustomAnalysis") == "CustomAnalysis"
    assert search_object_type("Table", "BASE TABLE") == "Table"
    assert search_object_type("Table", "view") == SEARCH_TYPE_VIEW
    assert search_object_type("Table", "materialized view") == SEARCH_TYPE_VIEW
    assert search_object_type(None) is None


def test_fulltext_source_omits_description_index_when_disabled() -> None:
    source = _fulltext_source(False)
    assert "gsf_name_index" in source
    assert "gsf_description_index" not in source


def test_fulltext_source_unions_description_index_when_enabled() -> None:
    source = _fulltext_source(True)
    assert "gsf_name_index" in source
    assert "gsf_description_index" in source
    assert "UNION" in source


def test_synonym_word_tokens_are_alphanumeric_whole_words() -> None:
    assert synonym_word_tokens("BU") == ["bu"]
    assert synonym_word_tokens("Business Unit") == ["business", "unit"]
    assert synonym_word_tokens("rev/enue") == ["rev", "enue"]


def test_synonym_matches_tokens_requires_whole_words_in_one_synonym() -> None:
    assert synonym_matches_tokens("BU", ["bu"]) is True
    assert synonym_matches_tokens("BU", ["b"]) is False
    assert synonym_matches_tokens("Business Unit", ["unit"]) is True
    assert synonym_matches_tokens("Business Unit", ["uni"]) is False
    assert synonym_matches_tokens("Business Unit", ["business", "unit"]) is True
    assert synonym_matches_tokens("Business", ["business", "unit"]) is False


def test_synonym_term_source_uses_word_boundary_regex() -> None:
    source = _synonym_term_source()
    assert "n.synonyms" in source
    assert "(^|[^a-z0-9])" in source
    assert "REPRESENTS" in source
    assert "'Term' IN $object_types" in source


def test_visibility_where_uses_object_types_not_allow_flags() -> None:
    from gsf.dal.search import _visibility_where

    where = _visibility_where()
    assert "$allow_" not in where
    assert "IN $object_types" in where
    assert "REPRESENTS" in where


def test_list_hit_source_unions_synonyms_after_fulltext_limit() -> None:
    source = _list_hit_source(False)
    assert "gsf_name_index" in source
    assert "LIMIT $limit" in source
    assert "n.synonyms" in source
    limit_at = source.index("LIMIT $limit")
    synonyms_at = source.index("n.synonyms")
    assert limit_at < synonyms_at


def test_view_table_types_remap_to_view() -> None:
    from gsf.dal.search import is_view_table_type

    assert is_view_table_type("view") is True
    assert is_view_table_type("materialized view") is True
    assert is_view_table_type("VIEW") is True
    assert is_view_table_type("base table") is False
    assert is_view_table_type(None) is False


def test_fetch_query_returns_graph_label_not_invented_type() -> None:
    from gsf.dal.search import _canonical_label

    label = _canonical_label()
    assert "labels(n)" in label
    assert "$search_labels" in label
