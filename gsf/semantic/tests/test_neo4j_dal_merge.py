"""Tests for Neo4j DAL merge key behavior (mocked driver)."""

from __future__ import annotations

from unittest.mock import MagicMock, patch

from gsf.dal import terms as neo4j_terms
from gsf.dal import datasources as neo4j_datasources
from gsf.semantic.constants import SEMANTIC_SOURCE
from gsf.utils.sample_values import stringify_sample_values


@patch("gsf.dal.terms.get_neo4j_conn")
def test_merge_term_uses_name_and_source(mock_conn: MagicMock) -> None:
    mock_conn.return_value = MagicMock()
    neo4j_terms.merge_term("Orders", "Order entity", "table-1")
    call = mock_conn.return_value.query_write.call_args
    query, params = call[0][0], call[0][1]
    assert "MERGE (term:Term" in query or "MERGE (term:" in query
    assert params["name"] == "Orders"
    assert params["source"] == SEMANTIC_SOURCE


@patch("gsf.dal.terms.get_neo4j_conn")
def test_fetch_terms_with_sqls_excludes_owned_sql(mock_conn: MagicMock) -> None:
    mock_conn.return_value.query_read.return_value = []

    assert neo4j_terms.fetch_terms_with_sqls() == []

    query, params = mock_conn.return_value.query_read.call_args.args
    assert "WHERE NOT EXISTS { (sql)<-[:HAS_SQL]-() }" in query
    assert params == {"source": SEMANTIC_SOURCE}


@patch("gsf.dal.datasources.graph")
def test_store_column_sample_values_skips_empty(mock_conn: MagicMock) -> None:
    mock_conn.return_value = MagicMock()
    neo4j_datasources.store_column_sample_values("table-1", {})
    mock_conn.return_value.query_write.assert_not_called()


@patch("gsf.dal.datasources.graph")
def test_store_column_sample_values_writes_native_list(mock_conn: MagicMock) -> None:
    mock_conn.return_value = MagicMock()
    neo4j_datasources.store_column_sample_values("table-1", {"amount": [10, 20, 30]})
    call = mock_conn.return_value.query_write.call_args
    params = call[0][1]
    assert params["table_id"] == "table-1"
    entries = params["entries"]
    assert len(entries) == 1
    assert entries[0]["column_name"] == "amount"
    assert entries[0]["sample_values"] == [10, 20, 30]
    assert all(type(v) is int for v in entries[0]["sample_values"])


@patch("gsf.dal.datasources.graph")
def test_store_column_sample_values_keeps_scalar_types(mock_conn: MagicMock) -> None:
    mock_conn.return_value = MagicMock()
    neo4j_datasources.store_column_sample_values(
        "table-1",
        {"active": [True, False], "ratio": [1.5, 2.5], "status": ["open", "closed"]},
    )
    entries = mock_conn.return_value.query_write.call_args[0][1]["entries"]
    stored = {e["column_name"]: e["sample_values"] for e in entries}
    assert stored["active"] == [True, False]
    assert stored["ratio"] == [1.5, 2.5]
    assert stored["status"] == ["open", "closed"]


@patch("gsf.dal.datasources.graph")
def test_store_column_sample_values_widens_mixed_numbers(mock_conn: MagicMock) -> None:
    """A property array must be homogeneous, so ints join floats as floats."""
    mock_conn.return_value = MagicMock()
    neo4j_datasources.store_column_sample_values("table-1", {"amount": [1, 2.5]})
    entries = mock_conn.return_value.query_write.call_args[0][1]["entries"]
    assert entries[0]["sample_values"] == [1.0, 2.5]
    assert all(type(v) is float for v in entries[0]["sample_values"])


@patch("gsf.dal.datasources.graph")
def test_store_column_sample_values_renders_containers_as_json(
    mock_conn: MagicMock,
) -> None:
    """A property array cannot nest, so array/JSON columns persist as text."""
    mock_conn.return_value = MagicMock()
    neo4j_datasources.store_column_sample_values(
        "table-1",
        {"tags": [["a", "b"]], "meta": [{"k": 1}]},
    )
    entries = mock_conn.return_value.query_write.call_args[0][1]["entries"]
    stored = {e["column_name"]: e["sample_values"] for e in entries}
    assert stored["tags"] == ['["a", "b"]']
    assert stored["meta"] == ['{"k": 1}']


