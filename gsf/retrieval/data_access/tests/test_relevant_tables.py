# SPDX-FileCopyrightText: Copyright (c) 2026, NVIDIA CORPORATION & AFFILIATES.
# All rights reserved.
# SPDX-License-Identifier: Apache-2.0

from nemo_retriever.tabular_data.ingestion.model.reserved_words import Labels

from gsf.retrieval.data_access import relevant_tables


def test_normalize_keeps_the_key_when_applied_twice() -> None:
    """The candidate path normalizes once per module, so the shape must be stable.

    A rename here (``pk`` -> something else) silently loses the key on the second
    pass, which costs the prediction graph its entity and every edge with it.
    """
    table = {
        "id": "table-1",
        "name": "Store Locations",
        "label": Labels.TABLE,
        "text": "table_name: Store Locations",
        "pk": ["StoreID"],
    }

    once = relevant_tables._normalize_table_to_relevant_shape(table)
    twice = relevant_tables._normalize_table_to_relevant_shape(once)

    assert once["pk"] == ["StoreID"]
    assert twice["pk"] == ["StoreID"]


def test_get_relevant_tables_from_candidates_keeps_an_already_normalized_key() -> None:
    """``_get_candidates_information`` normalizes before this function sees the dict."""
    normalized = relevant_tables._normalize_table_to_relevant_shape(
        {
            "id": "table-1",
            "name": "Store Locations",
            "label": Labels.TABLE,
            "pk": ["StoreID"],
        }
    )

    result = relevant_tables.get_relevant_tables_from_candidates(
        [{"id": "col-1", "relevant_tables": [normalized]}]
    )

    assert result[0]["pk"] == ["StoreID"]


def test_dedupe_merge_keeps_the_key_of_an_already_normalized_table() -> None:
    """``dedupe_merge_relevant_tables`` normalizes again after merging."""
    normalized = relevant_tables._normalize_table_to_relevant_shape(
        {
            "id": "table-1",
            "name": "Store Locations",
            "label": Labels.TABLE,
            "pk": ["StoreID"],
        }
    )

    merged = relevant_tables.dedupe_merge_relevant_tables([normalized, normalized])

    assert merged[0]["pk"] == ["StoreID"]
