# SPDX-FileCopyrightText: Copyright (c) 2026, NVIDIA CORPORATION & AFFILIATES.
# All rights reserved.
# SPDX-License-Identifier: Apache-2.0

"""Settings reject an unusable environment at startup, not on first request."""

from __future__ import annotations

import pytest
from pytest import MonkeyPatch

from gsf_mcp.config import (
    DEFAULT_CHAT_TIMEOUT_S,
    DEFAULT_PORT,
    DEFAULT_SPEC_PATH,
    DEFAULT_TIMEOUT_S,
    ConfigError,
    load_settings,
)

_VARS = (
    "GSF_API_URL",
    "GSF_API_TOKEN",
    "GSF_OPENAPI_SPEC",
    "GSF_MCP_TRANSPORT",
    "GSF_MCP_HOST",
    "GSF_MCP_PORT",
    "GSF_MCP_TIMEOUT_S",
    "GSF_MCP_CHAT_TIMEOUT_S",
    "GSF_MCP_ALLOW_SHARED_TOKEN",
)


@pytest.fixture(autouse=True)
def clean_env(monkeypatch: MonkeyPatch) -> None:
    """Ignore whatever the developer happens to have exported."""
    for name in _VARS:
        monkeypatch.delenv(name, raising=False)


def test_requires_an_api_token() -> None:
    with pytest.raises(ConfigError, match="GSF_API_TOKEN is required"):
        load_settings()


def test_defaults_are_usable_with_only_a_token(monkeypatch: MonkeyPatch) -> None:
    monkeypatch.setenv("GSF_API_TOKEN", "gsf_abc")

    settings = load_settings()

    assert settings.api_url == "http://localhost:3000"
    assert settings.transport == "stdio"
    assert settings.port == DEFAULT_PORT
    assert settings.timeout_s == DEFAULT_TIMEOUT_S
    assert settings.chat_timeout_s == DEFAULT_CHAT_TIMEOUT_S
    assert settings.spec_path == DEFAULT_SPEC_PATH


def test_strips_trailing_slash_from_api_url(monkeypatch: MonkeyPatch) -> None:
    # httpx joins base_url with a leading-slash path, so a trailing slash here
    # would produce '//api/...' and 404 against the Next.js router.
    monkeypatch.setenv("GSF_API_TOKEN", "gsf_abc")
    monkeypatch.setenv("GSF_API_URL", "https://gsf.example.com/")

    assert load_settings().api_url == "https://gsf.example.com"


def test_rejects_unknown_transport(monkeypatch: MonkeyPatch) -> None:
    monkeypatch.setenv("GSF_API_TOKEN", "gsf_abc")
    monkeypatch.setenv("GSF_MCP_TRANSPORT", "carrier-pigeon")

    with pytest.raises(ConfigError, match="GSF_MCP_TRANSPORT"):
        load_settings()


def test_accepts_http_transport(monkeypatch: MonkeyPatch) -> None:
    monkeypatch.setenv("GSF_MCP_TRANSPORT", "HTTP")

    settings = load_settings()

    assert settings.transport == "http"
    # No token: callers authenticate per request, which is the whole point of
    # the HTTP transport not requiring one.
    assert settings.api_token == ""


def test_stdio_still_requires_a_token(monkeypatch: MonkeyPatch) -> None:
    monkeypatch.setenv("GSF_MCP_TRANSPORT", "stdio")

    with pytest.raises(ConfigError, match="GSF_API_TOKEN is required"):
        load_settings()


def test_refuses_a_shared_token_on_http_without_opt_in(
    monkeypatch: MonkeyPatch,
) -> None:
    """The dangerous configuration must be chosen, never merely inherited."""
    monkeypatch.setenv("GSF_MCP_TRANSPORT", "http")
    monkeypatch.setenv("GSF_API_TOKEN", "gsf_abc")

    with pytest.raises(ConfigError, match="GSF_MCP_ALLOW_SHARED_TOKEN") as exc:
        load_settings()

    # The message has to say what it would do, not just that it refused.
    assert "every caller" in str(exc.value)


def test_allows_a_shared_token_on_http_when_opted_in(
    monkeypatch: MonkeyPatch,
) -> None:
    monkeypatch.setenv("GSF_MCP_TRANSPORT", "http")
    monkeypatch.setenv("GSF_API_TOKEN", "gsf_abc")
    monkeypatch.setenv("GSF_MCP_ALLOW_SHARED_TOKEN", "1")

    settings = load_settings()

    assert settings.api_token == "gsf_abc"
    assert settings.allow_shared_token is True


@pytest.mark.parametrize("value", ["true", "TRUE", "yes", "on", "1"])
def test_opt_in_accepts_the_usual_spellings(
    monkeypatch: MonkeyPatch, value: str
) -> None:
    monkeypatch.setenv("GSF_MCP_TRANSPORT", "http")
    monkeypatch.setenv("GSF_API_TOKEN", "gsf_abc")
    monkeypatch.setenv("GSF_MCP_ALLOW_SHARED_TOKEN", value)

    assert load_settings().allow_shared_token is True


@pytest.mark.parametrize("value", ["", "0", "false", "no", "maybe"])
def test_anything_else_is_not_an_opt_in(monkeypatch: MonkeyPatch, value: str) -> None:
    monkeypatch.setenv("GSF_MCP_TRANSPORT", "http")
    monkeypatch.setenv("GSF_API_TOKEN", "gsf_abc")
    monkeypatch.setenv("GSF_MCP_ALLOW_SHARED_TOKEN", value)

    with pytest.raises(ConfigError, match="GSF_MCP_ALLOW_SHARED_TOKEN"):
        load_settings()


def test_rejects_missing_spec(monkeypatch: MonkeyPatch, tmp_path) -> None:
    monkeypatch.setenv("GSF_API_TOKEN", "gsf_abc")
    monkeypatch.setenv("GSF_OPENAPI_SPEC", str(tmp_path / "nope.json"))

    with pytest.raises(ConfigError, match="OpenAPI spec not found"):
        load_settings()


@pytest.mark.parametrize("value", ["0", "-5", "not-a-number"])
def test_rejects_nonsense_timeouts(monkeypatch: MonkeyPatch, value: str) -> None:
    monkeypatch.setenv("GSF_API_TOKEN", "gsf_abc")
    monkeypatch.setenv("GSF_MCP_TIMEOUT_S", value)

    with pytest.raises(ConfigError, match="GSF_MCP_TIMEOUT_S"):
        load_settings()


@pytest.mark.parametrize("value", ["0", "70000", "http"])
def test_rejects_invalid_port(monkeypatch: MonkeyPatch, value: str) -> None:
    monkeypatch.setenv("GSF_API_TOKEN", "gsf_abc")
    monkeypatch.setenv("GSF_MCP_PORT", value)

    with pytest.raises(ConfigError, match="GSF_MCP_PORT"):
        load_settings()


def test_blank_values_fall_back_to_defaults(monkeypatch: MonkeyPatch) -> None:
    # Unset and empty-string are the same thing to a shell export, so an empty
    # value must not be read as "0" or as a literal blank URL.
    monkeypatch.setenv("GSF_API_TOKEN", "gsf_abc")
    monkeypatch.setenv("GSF_MCP_PORT", "")
    monkeypatch.setenv("GSF_MCP_TIMEOUT_S", "  ")

    settings = load_settings()

    assert settings.port == DEFAULT_PORT
    assert settings.timeout_s == DEFAULT_TIMEOUT_S
