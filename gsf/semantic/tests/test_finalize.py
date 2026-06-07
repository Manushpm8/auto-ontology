"""Tests for deferred ROLE synthesis in finalize."""

from __future__ import annotations

from unittest.mock import MagicMock, patch

from gsf.semantic.visit_finalize import visit_finalize, write_question_role_edges


@patch("gsf.semantic.visit_finalize.neo4j_dal")
@patch("gsf.semantic.visit_finalize.compute_semantic_layer_path")
@patch("gsf.semantic.visit_finalize.compute_data_layer_path")
def test_finalize_writes_fk_role_edges(
    mock_data_path: MagicMock,
    mock_sem_path: MagicMock,
    mock_dal: MagicMock,
) -> None:
    mock_dal.fetch_fk_role_pairs.return_value = [
        {
            "source_table_id": "t1",
            "target_table_id": "t2",
            "source_table": "orders",
            "target_table": "customers",
            "source_term": "Order",
            "target_term": "Customer",
            "source_column": "customer_id",
        }
    ]
    mock_data_path.return_value = [{"node": "orders"}, {"node": "customers"}]
    mock_sem_path.return_value = [{"node": "Order"}, {"node": "Customer"}]

    count = visit_finalize("t1")
    assert count == 1
    mock_dal.merge_role_edge.assert_called_once()


@patch("gsf.semantic.visit_finalize.neo4j_dal")
@patch("gsf.semantic.visit_finalize.compute_semantic_layer_path")
@patch("gsf.semantic.visit_finalize.compute_data_layer_path")
def test_write_question_role_edges(
    mock_data_path: MagicMock,
    mock_sem_path: MagicMock,
    mock_dal: MagicMock,
) -> None:
    from gsf.semantic.models import BusinessQuestionItem

    mock_dal.get_table_for_term.side_effect = [
        {"id": "t2", "name": "customers"},
    ]
    mock_data_path.return_value = [{"node": "orders"}, {"node": "customers"}]
    mock_sem_path.return_value = [{"node": "Order"}, {"node": "Customer"}]

    count = write_question_role_edges(
        "t1",
        "Order",
        "orders",
        [
            BusinessQuestionItem(
                question="Who placed the order?",
                entity="Customer",
                role="placedBy",
            )
        ],
    )
    assert count == 1
    mock_dal.merge_role_edge.assert_called_once_with(
        source_term="Order",
        target_term="Customer",
        role_name="placedBy",
        path_data_layer=mock_data_path.return_value,
        path_semantic_layer=mock_sem_path.return_value,
        source_table="orders",
        target_table="customers",
    )
