# SPDX-FileCopyrightText: Copyright (c) 2024-2026, NVIDIA CORPORATION & AFFILIATES.
# All rights reserved.
# SPDX-License-Identifier: Apache-2.0

"""Tests for minting Databricks tokens from NVIDIA SSA service-account credentials."""

import base64
from typing import Any

import httpx
import pytest
from pytest import MonkeyPatch

from gsf.connectors import databricks_ssa
from gsf.connectors.connection_string_factory import build_connection_string
from gsf.connectors.databricks import AUTH_SSA_SERVICE_ACCOUNT, DatabricksDatabase
from gsf.connectors.databricks_ssa import DatabricksSSAError, uses_ssa


@pytest.fixture(autouse=True)
def _clear_cache() -> None:
    databricks_ssa.clear_cache()


def _response(status: int, payload: dict[str, Any], url: str) -> httpx.Response:
    return httpx.Response(
        status_code=status, json=payload, request=httpx.Request("POST", url)
    )


def _ssa_connection(**extra: Any) -> dict[str, Any]:
    return {
        "type": "databricks",
        "host": "nvidia-kratos-ca1.cloud.databricks.com",
        "http_path": "/sql/1.0/warehouses/w",
        "database": "kdc_ca1",
        "auth_mode": "ssa",
        "ssa_client_id": "nvssa-prd-abc",
        "ssa_client_secret": "ssap-secret",
        "databricks_client_id": "db-client-id",
        **extra,
    }


def _two_leg_transport(monkeypatch: MonkeyPatch) -> list[dict[str, Any]]:
    """Stub both token endpoints, recording each request."""
    calls: list[dict[str, Any]] = []

    def fake_post(url: str, **kwargs: Any) -> httpx.Response:
        calls.append({"url": url, **kwargs})
        if "ssa.nvidia.com" in url or "custom-ssa" in url:
            return _response(200, {"access_token": "ssa-jwt"}, url)
        return _response(200, {"access_token": "db-token", "expires_in": 3600}, url)

    monkeypatch.setattr(httpx, "post", fake_post)
    return calls


def test_uses_ssa_follows_the_auth_mode_then_falls_back_to_the_fields() -> None:
    assert uses_ssa({"auth_mode": "ssa"})
    assert not uses_ssa({"auth_mode": "token", "ssa_client_id": "nvssa-prd-abc"})
    # A hand-written connection without the selector still behaves.
    assert uses_ssa({"ssa_client_id": "nvssa-prd-abc"})
    assert not uses_ssa({"password": "dapi-pat"})


def test_get_access_token_performs_both_legs(monkeypatch: MonkeyPatch) -> None:
    """SSA credentials -> OIDC JWT -> Databricks token."""
    calls = _two_leg_transport(monkeypatch)

    token = databricks_ssa.get_access_token(
        host="nvidia-kratos-ca1.cloud.databricks.com",
        client_id="nvssa-prd-abc",
        client_secret="ssap-secret",
        databricks_client_id="db-client-id",
    )

    assert token == "db-token"
    assert len(calls) == 2

    # Leg 1: Basic auth over the SSA client id/secret, client_credentials.
    leg1 = calls[0]
    assert leg1["url"] == databricks_ssa.DEFAULT_SSA_TOKEN_URL
    expected_basic = base64.b64encode(b"nvssa-prd-abc:ssap-secret").decode()
    assert leg1["headers"]["Authorization"] == f"Basic {expected_basic}"
    assert leg1["data"]["grant_type"] == "client_credentials"
    assert leg1["data"]["scope"] == databricks_ssa.DEFAULT_SSA_SCOPE

    # Leg 2: the JWT exchanged as the subject token. Databricks rejects
    # private_key_jwt client assertions, so this must be an RFC 8693 exchange.
    leg2 = calls[1]
    assert leg2["url"] == (
        "https://nvidia-kratos-ca1.cloud.databricks.com/oidc/v1/token"
    )
    assert leg2["data"]["grant_type"] == (
        "urn:ietf:params:oauth:grant-type:token-exchange"
    )
    assert leg2["data"]["client_id"] == "db-client-id"
    assert leg2["data"]["subject_token"] == "ssa-jwt"
    assert leg2["data"]["subject_token_type"] == "urn:ietf:params:oauth:token-type:jwt"


