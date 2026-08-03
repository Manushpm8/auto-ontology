# SPDX-FileCopyrightText: Copyright (c) 2026, NVIDIA CORPORATION & AFFILIATES.
# All rights reserved.
# SPDX-License-Identifier: Apache-2.0

from unittest.mock import MagicMock, patch

from nemo_retriever.tabular_data.ingestion.model.reserved_words import Labels

from gsf.retrieval.text_to_sql.agents.candidates_retrieval import (
    _CAND_KEEP_K,
    _CAND_RETRIEVE_K,
    _MAX_VECTOR_DISTANCE,
    CandidateRetrievalAgent,
    _column_attribute_rerank_query,
    _merge_column_attribute_hits,
    _rerank_hits,
    _search_label_bucket,
    _select_column_attributes_with_entity_coverage,
)
from gsf.semantic.constants import LABEL_COLUMN_ATTRIBUTE, LABEL_SQL_ATTRIBUTE


@patch("gsf.retrieval.text_to_sql.agents.candidates_retrieval.get_rerank_kwargs")
@patch("gsf.retrieval.text_to_sql.agents.candidates_retrieval.rerank_hits")
def test_rerank_hits_keeps_requested_count(
    mock_rerank: MagicMock,
    mock_kwargs: MagicMock,
) -> None:
    hits = [{"id": str(i), "text": f"candidate {i}"} for i in range(10)]
    ranked = [hits[7], hits[2], hits[5]]
    mock_rerank.return_value = ranked
    mock_kwargs.return_value = {"model_name": "reranker"}

    result = _rerank_hits(
        hits,
        "cluster failures",
        LABEL_COLUMN_ATTRIBUTE,
        keep_k=3,
    )

    assert result == ranked
    mock_rerank.assert_called_once_with(
        "cluster failures",
        hits,
        top_n=10,
        model_name="reranker",
    )


def test_column_attribute_rerank_query_combines_question_and_entities() -> None:
    assert _column_attribute_rerank_query(
        "Which clusters are failing?",
        ["clusters", "link transition to down state"],
    ) == (
        "Question: Which clusters are failing?\n"
        "Entities: clusters, link transition to down state"
    )


@patch("gsf.retrieval.text_to_sql.agents.candidates_retrieval.rerank_hits")
def test_rerank_hits_skips_two_or_fewer_items(mock_rerank: MagicMock) -> None:
    hits = [
        {"id": "a", "text": "first", "score": 0.1},
        {"id": "b", "text": "second", "score": 0.2},
    ]

    result = _rerank_hits(
        hits,
        "cluster",
        LABEL_COLUMN_ATTRIBUTE,
        keep_k=3,
    )

    assert result == hits
    mock_rerank.assert_not_called()


@patch(
    "gsf.retrieval.text_to_sql.agents.candidates_retrieval."
    "search_semantic_index_by_vectors"
)
def test_search_label_bucket_drops_large_vector_distances(
    mock_search: MagicMock,
) -> None:
    mock_search.return_value = [
        [
            {"id": "good", "score": _MAX_VECTOR_DISTANCE},
            {"id": "weak", "score": _MAX_VECTOR_DISTANCE + 0.01},
        ]
    ]

    result = _search_label_bucket(
        object(),
        [[0.1]],
        LABEL_COLUMN_ATTRIBUTE,
        top_k=10,
        database_name="testdb",
    )

    assert result == [[{"id": "good", "score": _MAX_VECTOR_DISTANCE}]]


@patch(
    "gsf.retrieval.text_to_sql.agents.candidates_retrieval."
    "search_semantic_index_by_vectors"
)
def test_search_label_bucket_keeps_best_fallback_for_column_entity(
    mock_search: MagicMock,
) -> None:
    mock_search.return_value = [
        [
            {"id": "closest", "score": 0.8},
            {"id": "farther", "score": 0.9},
        ]
    ]

    result = _search_label_bucket(
        object(),
        [[0.1]],
        LABEL_COLUMN_ATTRIBUTE,
        top_k=10,
        database_name="testdb",
    )

    assert result == [[{"id": "closest", "score": 0.8}]]


