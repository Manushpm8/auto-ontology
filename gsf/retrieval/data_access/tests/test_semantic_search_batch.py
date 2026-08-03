# SPDX-FileCopyrightText: Copyright (c) 2026, NVIDIA CORPORATION & AFFILIATES.
# All rights reserved.
# SPDX-License-Identifier: Apache-2.0

from unittest.mock import MagicMock, patch

from gsf.retrieval.data_access.semantic_search import search_semantic_index_by_vectors
from gsf.semantic.constants import LABEL_COLUMN_ATTRIBUTE
from gsf.utils.embedding import embed_query_texts


@patch("nemo_retriever.operators.vdb.query_vectors_from_embedded_dataframe")
@patch("nemo_retriever.models.inference.runtime.embed_text_main_text_embed")
def test_embed_query_texts_dedupes_and_preserves_order(
    mock_embed: MagicMock,
    mock_extract: MagicMock,
) -> None:
    mock_embed.return_value = MagicMock()
    mock_extract.return_value = [[1.0], [2.0], [3.0]]

    result = embed_query_texts(["q", "a", "q", "b", "a"])

    assert result == [[1.0], [2.0], [1.0], [3.0], [2.0]]
    # Only unique texts go to the HTTP call.
    df = mock_embed.call_args.args[0]
    assert list(df["text"]) == ["q", "a", "b"]
    assert mock_embed.call_args.kwargs["input_type"] == "query"


def test_search_semantic_index_by_vectors_preserves_bucket_order() -> None:
    vdb = MagicMock()
    vdb.metadata_filter_format = "dict"
    vdb.retrieval.return_value = [
        [
            {
                "text": "hit-a",
                "metadata": {"id": "a", "label": LABEL_COLUMN_ATTRIBUTE},
                "_distance": 0.1,
            }
        ],
        [
            {
                "text": "hit-b",
                "metadata": {"id": "b", "label": LABEL_COLUMN_ATTRIBUTE},
                "_distance": 0.2,
            }
        ],
    ]
    retriever = MagicMock()
    retriever.vdb_kwargs = {"vdb": vdb}

    results = search_semantic_index_by_vectors(
        retriever,
        [[0.1], [0.2]],
        label=LABEL_COLUMN_ATTRIBUTE,
        top_k=3,
        database_name="oasis",
    )

    assert [row[0]["id"] for row in results] == ["a", "b"]
    vdb.retrieval.assert_called_once_with(
        [[0.1], [0.2]],
        top_k=3,
        where={"label": LABEL_COLUMN_ATTRIBUTE, "database_name": "oasis"},
    )
