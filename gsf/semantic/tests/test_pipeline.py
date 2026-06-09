"""Tests for the collapsed compile_semantic_layer seed-picking loop."""

from __future__ import annotations

from unittest.mock import MagicMock, patch

from gsf.semantic.pipeline import compile_semantic_layer


@patch("gsf.semantic.pipeline.visit_enter")
@patch("gsf.semantic.pipeline.fetch_table_context")
@patch("gsf.semantic.pipeline.fetch_table_by_id")
@patch("gsf.semantic.pipeline.pick_orphan_seed")
@patch("gsf.semantic.pipeline.select_seed_table")
@patch("gsf.semantic.pipeline.fetch_sorted_tables")
@patch("gsf.semantic.pipeline.build_data_retriever", return_value=None)
@patch("gsf.semantic.pipeline.load_domain_summary", return_value=None)
@patch("gsf.semantic.pipeline.neo4j_dal")
def test_compile_marks_reviewed_seed_and_advances(
    mock_dal: MagicMock,
    _mock_summary: MagicMock,
    _mock_build_retr: MagicMock,
    _mock_sorted: MagicMock,
    mock_select_seed: MagicMock,
    mock_pick_orphan: MagicMock,
    mock_fetch_by_id: MagicMock,
    mock_fetch_ctx: MagicMock,
    mock_visit: MagicMock,
) -> None:
    """Stale 'reviewed' seed should be marked + skipped, then loop terminates."""
    table = {"id": "t1", "name": "orders", "description": ""}
    mock_dal.discover_unreviewed_tables.side_effect = [[{"id": "t1"}], []]
    mock_pick_orphan.return_value = table
    mock_fetch_by_id.return_value = table
    mock_fetch_ctx.return_value = {"reviewed": True, "columns": [], "fks": []}

    count = compile_semantic_layer("dbx", resume=True)

    assert count == 0
    mock_visit.assert_not_called()
    mock_dal.mark_table_reviewed.assert_called_once_with("t1")
    mock_select_seed.assert_not_called()


@patch("gsf.semantic.pipeline.visit_enter")
@patch("gsf.semantic.pipeline.fetch_table_context")
@patch("gsf.semantic.pipeline.fetch_table_by_id")
@patch("gsf.semantic.pipeline.pick_orphan_seed")
@patch("gsf.semantic.pipeline.select_seed_table")
@patch("gsf.semantic.pipeline.fetch_sorted_tables")
@patch("gsf.semantic.pipeline.build_data_retriever", return_value=None)
@patch("gsf.semantic.pipeline.load_domain_summary", return_value=None)
@patch("gsf.semantic.pipeline.neo4j_dal")
def test_compile_reseeds_from_orphans_until_empty(
    mock_dal: MagicMock,
    _mock_summary: MagicMock,
    _mock_build_retr: MagicMock,
    _mock_sorted: MagicMock,
    mock_select_seed: MagicMock,
    mock_pick_orphan: MagicMock,
    _mock_fetch_by_id: MagicMock,
    mock_fetch_ctx: MagicMock,
    mock_visit: MagicMock,
) -> None:
    """Two disconnected tables -> two visit_enter calls -> count == 2."""
    seed = {"id": "t1", "name": "orders", "description": ""}
    orphan = {"id": "t2", "name": "audit_log", "description": ""}

    mock_dal.discover_unreviewed_tables.side_effect = [
        [{"id": "t1"}, {"id": "t2"}],
        [{"id": "t2"}],
        [],
    ]
    # No domain summary -> first_tree path is False; pick_orphan_seed drives seeds.
    mock_pick_orphan.side_effect = [seed, orphan]
    mock_fetch_ctx.side_effect = [
        {"reviewed": False, "columns": [{"name": "amount"}], "fks": []},
        {"reviewed": False, "columns": [{"name": "ts"}], "fks": []},
    ]

    count = compile_semantic_layer("dbx", resume=True)

    assert count == 2
    assert mock_visit.call_count == 2
    visited_ids = [call.args[0]["id"] for call in mock_visit.call_args_list]
    assert visited_ids == ["t1", "t2"]
    mock_select_seed.assert_not_called()


@patch("gsf.semantic.pipeline.visit_enter")
@patch("gsf.semantic.pipeline.fetch_table_context")
@patch("gsf.semantic.pipeline.fetch_table_by_id")
@patch("gsf.semantic.pipeline.pick_orphan_seed", return_value=None)
@patch("gsf.semantic.pipeline.select_seed_table")
@patch("gsf.semantic.pipeline.fetch_sorted_tables")
@patch("gsf.semantic.pipeline.build_data_retriever", return_value=None)
@patch("gsf.semantic.pipeline.load_domain_summary")
@patch("gsf.semantic.pipeline.neo4j_dal")
def test_compile_uses_llm_seed_when_summary_present(
    mock_dal: MagicMock,
    mock_summary: MagicMock,
    _mock_build_retr: MagicMock,
    mock_sorted: MagicMock,
    mock_select_seed: MagicMock,
    _mock_pick_orphan: MagicMock,
    _mock_fetch_by_id: MagicMock,
    mock_fetch_ctx: MagicMock,
    mock_visit: MagicMock,
) -> None:
    """When a DomainSummary is loaded, the first tree is seeded via select_seed_table."""
    seed = {"id": "t1", "name": "orders", "description": "", "query_count": 5}
    mock_summary.return_value = MagicMock(name="domain_summary")
    mock_sorted.return_value = [seed]
    mock_select_seed.return_value = seed
    mock_dal.discover_unreviewed_tables.side_effect = [[{"id": "t1"}], []]
    mock_fetch_ctx.return_value = {
        "reviewed": False,
        "columns": [{"name": "amount"}],
        "fks": [],
    }

    count = compile_semantic_layer("dbx", resume=False)

    assert count == 1
    mock_dal.clear_reviewed_flags.assert_called_once()
    mock_select_seed.assert_called_once()
    mock_visit.assert_called_once()
