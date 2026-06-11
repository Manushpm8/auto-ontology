"""Tests for the flat table-by-table compile_semantic_layer loop."""

from __future__ import annotations

from unittest.mock import MagicMock, patch

from gsf.semantic.pipeline import compile_semantic_layer


@patch("gsf.semantic.pipeline.process_table")
@patch("gsf.semantic.pipeline.fetch_table_context")
@patch("gsf.semantic.pipeline.load_domain_summary", return_value=None)
@patch("gsf.semantic.pipeline.neo4j_dal")
def test_compile_processes_all_tables(
    mock_dal: MagicMock,
    _mock_summary: MagicMock,
    mock_fetch_ctx: MagicMock,
    mock_process: MagicMock,
) -> None:
    """Two unreviewed tables → two process_table calls → count == 2."""
    mock_dal.discover_unreviewed_tables.side_effect = [
        [{"id": "t1", "name": "orders", "description": ""}],
        [{"id": "t2", "name": "customers", "description": ""}],
        [],
    ]
    mock_fetch_ctx.side_effect = [
        {"reviewed": False, "columns": [{"name": "amount"}], "fks": []},
        {"reviewed": False, "columns": [{"name": "name"}], "fks": []},
    ]

    count = compile_semantic_layer("dbx", resume=True)

    assert count == 2
    assert mock_process.call_count == 2
    assert mock_dal.mark_table_reviewed.call_count == 2


@patch("gsf.semantic.pipeline.process_table")
@patch("gsf.semantic.pipeline.fetch_table_context")
@patch("gsf.semantic.pipeline.load_domain_summary", return_value=None)
@patch("gsf.semantic.pipeline.neo4j_dal")
def test_compile_skips_columnless_table(
    mock_dal: MagicMock,
    _mock_summary: MagicMock,
    mock_fetch_ctx: MagicMock,
    mock_process: MagicMock,
) -> None:
    """Table with no columns is marked reviewed without calling process_table."""
    mock_dal.discover_unreviewed_tables.side_effect = [
        [{"id": "t1", "name": "empty_table", "description": ""}],
        [],
    ]
    mock_fetch_ctx.return_value = {"reviewed": False, "columns": [], "fks": []}

    count = compile_semantic_layer("dbx", resume=True)

    assert count == 0
    mock_process.assert_not_called()
    mock_dal.mark_table_reviewed.assert_called_once_with("t1")


@patch("gsf.semantic.pipeline.process_table")
@patch("gsf.semantic.pipeline.fetch_table_context")
@patch("gsf.semantic.pipeline.load_domain_summary", return_value=None)
@patch("gsf.semantic.pipeline.neo4j_dal")
def test_compile_clears_flags_when_not_resuming(
    mock_dal: MagicMock,
    _mock_summary: MagicMock,
    _mock_fetch_ctx: MagicMock,
    _mock_process: MagicMock,
) -> None:
    """resume=False should call clear_reviewed_flags before processing."""
    mock_dal.discover_unreviewed_tables.return_value = []

    compile_semantic_layer("dbx", resume=False)

    mock_dal.clear_reviewed_flags.assert_called_once()


@patch("gsf.semantic.pipeline.process_table", side_effect=RuntimeError("boom"))
@patch("gsf.semantic.pipeline.fetch_table_context")
@patch("gsf.semantic.pipeline.load_domain_summary", return_value=None)
@patch("gsf.semantic.pipeline.neo4j_dal")
def test_compile_continues_after_error(
    mock_dal: MagicMock,
    _mock_summary: MagicMock,
    mock_fetch_ctx: MagicMock,
    _mock_process: MagicMock,
) -> None:
    """process_table raising an exception should not abort the loop."""
    mock_dal.discover_unreviewed_tables.side_effect = [
        [{"id": "t1", "name": "orders", "description": ""}],
        [],
    ]
    mock_fetch_ctx.return_value = {
        "reviewed": False,
        "columns": [{"name": "x"}],
        "fks": [],
    }

    count = compile_semantic_layer("dbx", resume=True)

    # Table is still marked reviewed even after error, but not counted as processed.
    mock_dal.mark_table_reviewed.assert_called_once_with("t1")
    assert count == 1
