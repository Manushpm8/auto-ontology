# SPDX-FileCopyrightText: Copyright (c) 2024-2026, NVIDIA CORPORATION & AFFILIATES.
# All rights reserved.
# SPDX-License-Identifier: Apache-2.0

"""Mint a Databricks access token from NVIDIA SSA service-account credentials.

An alternative to storing a long-lived personal access token on the connection: the
connection instead carries SSA credentials, and a short-lived Databricks token is minted
on demand. Two legs, per the Kratos CI/CD guide:

1. **SSA → OIDC JWT.** ``POST <ssa_token_url>`` with HTTP Basic auth (SSA client id and
   secret) and ``grant_type=client_credentials``. Returns the service account's JWT.
2. **JWT → Databricks token.** ``POST https://<host>/oidc/v1/token`` as an RFC 8693 token
   exchange: ``grant_type=urn:ietf:params:oauth:grant-type:token-exchange`` with the SSA
   JWT as the ``subject_token`` and the workspace service principal's application id as
   ``client_id``. Returns the Databricks access token the SQL connector uses.

The second leg is what the Databricks SDK performs internally for
``DATABRICKS_AUTH_TYPE=env-oidc``. Databricks matches the JWT against a *federation
policy* on that service principal (issuer, subject and audience) rather than
authenticating the client directly — it rejects ``private_key_jwt`` client assertions
outright. Replicating the exchange here keeps GSF on the plain SQL connector rather than
pulling in the SDK's auth stack.

Tokens are short-lived, so they are minted per use and cached until shortly before
expiry, keyed by the credentials that produced them.
"""

from __future__ import annotations

import base64
import hashlib
import logging
import threading
import time
from dataclasses import dataclass
from typing import Any, Mapping

import httpx

logger = logging.getLogger(__name__)

# NVIDIA's SSA token endpoint and the scope the Kratos guide registers service accounts
# under. Both are overridable per connection: they are stable, not immutable.
DEFAULT_SSA_TOKEN_URL = (
    "https://w6rojyggn16dpp37xuunjjnvczxdhjrkobq393rkkae.ssa.nvidia.com/token"
)
DEFAULT_SSA_SCOPE = "pipelines-write"

# Grant identifiers: leg 1 is a plain client-credentials grant, leg 2 an RFC 8693 token
# exchange (Databricks workload identity federation).
_CLIENT_CREDENTIALS = "client_credentials"
_TOKEN_EXCHANGE = "urn:ietf:params:oauth:grant-type:token-exchange"
_JWT_TOKEN_TYPE = "urn:ietf:params:oauth:token-type:jwt"
_DATABRICKS_SCOPE = "all-apis"

_EXPIRY_MARGIN_S = 60.0
_REQUEST_TIMEOUT_S = 30.0

# Connection fields that configure this flow. ``ssa_client_secret`` is a credential and
# is redacted/stored like any other secret.
SSA_FIELDS = (
    "ssa_client_id",
    "ssa_client_secret",
    "databricks_client_id",
    "ssa_token_url",
    "ssa_scope",
    "ssa_audience",
)


class DatabricksSSAError(RuntimeError):
    """Raised when SSA credentials cannot be turned into a Databricks token."""


@dataclass(frozen=True)
class _CachedToken:
    access_token: str
    expires_at: float


_cache: dict[str, _CachedToken] = {}
_cache_lock = threading.Lock()


def uses_ssa(connection: Mapping[str, Any]) -> bool:
    """Whether *connection* mints its Databricks token from SSA credentials.

    Driven by the form's auth-mode selector, but inferred from the presence of an SSA
    client id too, so a hand-written connection behaves without the extra field.
    """
    mode = str(connection.get("auth_mode") or "").strip().lower()
    if mode == "ssa":
        return True
    if mode in ("token", "pat"):
        return False
    return bool(str(connection.get("ssa_client_id") or "").strip())


def clear_cache() -> None:
    """Drop all cached SSA-minted tokens (tests, and on connection change)."""
    with _cache_lock:
        _cache.clear()


def _purge_expired(now: float) -> None:
    """Drop lapsed entries. Caller must hold ``_cache_lock``.

    Entries are keyed by the credentials that produced them, so rotating an SSA secret
    strands the old key. Nothing else removes entries, so prune on write.
    """
    expired = [key for key, token in _cache.items() if token.expires_at <= now]
    for key in expired:
        del _cache[key]


def _cache_key(
    host: str, client_id: str, secret: str, databricks_client_id: str
) -> str:
    """Identify a credential set without holding the secret in a dict key."""
    material = "\0".join((host, client_id, secret, databricks_client_id))
    return hashlib.sha256(material.encode("utf-8")).hexdigest()


def _normalize_host(host: str) -> str:
    host = str(host or "").strip().rstrip("/")
    if host.startswith(("https://", "http://")):
        host = host.split("://", 1)[1]
    return host


