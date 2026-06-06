"""Tests for Neo4j DAL merge key behavior (mocked driver)."""

from __future__ import annotations

from unittest.mock import MagicMock, patch

from gsf.semantic import neo4j_dal


@patch("gsf.semantic.neo4j_dal.get_neo4j_conn")
def test_merge_term_uses_name_and_source(mock_conn: MagicMock) -> None:
    mock_conn.return_value = MagicMock()
    neo4j_dal.merge_term("Orders", "Order entity", "table-1")
    call = mock_conn.return_value.query_write.call_args
    query, params = call[0][0], call[0][1]
    assert "MERGE (term:Term" in query or "MERGE (term:" in query
    assert params["name"] == "Orders"
    assert params["source"] == neo4j_dal.SEMANTIC_SOURCE


@patch("gsf.semantic.neo4j_dal.get_neo4j_conn")
def test_merge_is_a_skips_self_edge(mock_conn: MagicMock) -> None:
    mock_conn.return_value = MagicMock()
    neo4j_dal.merge_is_a("Same", "Same")
    mock_conn.return_value.query_write.assert_not_called()
