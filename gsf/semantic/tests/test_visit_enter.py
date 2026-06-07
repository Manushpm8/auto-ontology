"""Tests for enter-phase semantic writes and question ROLE edges."""

from __future__ import annotations

from unittest.mock import MagicMock, patch

from gsf.semantic.visit_enter import visit_enter
from gsf.semantic.queue import TablesQueue


@patch("gsf.semantic.visit_enter.neo4j_dal")
@patch("gsf.semantic.visit_enter.extract_term")
@patch("gsf.semantic.visit_enter.generate_business_questions")
@patch("gsf.semantic.visit_enter.discover_tables_via_vdb")
@patch("gsf.semantic.visit_enter.write_question_role_edges")
def test_enter_writes_question_roles_after_discovery(
    mock_write_roles: MagicMock,
    mock_vdb: MagicMock,
    mock_questions: MagicMock,
    mock_term: MagicMock,
    mock_dal: MagicMock,
) -> None:
    from gsf.semantic.models import (
        BusinessQuestionItem,
        BusinessQuestionsResult,
        TermProposal,
    )

    mock_term.return_value = TermProposal(name="Order", description="Order entity")
    items = [
        BusinessQuestionItem(
            question="Who placed orders?",
            entity="Customer",
            role="placedBy",
        )
    ]
    mock_questions.return_value = BusinessQuestionsResult(items=items)
    mock_vdb.return_value = []
    mock_write_roles.return_value = 1

    table = {"id": "t1", "name": "orders", "description": ""}
    ctx = {
        "columns": [{"name": "amount", "data_type": "numeric"}],
        "fks": [],
    }
    queue = TablesQueue({"orders": table}, join_edges=[])
    retriever = MagicMock()

    visit_enter(
        table,
        ctx,
        queue=queue,
        hop=0,
        retriever=retriever,
        domain_summary=None,
    )

    mock_dal.merge_term.assert_called_once()
    mock_write_roles.assert_called_once_with("t1", "Order", "orders", items)
    mock_dal.merge_role_edge.assert_not_called()


@patch("gsf.semantic.visit_enter.neo4j_dal")
@patch("gsf.semantic.visit_enter.extract_term")
@patch("gsf.semantic.visit_enter.write_question_role_edges")
def test_enter_skips_question_roles_without_retriever(
    mock_write_roles: MagicMock,
    mock_term: MagicMock,
    mock_dal: MagicMock,
) -> None:
    from gsf.semantic.models import TermProposal

    mock_term.return_value = TermProposal(name="Order", description="Order entity")

    table = {"id": "t1", "name": "orders", "description": ""}
    ctx = {
        "columns": [{"name": "amount", "data_type": "numeric"}],
        "fks": [],
    }
    queue = TablesQueue({"orders": table}, join_edges=[])

    visit_enter(
        table,
        ctx,
        queue=queue,
        hop=0,
        retriever=None,
        domain_summary=None,
    )

    mock_write_roles.assert_not_called()
    mock_dal.merge_role_edge.assert_not_called()
