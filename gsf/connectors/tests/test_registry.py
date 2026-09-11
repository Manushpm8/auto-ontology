# SPDX-FileCopyrightText: Copyright (c) 2026, NVIDIA CORPORATION & AFFILIATES.
# All rights reserved.
# SPDX-License-Identifier: Apache-2.0

"""Tests for connector discovery in :mod:`gsf.connectors.registry`.

One unreachable connection must not take down every consumer of the
registry, and the log line that reports the skip must never carry the
credentials or query parameters that live in a connection string.
"""

from __future__ import annotations

import logging
from typing import Callable, Iterator

import pytest

from gsf.connectors import registry


class _Connector:
    def __init__(self, database_name: str) -> None:
        self.database_name = database_name

    def close(self) -> None:
        pass


@pytest.fixture
def connections(
    monkeypatch: pytest.MonkeyPatch,
) -> Iterator[Callable[..., None]]:
    """Configure ``get_connectors`` from a plain list of connection strings.

    The catalog is emptied so discovery falls through to ``CONNECTION_STRINGS``,
    and the cache is cleared on both sides so tests neither see nor leak
    connectors.
    """
    import gsf.dal.connections as dal

    monkeypatch.setattr(dal, "list_connections", lambda: [])
    monkeypatch.setattr(registry, "_connectors", None)

    def configure(*connection_strings: str) -> None:
        monkeypatch.setenv("CONNECTION_STRINGS", ",".join(connection_strings))

    yield configure
    registry._connectors = None


def _fail_matching(*needles: str) -> Callable[..., _Connector]:
    """A ``create_connector`` stand-in that fails for strings containing a needle.

    The error carries the raw connection string, as real drivers' errors can,
    so the tests prove the skip log stays clean even then.
    """

    def create(connection_string: str, schemas: list[str] | None = None) -> _Connector:
        for needle in needles:
            if needle in connection_string:
                raise ConnectionError(f"cannot reach {connection_string}")
        return _Connector(connection_string.rsplit("/", 1)[-1])

    return create


def test_a_failed_connector_is_skipped_and_the_rest_survive(
    connections: Callable[..., None], monkeypatch: pytest.MonkeyPatch
) -> None:
    connections("postgres://u:p@dead-host/one", "duckdb:///two", "mysql://u:p@h/three")
    monkeypatch.setattr(registry, "create_connector", _fail_matching("dead-host"))

    assert [c.database_name for c in registry.get_connectors()] == ["two", "three"]


def test_every_connector_failing_yields_an_empty_list(
    connections: Callable[..., None], monkeypatch: pytest.MonkeyPatch
) -> None:
    connections("postgres://u:p@dead-host/one", "mysql://u:p@dead-host/two")
    monkeypatch.setattr(registry, "create_connector", _fail_matching("dead-host"))

    assert registry.get_connectors() == []


def test_the_skip_log_names_the_connection_without_its_secrets(
    connections: Callable[..., None],
    monkeypatch: pytest.MonkeyPatch,
    caplog: pytest.LogCaptureFixture,
) -> None:
    connections(
        "snowflake://alice:s3cr3t@dead-host/analytics"
        "?private_key=PEMBYTES&private_key_passphrase=hunter2"
    )
    monkeypatch.setattr(registry, "create_connector", _fail_matching("dead-host"))

    with caplog.at_level(logging.ERROR, logger=registry.__name__):
        registry.get_connectors()

    skip = [r for r in caplog.records if "Skipping connection" in r.getMessage()]
    assert len(skip) == 1
    assert "snowflake://dead-host/analytics" in skip[0].getMessage()
    assert "ConnectionError" in skip[0].getMessage()
    # Check the fully formatted output, not just the message: a traceback
    # would carry the exception text, and with it the raw connection string.
    for secret in ("alice", "s3cr3t", "private_key", "PEMBYTES", "hunter2", "?"):
        assert secret not in caplog.text


def test_an_unparseable_connection_string_is_skipped_with_a_placeholder(
    connections: Callable[..., None],
    monkeypatch: pytest.MonkeyPatch,
    caplog: pytest.LogCaptureFixture,
) -> None:
    """urlparse raises on an unbalanced IPv6 bracket; the handler must not."""
    connections("postgres://[::1/one", "duckdb:///two")
    monkeypatch.setattr(registry, "create_connector", _fail_matching("[::1"))

    with caplog.at_level(logging.ERROR, logger=registry.__name__):
        loaded = registry.get_connectors()

    assert [c.database_name for c in loaded] == ["two"]
    skip = [r for r in caplog.records if "Skipping connection" in r.getMessage()]
    assert len(skip) == 1
    assert "<unparseable connection string>" in skip[0].getMessage()
    assert "[::1" not in caplog.text
