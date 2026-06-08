"""Tests for enter-phase semantic writes and question ROLE edges."""

from __future__ import annotations

from unittest.mock import MagicMock, patch

from gsf.semantic.visit_enter import visit_enter
from gsf.semantic.queue import TablesQueue


@patch("gsf.semantic.visit_enter.neo4j_dal")
@patch("gsf.semantic.visit_enter.suggest_potential_foreign_keys")
@patch("gsf.semantic.visit_enter.extract_term")
@patch("gsf.semantic.visit_enter.generate_business_questions")
@patch("gsf.semantic.visit_enter.discover_tables_via_vdb")
@patch("gsf.semantic.visit_enter.write_question_role_edges")
def test_enter_writes_question_roles_after_discovery(
    mock_write_roles: MagicMock,
    mock_vdb: MagicMock,
    mock_questions: MagicMock,
    mock_term: MagicMock,
    mock_fk_suggest: MagicMock,
    mock_dal: MagicMock,
) -> None:
    from gsf.semantic.models import (
        BusinessQuestionItem,
        BusinessQuestionsResult,
        PotentialFkResult,
        TableTermsResult,
        TermAttributeAssignment,
        TermProposal,
    )

    mock_term.return_value = TableTermsResult(
        terms=[
            TermProposal(
                name="Order",
                description="Order entity",
                attributes=[
                    TermAttributeAssignment(
                        source_column="amount",
                        display_name="totalAmount",
                    )
                ],
            )
        ]
    )
    mock_fk_suggest.return_value = PotentialFkResult()
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
    mock_dal.mark_suspected_foreign_keys.assert_called_once_with("t1", [])
    mock_write_roles.assert_called_once_with("t1", "Order", "orders", items)
    mock_dal.merge_role_edge.assert_not_called()


@patch("gsf.semantic.visit_enter.neo4j_dal")
@patch("gsf.semantic.visit_enter.suggest_potential_foreign_keys")
@patch("gsf.semantic.visit_enter.extract_term")
@patch("gsf.semantic.visit_enter.write_question_role_edges")
def test_enter_skips_question_roles_without_retriever(
    mock_write_roles: MagicMock,
    mock_term: MagicMock,
    mock_fk_suggest: MagicMock,
    mock_dal: MagicMock,
) -> None:
    from gsf.semantic.models import PotentialFkResult, TableTermsResult, TermProposal

    mock_term.return_value = TableTermsResult(
        terms=[TermProposal(name="Order", description="Order entity")]
    )
    mock_fk_suggest.return_value = PotentialFkResult()

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


@patch("gsf.semantic.visit_enter.neo4j_dal")
@patch("gsf.semantic.visit_enter.suggest_potential_foreign_keys")
@patch("gsf.semantic.visit_enter.extract_term")
@patch("gsf.semantic.visit_enter.generate_business_questions")
@patch("gsf.semantic.visit_enter.discover_tables_via_vdb")
@patch("gsf.semantic.visit_enter.write_question_role_edges")
def test_enter_unions_questions_from_all_terms(
    mock_write_roles: MagicMock,
    mock_vdb: MagicMock,
    mock_questions: MagicMock,
    mock_term: MagicMock,
    mock_fk_suggest: MagicMock,
    mock_dal: MagicMock,
) -> None:
    from gsf.semantic.models import (
        BusinessQuestionItem,
        BusinessQuestionsResult,
        PotentialFkResult,
        TableTermsResult,
        TermProposal,
    )

    mock_fk_suggest.return_value = PotentialFkResult()
    mock_term.return_value = TableTermsResult(
        terms=[
            TermProposal(name="Order", description="Order entity"),
            TermProposal(name="OrderLine", description="Line item"),
        ]
    )

    def _questions_for_term(
        _table: dict,
        _ctx: dict,
        term_name: str,
    ) -> BusinessQuestionsResult:
        return BusinessQuestionsResult(
            items=[
                BusinessQuestionItem(
                    question=f"Question for {term_name}?",
                    entity="Customer",
                    role="relatedTo",
                )
            ]
        )

    mock_questions.side_effect = _questions_for_term
    mock_vdb.return_value = []
    mock_write_roles.return_value = 1

    table = {"id": "t1", "name": "orders", "description": ""}
    ctx = {"columns": [{"name": "amount", "data_type": "numeric"}], "fks": []}
    queue = TablesQueue({"orders": table}, join_edges=[])

    visit_enter(
        table,
        ctx,
        queue=queue,
        hop=0,
        retriever=MagicMock(),
        domain_summary=None,
    )

    assert mock_questions.call_count == 2
    mock_vdb.assert_called_once()
    discovered_items = mock_vdb.call_args[0][0]
    assert len(discovered_items) == 2
    assert mock_write_roles.call_count == 2
