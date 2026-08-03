# SPDX-FileCopyrightText: Copyright (c) 2026, NVIDIA CORPORATION & AFFILIATES.
# All rights reserved.
# SPDX-License-Identifier: Apache-2.0

from unittest.mock import MagicMock, patch

from nemo_retriever.tabular_data.ingestion.model.reserved_words import Labels

from gsf.retrieval.text_to_sql.agents.candidates_retrieval import (
    _CAND_KEEP_K,
    _CAND_RETRIEVE_K,
    CandidateRetrievalAgent,
    _search_and_rerank,
)
from gsf.semantic.constants import LABEL_COLUMN_ATTRIBUTE, LABEL_SQL_ATTRIBUTE


@patch("gsf.retrieval.text_to_sql.agents.candidates_retrieval.get_rerank_kwargs")
@patch("gsf.retrieval.text_to_sql.agents.candidates_retrieval.rerank_hits")
@patch("gsf.retrieval.text_to_sql.agents.candidates_retrieval._search_by_label")
def test_search_and_rerank_keeps_requested_count(
    mock_search: MagicMock,
    mock_rerank: MagicMock,
    mock_kwargs: MagicMock,
) -> None:
    hits = [{"id": str(i), "text": f"candidate {i}"} for i in range(10)]
    ranked = [hits[7], hits[2], hits[5]]
    mock_search.return_value = hits
    mock_rerank.return_value = ranked
    mock_kwargs.return_value = {"model_name": "reranker"}

    result = _search_and_rerank(
        object(),
        "cluster failures",
        LABEL_COLUMN_ATTRIBUTE,
        retrieve_k=10,
        keep_k=3,
        database_name="oasis",
    )

    assert result == ranked
    mock_rerank.assert_called_once_with(
        "cluster failures",
        hits,
        top_n=10,
        model_name="reranker",
    )


@patch("gsf.retrieval.text_to_sql.agents.candidates_retrieval.get_rerank_kwargs")
@patch("gsf.retrieval.text_to_sql.agents.candidates_retrieval.rerank_hits")
@patch("gsf.retrieval.text_to_sql.agents.candidates_retrieval._search_by_label")
def test_search_and_rerank_fuses_entity_and_question_rankings(
    mock_search: MagicMock,
    mock_rerank: MagicMock,
    mock_kwargs: MagicMock,
) -> None:
    hits = [{"id": hit_id, "text": hit_id} for hit_id in ("a", "b", "c", "d")]
    mock_search.return_value = hits
    mock_rerank.side_effect = [
        [hits[0], hits[1], hits[2], hits[3]],
        [hits[3], hits[1], hits[2], hits[0]],
    ]
    mock_kwargs.return_value = {}

    result = _search_and_rerank(
        object(),
        "cluster",
        LABEL_COLUMN_ATTRIBUTE,
        retrieve_k=10,
        keep_k=1,
        database_name="oasis",
        question="Which clusters are failing?",
    )

    assert [hit["id"] for hit in result] == ["b"]
    assert mock_rerank.call_args_list[0].args[0] == "cluster"
    assert mock_rerank.call_args_list[1].args[0] == "Which clusters are failing?"


@patch("gsf.retrieval.text_to_sql.agents.candidates_retrieval._search_and_rerank")
def test_agent_reranks_each_search_with_its_own_query(mock_search: MagicMock) -> None:
    mock_search.side_effect = lambda _retriever, query, label, *_args: [
        {"id": f"{label}:{query}", "text": query, "score": 0.1}
    ]
    state = {
        "initial_question": "Which clusters are failing?",
        "path_state": {
            "entities": ["clusters", "failures"],
            "target_db": "oasis",
        },
        "semantic_retriever": object(),
    }

    result = CandidateRetrievalAgent().execute(state)  # type: ignore[arg-type]

    calls = {
        (
            call.args[1],
            call.args[2],
            call.args[3],
            call.args[4],
            call.args[6] if len(call.args) > 6 else None,
        )
        for call in mock_search.call_args_list
    }
    assert calls == {
        (
            "Which clusters are failing?",
            Labels.CUSTOM_ANALYSIS,
            _CAND_RETRIEVE_K,
            _CAND_KEEP_K,
            None,
        ),
        (
            "Which clusters are failing?",
            LABEL_SQL_ATTRIBUTE,
            _CAND_RETRIEVE_K,
            _CAND_KEEP_K,
            None,
        ),
        (
            "clusters",
            LABEL_COLUMN_ATTRIBUTE,
            _CAND_RETRIEVE_K,
            _CAND_KEEP_K,
            "Which clusters are failing?",
        ),
        (
            "failures",
            LABEL_COLUMN_ATTRIBUTE,
            _CAND_RETRIEVE_K,
            _CAND_KEEP_K,
            "Which clusters are failing?",
        ),
    }
    path_state = result["path_state"]
    assert len(path_state["retrieved_custom_analyses"]) == 1
    assert len(path_state["retrieved_sql_attributes"]) == 1
    assert len(path_state["retrieved_column_attributes"]) == 2