def _post(url: str, *, headers: dict[str, str], data: dict[str, str], leg: str) -> dict:
    """POST a form-encoded token request and return its JSON payload.

    Error bodies can echo the credential back, so only the status and the provider's
    short ``error`` code are surfaced — never the body.
    """
    try:
        response = httpx.post(
            url, headers=headers, data=data, timeout=_REQUEST_TIMEOUT_S
        )
    except httpx.HTTPError as exc:
        raise DatabricksSSAError(f"{leg} request to {url} failed: {exc}") from exc

    if response.status_code != 200:
        detail = ""
        try:
            detail = str(response.json().get("error", ""))
        except Exception:  # noqa: BLE001 — error body may not be JSON
            pass
        raise DatabricksSSAError(
            f"{leg} failed with HTTP {response.status_code}"
            + (f" ({detail})" if detail else "")
        )

    try:
        return dict(response.json())
    except Exception as exc:  # noqa: BLE001 — malformed success body
        raise DatabricksSSAError(f"{leg} returned a malformed response") from exc


def fetch_ssa_jwt(
    *,
    client_id: str,
    client_secret: str,
    token_url: str = DEFAULT_SSA_TOKEN_URL,
    scope: str = DEFAULT_SSA_SCOPE,
) -> str:
    """Leg 1: the service account's OIDC JWT, via client-credentials Basic auth."""
    if not client_id or not client_secret:
        raise DatabricksSSAError("SSA client id and secret are required")

    basic = base64.b64encode(f"{client_id}:{client_secret}".encode("utf-8")).decode()
    payload = _post(
        token_url or DEFAULT_SSA_TOKEN_URL,
        headers={
            "Authorization": f"Basic {basic}",
            "Content-Type": "application/x-www-form-urlencoded",
            "Cache-Control": "no-cache",
        },
        data={
            "grant_type": _CLIENT_CREDENTIALS,
            "scope": scope or DEFAULT_SSA_SCOPE,
        },
        leg="SSA token request",
    )
    token = str(payload.get("access_token") or "")
    if not token:
        raise DatabricksSSAError("SSA token request returned no access_token")
    return token


def exchange_jwt_for_databricks_token(
    *,
    host: str,
    databricks_client_id: str,
    jwt: str,
    audience: str = "",
) -> tuple[str, float]:
    """Leg 2: the workspace token, exchanging the SSA JWT as the subject token."""
    host = _normalize_host(host)
    if not host:
        raise DatabricksSSAError("Databricks host is required")
    if not databricks_client_id:
        raise DatabricksSSAError(
            "A Databricks client id is required to exchange an SSA token "
            "(the id Kratos issues at registration)"
        )

    data = {
        "grant_type": _TOKEN_EXCHANGE,
        "client_id": databricks_client_id,
        "subject_token_type": _JWT_TOKEN_TYPE,
        "subject_token": jwt,
        "scope": _DATABRICKS_SCOPE,
    }
    if audience:
        data["audience"] = audience

    payload = _post(
        f"https://{host}/oidc/v1/token",
        headers={"Content-Type": "application/x-www-form-urlencoded"},
        data=data,
        leg="Databricks SSA token exchange",
    )
    token = str(payload.get("access_token") or "")
    if not token:
        raise DatabricksSSAError("Databricks token exchange returned no access_token")
    try:
        expires_in = float(payload.get("expires_in", 3600))
    except (TypeError, ValueError):
        expires_in = 3600.0
    return token, expires_in


def get_access_token(
    *,
    host: str,
    client_id: str,
    client_secret: str,
    databricks_client_id: str,
    token_url: str = DEFAULT_SSA_TOKEN_URL,
    scope: str = DEFAULT_SSA_SCOPE,
    audience: str = "",
) -> str:
    """Return a Databricks access token for these SSA credentials.

    Cached until shortly before expiry, so the per-operation connections the Databricks
    connector opens do not each mint a fresh token.

    Raises:
        DatabricksSSAError: either leg failed, or a required field is missing.
    """
    host = _normalize_host(host)
    key = _cache_key(host, client_id, client_secret, databricks_client_id)

    with _cache_lock:
        cached = _cache.get(key)
        if cached is not None and cached.expires_at > time.monotonic():
            return cached.access_token

    jwt = fetch_ssa_jwt(
        client_id=client_id,
        client_secret=client_secret,
        token_url=token_url,
        scope=scope,
    )
    token, expires_in = exchange_jwt_for_databricks_token(
        host=host,
        databricks_client_id=databricks_client_id,
        jwt=jwt,
        audience=audience,
    )

    ttl = max(expires_in - _EXPIRY_MARGIN_S, 0.0)
    stored_at = time.monotonic()
    with _cache_lock:
        _purge_expired(stored_at)
        _cache[key] = _CachedToken(token, stored_at + ttl)

    logger.info(
        "Minted Databricks token from SSA credentials for %s (ttl %.0fs)", host, ttl
    )
    return token
