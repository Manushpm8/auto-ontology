"""Tests for BFS pipeline orchestration."""

from __future__ import annotations

from unittest.mock import MagicMock, patch

from gsf.semantic.pipeline import run_bfs_tree


@patch("gsf.semantic.pipeline.orphan_stitch")
@patch("gsf.semantic.pipeline.visit_finalize")
@patch("gsf.semantic.pipeline.visit_enter")
@patch("gsf.semantic.pipeline.fetch_table_context")
def test_bfs_skips_already_reviewed(
    mock_fetch_ctx: MagicMock,
    mock_enter: MagicMock,
    mock_finalize: MagicMock,
    mock_orphan: MagicMock,
) -> None:
    table = {"id": "t1", "name": "orders", "description": ""}
    mock_fetch_ctx.return_value = {
        "reviewed": True,
        "columns": [{"name": "amount", "data_type": "numeric"}],
        "fks": [],
    }

    visit_order = run_bfs_tree(
        table,
        tables_by_name={"orders": table},
        tables_by_id={"t1": table},
        join_edges=[],
        domain_summary=None,
        retriever=None,
        tree_index=0,
    )

    assert visit_order == []
    mock_enter.assert_not_called()
    mock_finalize.assert_not_called()
    mock_orphan.assert_called_once()
