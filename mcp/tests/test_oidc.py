# SPDX-FileCopyrightText: Copyright (c) 2026, NVIDIA CORPORATION & AFFILIATES.
# All rights reserved.
# SPDX-License-Identifier: Apache-2.0

"""Sign-in carries the upstream id token through to GSF, or says why it cannot."""

from __future__ import annotations

import asyncio
import base64
import json
import logging
from functools import partial
from pathlib import Path
from typing import Any

from gsf_mcp.config import OidcSettings, Settings
from gsf_mcp.oidc import (
    ID_TOKEN_CLAIM,
    GsfOidcProxy,
    SigningAlgorithmVerifier,
    build_auth,
    sso_id_token,
)


def _settings(oidc: OidcSettings | None) -> Settings:
    return Settings(
        api_url="http://gsf.test",
        api_token="",
        spec_path=Path("unused.json"),
        transport="http",
        host="127.0.0.1",
        port=3003,
        timeout_s=1.0,
        chat_timeout_s=1.0,
        oidc=oidc,
    )


def _claims(proxy: GsfOidcProxy, idp_tokens: dict[str, Any]) -> dict[str, Any] | None:
    return asyncio.run(proxy._extract_upstream_claims(idp_tokens))


def _bare_proxy() -> GsfOidcProxy:
    """A proxy that skips ``__init__``.

    Constructing one for real fetches the provider's configuration document over
    the network, which a unit test has no business doing; claim extraction reads
    nothing but its arguments and the client id.
    """
    proxy = object.__new__(GsfOidcProxy)
    proxy._upstream_client_id = "gsf-mcp"
    return proxy


def test_no_auth_provider_unless_sign_in_is_configured() -> None:
    assert build_auth(_settings(None)) is None


def test_the_id_token_is_captured_for_forwarding() -> None:
    claims = _claims(
        _bare_proxy(), {"access_token": "opaque-abc", "id_token": "eyJhbGciOi"}
    )

    assert claims is not None
    assert claims[ID_TOKEN_CLAIM] == "eyJhbGciOi"


def test_the_access_token_is_not_mistaken_for_the_id_token() -> None:
    """GSF verifies id tokens; an access token is often not even a JWT."""
    claims = _claims(_bare_proxy(), {"access_token": "opaque-abc"})

    assert claims is None or ID_TOKEN_CLAIM not in claims


def test_a_missing_id_token_is_logged_loudly(caplog) -> None:
    # The sign-in itself succeeds, so nothing else would reveal that every
    # subsequent GSF call is going to fail to authenticate.
    with caplog.at_level(logging.ERROR):
        _claims(_bare_proxy(), {"access_token": "opaque-abc"})

    assert "no id_token" in caplog.text
    assert "gsf-mcp" in caplog.text


def test_no_id_token_outside_a_signed_in_request() -> None:
    """The stdio case, and any unauthenticated call: absence, not an error."""
    assert sso_id_token() is None


def _jwt(alg: str) -> str:
    header = base64.urlsafe_b64encode(json.dumps({"alg": alg}).encode()).decode()
    return f"{header.rstrip('=')}.body.signature"


def _verifier(*algorithms: str) -> SigningAlgorithmVerifier:
    return SigningAlgorithmVerifier(
        jwks_uri="https://idp.example/jwks",
        issuer="https://idp.example",
        audience="gsf-mcp",
        algorithms=algorithms,
    )


def test_verifies_with_the_algorithm_the_token_names() -> None:
    # RS256 is JWTVerifier's fallback, so a provider signing ES256 is exactly
    # the case that silently rejected everything before.
    verifier = _verifier("ES256", "RS256")
    seen: list[str] = []

    async def record(algorithm: str, token: str) -> str:
        seen.append(algorithm)
        return "verified"

    for algorithm, delegate in verifier._verifiers.items():
        delegate.verify_token = partial(record, algorithm)  # type: ignore[method-assign]

    assert asyncio.run(verifier.verify_token(_jwt("ES256"))) == "verified"
    assert seen == ["ES256"]


def test_refuses_an_algorithm_the_issuer_does_not_advertise() -> None:
    """Otherwise a token could name whatever algorithm suited it."""
    assert asyncio.run(_verifier("ES256").verify_token(_jwt("RS256"))) is None


def test_refuses_a_token_whose_header_is_unreadable() -> None:
    assert asyncio.run(_verifier("ES256").verify_token("not-a-jwt")) is None
    assert asyncio.run(_verifier("ES256").verify_token("")) is None