@patch("gsf.dal.datasources.graph")
def test_store_column_sample_values_container_matches_rendered_form(
    mock_conn: MagicMock,
) -> None:
    """What is stored must read back as what a prompt would have rendered."""
    mock_conn.return_value = MagicMock()
    samples = [{"k": 1}, ["a", "b"]]
    neo4j_datasources.store_column_sample_values("table-1", {"meta": samples})
    entries = mock_conn.return_value.query_write.call_args[0][1]["entries"]
    stored = entries[0]["sample_values"]
    assert stringify_sample_values(stored) == stringify_sample_values(samples)


@patch("gsf.dal.datasources.graph")
def test_store_column_sample_values_skips_columns_of_mixed_types(
    mock_conn: MagicMock,
) -> None:
    """Samples that cannot name one type are not written; siblings still are.

    A SQLite column declared without an affinity keeps whatever was inserted,
    and a JSONB / VARIANT column may hold any JSON value, so both reach this
    point with disagreeing types.
    """
    mock_conn.return_value = MagicMock()
    neo4j_datasources.store_column_sample_values(
        "table-1",
        {
            "loose": ["open", 1, True],
            "flags": [True, 1],
            "payload": [{"a": 1}, 5],
            "status": ["open", "closed"],
        },
    )
    entries = mock_conn.return_value.query_write.call_args[0][1]["entries"]
    stored = {e["column_name"]: e["sample_values"] for e in entries}
    assert stored == {"status": ["open", "closed"]}


@patch("gsf.dal.datasources.graph")
def test_store_column_sample_values_ignores_none_when_judging_types(
    mock_conn: MagicMock,
) -> None:
    """A null alongside one real type is not a type disagreement."""
    mock_conn.return_value = MagicMock()
    neo4j_datasources.store_column_sample_values("table-1", {"note": ["a", None, "b"]})
    entries = mock_conn.return_value.query_write.call_args[0][1]["entries"]
    assert entries[0]["sample_values"] == ["a", "b"]


@patch("gsf.dal.datasources.graph")
def test_store_column_sample_values_skips_write_when_nothing_storable(
    mock_conn: MagicMock,
) -> None:
    mock_conn.return_value = MagicMock()
    neo4j_datasources.store_column_sample_values("table-1", {"empty": [None, None]})
    mock_conn.return_value.query_write.assert_not_called()


@patch("gsf.dal.datasources.graph")
def test_store_column_uniqueness_skips_empty(mock_conn: MagicMock) -> None:
    mock_conn.return_value = MagicMock()
    neo4j_datasources.store_column_uniqueness("table-1", {})
    mock_conn.return_value.query_write.assert_not_called()


@patch("gsf.dal.datasources.graph")
def test_store_column_uniqueness_writes_flags(mock_conn: MagicMock) -> None:
    mock_conn.return_value = MagicMock()
    neo4j_datasources.store_column_uniqueness("table-1", {"id": True, "status": False})
    call = mock_conn.return_value.query_write.call_args
    query, params = call[0][0], call[0][1]
    assert "SET col.is_unique = iu" in query
    assert params["table_id"] == "table-1"
    entries = {e["column_name"]: e["is_unique"] for e in params["entries"]}
    assert entries == {"id": True, "status": False}


@patch("gsf.dal.datasources.graph")
def test_store_column_date_formats_skips_empty(mock_conn: MagicMock) -> None:
    mock_conn.return_value = MagicMock()
    neo4j_datasources.store_column_date_formats("table-1", {})
    mock_conn.return_value.query_write.assert_not_called()


@patch("gsf.dal.datasources.graph")
def test_store_column_date_formats_writes_notation(mock_conn: MagicMock) -> None:
    mock_conn.return_value = MagicMock()
    neo4j_datasources.store_column_date_formats("table-1", {"Match_Date": "YYMMDD"})
    call = mock_conn.return_value.query_write.call_args
    query, params = call[0][0], call[0][1]
    assert "SET col.format = fmt" in query
    assert params["table_id"] == "table-1"
    entries = {e["column_name"]: e["format"] for e in params["entries"]}
    assert entries == {"Match_Date": "YYMMDD"}