def test_token_is_cached_across_calls(monkeypatch: MonkeyPatch) -> None:
    """The connector opens a connection per operation; each must not mint a token."""
    calls = _two_leg_transport(monkeypatch)
    kwargs = dict(
        host="nvidia-kratos-ca1.cloud.databricks.com",
        client_id="nvssa-prd-abc",
        client_secret="ssap-secret",
        databricks_client_id="db-client-id",
    )

    databricks_ssa.get_access_token(**kwargs)
    databricks_ssa.get_access_token(**kwargs)

    assert len(calls) == 2, "the second call should have been served from cache"


def test_custom_token_url_and_scope_are_honoured(monkeypatch: MonkeyPatch) -> None:
    """Every parameter is settable per connection, not baked into the code."""
    calls = _two_leg_transport(monkeypatch)

    databricks_ssa.get_access_token(
        host="host.databricks.com",
        client_id="nvssa-prd-abc",
        client_secret="ssap-secret",
        databricks_client_id="db-client-id",
        token_url="https://custom-ssa.example.com/token",
        scope="custom-scope",
        audience="custom-audience",
    )

    assert calls[0]["url"] == "https://custom-ssa.example.com/token"
    assert calls[0]["data"]["scope"] == "custom-scope"
    assert calls[1]["data"]["audience"] == "custom-audience"


def test_a_rejected_leg_reports_status_without_echoing_the_secret(
    monkeypatch: MonkeyPatch,
) -> None:
    """Error bodies can echo credentials back, so only status and code surface."""

    def fake_post(url: str, **_kwargs: Any) -> httpx.Response:
        return _response(
            401, {"error": "invalid_client", "description": "ssap-secret"}, url
        )

    monkeypatch.setattr(httpx, "post", fake_post)

    with pytest.raises(DatabricksSSAError) as excinfo:
        databricks_ssa.get_access_token(
            host="host.databricks.com",
            client_id="nvssa-prd-abc",
            client_secret="ssap-secret",
            databricks_client_id="db-client-id",
        )

    message = str(excinfo.value)
    assert "401" in message and "invalid_client" in message
    assert "ssap-secret" not in message


def test_missing_databricks_client_id_is_rejected_before_any_request() -> None:
    """Leg 2 cannot work without it, and the message says where it comes from."""
    with pytest.raises(DatabricksSSAError, match="Databricks client id"):
        databricks_ssa.exchange_jwt_for_databricks_token(
            host="host.databricks.com", databricks_client_id="", jwt="jwt"
        )


def test_connection_string_carries_ssa_config_instead_of_a_token() -> None:
    """An SSA connection stores no PAT, so the password slot is a placeholder."""
    url = build_connection_string(_ssa_connection())

    assert "ssa_client_id=nvssa-prd-abc" in url
    assert "databricks_client_id=db-client-id" in url
    # No password is required, and none is invented beyond the placeholder.
    assert url.startswith("databricks://token:ssa@")


def test_connector_reports_the_ssa_auth_mode() -> None:
    connector = DatabricksDatabase(build_connection_string(_ssa_connection()))

    assert connector.auth_mode == AUTH_SSA_SERVICE_ACCOUNT


def test_connector_mints_the_token_at_connect_time(monkeypatch: MonkeyPatch) -> None:
    """Resolving per connect (not at construction) means a long-lived connector
    refreshes the token rather than holding one until it lapses."""
    _two_leg_transport(monkeypatch)
    connector = DatabricksDatabase(build_connection_string(_ssa_connection()))

    seen: list[dict[str, Any]] = []

    class _FakeConnection:
        def close(self) -> None:
            return None

    monkeypatch.setattr(
        "gsf.connectors.databricks.sql.connect",
        lambda **kwargs: (seen.append(kwargs), _FakeConnection())[1],
    )

    with connector._connect():
        pass

    assert seen[0]["access_token"] == "db-token", "the placeholder must be replaced"


def test_a_pat_connection_never_mints_a_token(monkeypatch: MonkeyPatch) -> None:
    """The existing stored-token path must be untouched by this feature."""

    def fail(*_args: Any, **_kwargs: Any) -> httpx.Response:
        raise AssertionError("a PAT connection must not call a token endpoint")

    monkeypatch.setattr(httpx, "post", fail)

    url = build_connection_string(
        {
            "type": "databricks",
            "host": "example.databricks.com",
            "http_path": "/sql/1.0/warehouses/w",
            "password": "dapi-stored-pat",
            "database": "main",
        }
    )
    connector = DatabricksDatabase(url)

    assert connector._resolve_access_token() is None
