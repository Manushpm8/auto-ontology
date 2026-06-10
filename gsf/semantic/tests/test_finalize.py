"""Tests for deferred ROLE synthesis in finalize."""

from __future__ import annotations

from unittest.mock import MagicMock, patch

from gsf.semantic.visit_finalize import (
    _write_fk_role_edges,
    resolve_single_hop_role_intents,
)


@patch("gsf.semantic.visit_finalize.neo4j_dal")
@patch("gsf.semantic.visit_finalize.compute_join_path")
def test_write_fk_role_edges(
    mock_join_path: MagicMock,
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
    mock_join_path.return_value = [{"node": "orders"}, {"node": "customers"}]

    count = _write_fk_role_edges("t1")
    assert count == 1
    mock_dal.merge_role_edge.assert_called_once()


@patch("gsf.semantic.visit_finalize.neo4j_dal")
@patch("gsf.semantic.visit_finalize.resolve_single_hop_join")
def test_resolve_single_hop_writes_edge(
    mock_single_hop: MagicMock,
    mock_dal: MagicMock,
) -> None:
    from gsf.semantic.models import BusinessQuestionItem

    mock_dal.get_table_for_term.return_value = {"id": "t2", "name": "customers"}
    mock_single_hop.return_value = [
        {
            "hop": 1,
            "source": {"table": "orders", "column": "customer_id"},
            "target": {"table": "customers", "column": "id"},
        }
    ]

    written, unresolved = resolve_single_hop_role_intents(
        "t1",
        "orders",
        [
            (
                "Order",
                BusinessQuestionItem(
                    question="Who placed the order?",
                    entity="Customer",
                    role="placedBy",
                ),
            )
        ],
        src_table={"id": "t1", "name": "orders", "pk": "id"},
        src_ctx={"columns": [], "fks": []},
        suggested_fk_names={"customer_id"},
    )

    assert written == 1
    assert unresolved == []
    mock_dal.merge_role_edge.assert_called_once_with(
        source_term="Order",
        target_term="Customer",
        role_name="placedBy",
        join_path=mock_single_hop.return_value,
        source_table="orders",
        target_table="customers",
    )


@patch("gsf.semantic.visit_finalize.neo4j_dal")
@patch("gsf.semantic.visit_finalize.resolve_single_hop_join", return_value=None)
def test_resolve_single_hop_returns_unresolved(
    _mock_single_hop: MagicMock,
    mock_dal: MagicMock,
) -> None:
    from gsf.semantic.models import BusinessQuestionItem

    mock_dal.get_table_for_term.return_value = {"id": "t2", "name": "customers"}

    item = BusinessQuestionItem(
        question="Who placed the order?",
        entity="Customer",
        role="placedBy",
    )
    written, unresolved = resolve_single_hop_role_intents(
        "t1",
        "orders",
        [("Order", item)],
        src_table={"id": "t1", "name": "orders", "pk": "id"},
        src_ctx={"columns": [], "fks": []},
        suggested_fk_names=set(),
    )

    assert written == 0
    assert len(unresolved) == 1
    assert unresolved[0] == ("Order", item)
    mock_dal.merge_role_edge.assert_not_called()
