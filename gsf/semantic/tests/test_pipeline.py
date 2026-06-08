"""Tests for BFS pipeline orchestration."""

from __future__ import annotations

from unittest.mock import MagicMock, patch

from gsf.semantic.pipeline import run_bfs_tree


@patch("gsf.semantic.pipeline.pick_orphan_seed", return_value=None)
@patch("gsf.semantic.pipeline.visit_finalize")
@patch("gsf.semantic.pipeline.visit_enter")
@patch("gsf.semantic.pipeline.fetch_table_context")
def test_bfs_skips_already_reviewed(
    mock_fetch_ctx: MagicMock,
    mock_enter: MagicMock,
    mock_finalize: MagicMock,
    _mock_orphan: MagicMock,
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


@patch("gsf.semantic.pipeline.visit_finalize")
@patch("gsf.semantic.pipeline.visit_enter")
@patch("gsf.semantic.pipeline.fetch_table_context")
def test_bfs_reseeds_from_orphan(
    mock_fetch_ctx: MagicMock,
    mock_enter: MagicMock,
    mock_finalize: MagicMock,
) -> None:
    seed = {"id": "t1", "name": "orders", "description": ""}
    orphan = {"id": "t2", "name": "orphan_table", "description": ""}
    tables_by_name = {"orders": seed, "orphan_table": orphan}
    tables_by_id = {"t1": seed, "t2": orphan}

    mock_fetch_ctx.side_effect = [
        {"reviewed": False, "columns": [{"name": "id"}], "fks": []},
        {"reviewed": False, "columns": [{"name": "id"}], "fks": []},
        {"reviewed": False, "columns": [{"name": "id"}], "fks": []},
    ]

    with patch(
        "gsf.semantic.pipeline.pick_orphan_seed",
        side_effect=[orphan, None],
    ):
        visit_order = run_bfs_tree(
            seed,
            tables_by_name=tables_by_name,
            tables_by_id=tables_by_id,
            join_edges=[],
            domain_summary=None,
            retriever=None,
            tree_index=0,
        )

    assert visit_order == ["t1", "t2"]
    assert mock_enter.call_count == 2
    assert mock_finalize.call_count == 2