def test_column_attribute_selection_guarantees_per_entity_coverage() -> None:
    hits_per_entity = [
        [
            {"id": "shared", "score": 0.1},
            {"id": "clusters-only", "score": 0.2},
        ],
        [
            {"id": "shared", "score": 0.15},
            {"id": "failures-only", "score": 0.3},
        ],
    ]
    merged, matched = _merge_column_attribute_hits(
        hits_per_entity, ["clusters", "failures"]
    )
    # Global rerank strongly favors the shared and cluster-only candidates.
    ranked = [
        next(hit for hit in merged if hit["id"] == "shared"),
        next(hit for hit in merged if hit["id"] == "clusters-only"),
        next(hit for hit in merged if hit["id"] == "failures-only"),
    ]

    selected = _select_column_attributes_with_entity_coverage(
        ranked,
        matched,
        ["clusters", "failures"],
        keep_k=2,
    )

    assert [hit["id"] for hit in selected] == ["shared", "failures-only"]
    assert selected[0]["_matched_entities"] == ["clusters", "failures"]
    assert selected[1]["_matched_entities"] == ["failures"]


@patch("gsf.retrieval.text_to_sql.agents.candidates_retrieval._rerank_hits")
@patch(
    "gsf.retrieval.text_to_sql.agents.candidates_retrieval.search_semantic_index_by_vectors"
)
@patch("gsf.retrieval.text_to_sql.agents.candidates_retrieval.embed_query_texts")
def test_agent_batches_embed_then_buckets_searches(
    mock_embed: MagicMock,
    mock_search: MagicMock,
    mock_rerank: MagicMock,
) -> None:
    question = "Which clusters are failing?"
    # One vector per embed_query_texts input: question + 2 entities.
    mock_embed.return_value = [[0.1], [0.2], [0.3]]

    def _search(
        _retriever: object,
        vectors: list[list[float]],
        *,
        label: str,
        top_k: int,
        database_name: str | None = None,
        schema_name: str | None = None,
    ) -> list[list[dict]]:
        return [
            [{"id": f"{label}:{i}", "text": label, "score": 0.1}]
            for i, _ in enumerate(vectors)
        ]

    mock_search.side_effect = _search
    mock_rerank.side_effect = lambda hits, query, label, keep_k: hits[:keep_k]

    state = {
        "initial_question": question,
        "path_state": {
            "entities": ["clusters", "failures"],
            "target_db": "testdb",
        },
        "semantic_retriever": MagicMock(vdb_kwargs={"vdb": object()}),
    }

    result = CandidateRetrievalAgent().execute(state)  # type: ignore[arg-type]

    mock_embed.assert_called_once_with([question, "clusters", "failures"])

    search_calls = {
        (
            tuple(tuple(v) for v in call.kwargs.get("vectors") or call.args[1]),
            call.kwargs["label"],
            call.kwargs["top_k"],
            call.kwargs.get("database_name"),
        )
        for call in mock_search.call_args_list
    }
    assert search_calls == {
        (((0.1,),), Labels.CUSTOM_ANALYSIS, _CAND_RETRIEVE_K, "testdb"),
        (((0.1,),), LABEL_SQL_ATTRIBUTE, _CAND_RETRIEVE_K, "testdb"),
        (((0.2,), (0.3,)), LABEL_COLUMN_ATTRIBUTE, _CAND_RETRIEVE_K, "testdb"),
    }

    rerank_calls = {
        (
            call.args[1],
            call.args[2],
            call.args[3],
        )
        for call in mock_rerank.call_args_list
    }
    assert rerank_calls == {
        (question, Labels.CUSTOM_ANALYSIS, _CAND_KEEP_K),
        (question, LABEL_SQL_ATTRIBUTE, _CAND_KEEP_K),
        (
            "Question: Which clusters are failing?\nEntities: clusters, failures",
            LABEL_COLUMN_ATTRIBUTE,
            2,
        ),
    }

    path_state = result["path_state"]
    assert len(path_state["retrieved_custom_analyses"]) == 1
    assert len(path_state["retrieved_sql_attributes"]) == 1
    assert len(path_state["retrieved_column_attributes"]) == 2
    assert path_state["retrieved_column_attributes"][0]["_matched_entities"]
