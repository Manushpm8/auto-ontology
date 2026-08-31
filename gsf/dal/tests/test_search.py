# SPDX-FileCopyrightText: Copyright (c) 2024-2026, NVIDIA CORPORATION & AFFILIATES.
# All rights reserved.
# SPDX-License-Identifier: Apache-2.0

"""Unit tests for global-search index management and read repair."""

from __future__ import annotations

from collections.abc import Iterator
from unittest.mock import MagicMock, patch

import pytest
from neo4j.exceptions import ClientError, Neo4jError

from gsf.dal import search


@pytest.fixture(autouse=True)
def _fresh_index_state() -> Iterator[None]:
    """Each test starts with the process-level index flag cleared."""
    search._indexes_ready = False
    yield
    search._indexes_ready = False


def _server_error(message: str) -> ClientError:
    """Build the driver's own error object; ``code`` is read-only otherwise."""
    error = Neo4jError._hydrate_neo4j(code=search._MISSING_INDEX_CODE, message=message)
    assert isinstance(error, ClientError)
    return error


def _missing_index_error() -> ClientError:
    return _server_error(
        "Failed to invoke procedure `db.index.fulltext.queryNodes`: Caused by: "
        "java.lang.IllegalArgumentException: There is no such fulltext schema "
        f"index: {search.NAME_INDEX}"
    )


def _other_client_error() -> ClientError:
    """Same error code as a dropped index, but a Lucene syntax problem."""
    return _server_error(
        "Failed to invoke procedure `db.index.fulltext.queryNodes`: Caused by: "
        "org.apache.lucene.queryparser.classic.ParseException: Cannot parse '*'"
    )


def test_every_lucene_operator_is_a_separator() -> None:
    """Anything the classic query parser reads as syntax must be stripped.

    ``&&`` and ``||`` are covered by their single characters.
    """
    for char in '+-!(){}[]^"~*?:\\/&|':
        assert char in search._LUCENE_STRIP, char


@pytest.mark.parametrize(
    ("term", "expected"),
    [
        ("date", "*date*"),
        ("created date", "*created* AND *date*"),
        # A hyphen has to split: '*created-at*' matches nothing, because the
        # index analyzer stored 'created' and 'at' as separate tokens.
        ("created-at", "*created* AND *at*"),
        ("2024-01-01", "*2024* AND *01* AND *01*"),
        ("(revenue)", "*revenue*"),
        ("-", ""),
        ("   ", ""),
    ],
)
def test_build_lucene_query_wraps_every_token(term: str, expected: str) -> None:
    assert search.build_lucene_query(term) == expected


def test_ensure_search_indexes_writes_schema_once_per_process() -> None:
    conn = MagicMock()

    with patch.object(search, "get_neo4j_conn", return_value=conn):
        search.ensure_search_indexes()
        search.ensure_search_indexes()
        search.ensure_search_indexes()

    assert conn.query_write.call_count == 2


def test_ensure_search_indexes_reruns_when_forced() -> None:
    conn = MagicMock()

    with patch.object(search, "get_neo4j_conn", return_value=conn):
        search.ensure_search_indexes()
        search.ensure_search_indexes(force=True)

    assert conn.query_write.call_count == 4


def test_ensure_search_indexes_stays_unarmed_when_the_write_fails() -> None:
    conn = MagicMock()
    conn.query_write.side_effect = RuntimeError("neo4j is down")

    with (
        patch.object(search, "get_neo4j_conn", return_value=conn),
        pytest.raises(RuntimeError, match="neo4j is down"),
    ):
        search.ensure_search_indexes()

    assert search._indexes_ready is False


def test_ensure_search_indexes_bypasses_an_open_write_transaction() -> None:
    conn = MagicMock()

    with (
        patch.object(search, "get_neo4j_conn", return_value=conn) as shared,
        patch.object(search, "graph") as scoped,
    ):
        search.ensure_search_indexes()

    shared.assert_called_once()
    scoped.assert_not_called()


def test_read_recreates_the_index_and_retries_once() -> None:
    conn = MagicMock()
    conn.query_read.side_effect = [_missing_index_error(), [{"id": "col-1"}]]

    with (
        patch.object(search, "graph", return_value=conn),
        patch.object(search, "ensure_search_indexes") as ensure,
    ):
        rows = search._read_with_index_repair("MATCH (n) RETURN n", {})

    assert rows == [{"id": "col-1"}]
    assert conn.query_read.call_count == 2
    ensure.assert_called_once_with(force=True)


def test_read_does_not_rebuild_on_unrelated_client_errors() -> None:
    conn = MagicMock()
    conn.query_read.side_effect = _other_client_error()

    with (
        patch.object(search, "graph", return_value=conn),
        patch.object(search, "ensure_search_indexes") as ensure,
        pytest.raises(ClientError, match="ParseException"),
    ):
        search._read_with_index_repair("MATCH (n) RETURN n", {})

    assert conn.query_read.call_count == 1
    ensure.assert_not_called()


def test_read_gives_up_when_the_retry_still_fails() -> None:
    conn = MagicMock()
    conn.query_read.side_effect = [_missing_index_error(), _missing_index_error()]

    with (
        patch.object(search, "graph", return_value=conn),
        patch.object(search, "ensure_search_indexes"),
        pytest.raises(ClientError, match="no such fulltext schema index"),
    ):
        search._read_with_index_repair("MATCH (n) RETURN n", {})

    assert conn.query_read.call_count == 2


def test_fetch_global_search_routes_through_index_repair() -> None:
    with patch.object(search, "_read_with_index_repair", return_value=[]) as repair:
        search.fetch_global_search(
            "*revenue*",
            set(search.SEARCH_OBJECT_TYPES),
            include_description=True,
            synonym_tokens=["revenue"],
        )

    repair.assert_called_once()
    assert repair.call_args.args[1]["lucene"] == "*revenue*"


def test_count_global_search_routes_through_index_repair() -> None:
    with patch.object(search, "_read_with_index_repair", return_value=[]) as repair:
        search.count_global_search(
            "*revenue*",
            set(search.SEARCH_OBJECT_TYPES),
            include_description=False,
        )

    repair.assert_called_once()
