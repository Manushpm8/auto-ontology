"""Tests that finalize is separate from enter (ROLE not on enter)."""

from __future__ import annotations

from unittest.mock import MagicMock, patch

from gsf.semantic.visit_enter import visit_enter
from gsf.semantic.queue import TablesQueue


@patch("gsf.semantic.visit_enter.neo4j_dal")
@patch("gsf.semantic.visit_enter.extract_term")
@patch("gsf.semantic.visit_enter.generate_business_questions")
@patch("gsf.semantic.visit_enter.discover_tables_via_vdb")
def test_enter_does_not_merge_role(
    mock_vdb: MagicMock,
    mock_questions: MagicMock,
    mock_term: MagicMock,
    mock_dal: MagicMock,
) -> None:
    from gsf.semantic.models import TermProposal, BusinessQuestionsResult

    mock_term.return_value = TermProposal(name="Orders", description="Order entity")
    mock_questions.return_value = BusinessQuestionsResult(entities=["Customer"])
    mock_vdb.return_value = []

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

    mock_dal.mark_table_reviewed.assert_called_once()
    mock_dal.merge_term.assert_called_once()
    mock_dal.merge_column_attribute.assert_called()
    mock_dal.merge_role_edge.assert_not_called()
