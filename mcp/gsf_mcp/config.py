# SPDX-FileCopyrightText: Copyright (c) 2026, NVIDIA CORPORATION & AFFILIATES.
# All rights reserved.
# SPDX-License-Identifier: Apache-2.0

"""Settings for the GSF MCP server, read from the environment.

The server talks to the **public** GSF API, not to the Python services behind
it: those are ClusterIP-only and reachable solely through the Next.js proxy,
which is where authentication and permission checks live. So all it needs is a
base URL and a credential — the same one a script or notebook would use,
carrying exactly the permissions of the user who minted it.

Where that credential comes from depends on who the server serves:

* **stdio** — one process per user, started by their own client, so
  ``GSF_API_TOKEN`` *is* that user's identity and is required.
* **http** — potentially many users on one process, so each request carries its
  own credential and the server holds none. ``GSF_API_TOKEN`` is not required,
  and supplying one anyway needs ``GSF_MCP_ALLOW_SHARED_TOKEN`` because it makes
  every caller act as that token's owner.

That last case is the reason the opt-in exists rather than a warning: a shared
token silently collapses a team into one identity, with one set of permissions
and one conversation history, and nothing in the protocol would reveal it.

Under ``http`` the caller still has to *have* a credential before it can call,
which means every user mints a GSF API token by hand first. Setting the
``GSF_MCP_OIDC_*`` variables removes that step: the server advertises an
authorization server, the client opens a browser, the user signs in through the
same SSO provider the GSF deployment trusts, and the id token that comes back is
forwarded upstream unchanged. GSF verifies it in ``frontend/auth/bearer.ts``
against that provider's JWKS, so nothing new is needed on the GSF side.

Pointing at a self-hosted GSF or a hosted one still differs only in
``GSF_API_URL``.
"""

from __future__ import annotations

import os
from dataclasses import dataclass
from pathlib import Path

# The description of the public API that tools are generated from, shipped
# inside the package so the server runs from an ordinary install rather than
# only from a source checkout. Kept identical to docs/openapi/gsf-api.json by
# `pnpm openapi`, and enforced by CI — a stale copy would silently yield a
# stale tool surface.
DEFAULT_SPEC_PATH = Path(__file__).resolve().parent / "gsf-api.json"

DEFAULT_API_URL = "http://localhost:3000"

# 3000 frontend, 3001 backend, 3002 ingestion — this is the next free one.
DEFAULT_PORT = 3003
DEFAULT_HOST = "0.0.0.0"

# Catalog and glossary reads are ordinary API calls.
DEFAULT_TIMEOUT_S = 30.0

# One chat turn is many sequential LLM calls. The backend caps individual SQL
# statements at 30s but puts no ceiling on a whole run, so this is a client-side
# guard against waiting forever rather than a mirror of a server-side limit.
DEFAULT_CHAT_TIMEOUT_S = 900.0

TRANSPORTS = ("stdio", "http")


class ConfigError(RuntimeError):
    """The environment does not describe a usable server."""


@dataclass(frozen=True)
class OidcSettings:
    """An OIDC provider the server delegates its own sign-in to.

    ``public_url`` is where callers reach *this* server, not where GSF lives; the
    redirect URI is built from it, so it has to match what the provider has
    registered for this client.

    ``client_secret`` is required rather than optional. Beyond authenticating at
    the token endpoint, FastMCP derives the key it signs its own tokens with from
    it, which is what lets sessions outlive a restart and lets replicas accept
    each other's tokens. A public client would have to supply that key by another
    route, which nothing here needs yet.
    """

    config_url: str
    client_id: str
    client_secret: str
    public_url: str
    scopes: tuple[str, ...]
    redirect_path: str


@dataclass(frozen=True)
class Settings:
    """Everything the server needs to start."""

    api_url: str
    # Empty under `http` unless a shared identity was opted into: there, the
    # credential arrives per request instead. Always set under `stdio`.
    api_token: str
    spec_path: Path
    transport: str
    host: str
    port: int
    timeout_s: float
    chat_timeout_s: float
    # Where callers reach this server. Only sign-in needs it — it is what a
    # client is told to come back to — so it is derived from host and port
    # unless GSF_MCP_PUBLIC_URL says otherwise.
    public_url: str = ""
    allow_shared_token: bool = False
    # Set only when the GSF_MCP_OIDC_* group is configured, which is `http`-only.
    oidc: OidcSettings | None = None
    # GSF itself is the authorization server. `http`-only, and mutually
    # exclusive with both of the above. See gsf_mcp.gsf_auth.
    sign_in_with_gsf: bool = False


def _positive_float(name: str, default: float) -> float:
    raw = os.environ.get(name)
    if raw is None or not raw.strip():
        return default
    try:
        value = float(raw)
    except ValueError as exc:
        raise ConfigError(f"{name} must be a number, got {raw!r}") from exc
    if value <= 0:
        raise ConfigError(f"{name} must be greater than zero, got {value}")
    return value


def _flag(name: str) -> bool:
    """Read a boolean opt-in. Anything unset or unrecognised reads as false."""
    return (os.environ.get(name) or "").strip().lower() in {"1", "true", "yes", "on"}


def _port(name: str, default: int) -> int:
    raw = os.environ.get(name)
    if raw is None or not raw.strip():
        return default
    try:
        value = int(raw)
    except ValueError as exc:
        raise ConfigError(f"{name} must be an integer, got {raw!r}") from exc
    if not 1 <= value <= 65535:
        raise ConfigError(f"{name} must be a valid port, got {value}")
    return value


# The provider plus our client identity — all of it or none of it.
# `GSF_MCP_PUBLIC_URL` is required alongside these but is deliberately not one
# of them: it describes this server, and the other sign-in mode needs it too, so
# setting it alone must not read as a half-configured OIDC group.
_OIDC_VARS = (
    "GSF_MCP_OIDC_CONFIG_URL",
    "GSF_MCP_OIDC_CLIENT_ID",
    "GSF_MCP_OIDC_CLIENT_SECRET",
)

# Enough to identify the user to GSF, which resolves an account by email. The
# provider still has to grant them; asking for more than the client is
# registered for is how a sign-in fails at the provider rather than here.
DEFAULT_OIDC_SCOPES = ("openid", "email", "profile")

# Without this scope the provider returns no id token, and an id token is the
# only thing GSF can verify. See gsf_mcp.oidc.
_REQUIRED_SCOPE = "openid"

# FastMCP's own default. Overridable because provider registrations are often
# managed centrally: aligning with a path that is already approved is easier
# than getting a new one added.
DEFAULT_OIDC_REDIRECT_PATH = "/auth/callback"


def _oidc(transport: str, token: str) -> OidcSettings | None:
    """Read the OIDC group, or ``None`` if it was left alone.

    Partial configuration raises rather than falling back to hand-minted
    tokens. A server that was meant to offer sign-in and quietly does not looks
    exactly like one that works, right up until a caller arrives without a
    credential of its own.
    """

    values = {name: (os.environ.get(name) or "").strip() for name in _OIDC_VARS}
    if not any(values.values()):
        return None

    if transport != "http":
        raise ConfigError(
            "The GSF_MCP_OIDC_* variables need GSF_MCP_TRANSPORT=http. Signing "
            "in ends in a browser redirect back to this server, and stdio has "
            "no address to redirect to; there, GSF_API_TOKEN is the identity."
        )

    missing = [name for name in _OIDC_VARS if not values[name]]
    if missing:
        raise ConfigError(
            f"Incomplete OIDC configuration: {', '.join(missing)} "
            f"{'is' if len(missing) == 1 else 'are'} unset. Set the whole "
            "group or none of it."
        )

    if token:
        # Reachable only with GSF_MCP_ALLOW_SHARED_TOKEN, since the check above
        # rejects a bare token under http.
        raise ConfigError(
            "GSF_API_TOKEN is set alongside the GSF_MCP_OIDC_* variables. Every "
            "caller would act as that token's owner, so the sign-in they were "
            "sent through would decide nothing. Unset one of the two."
        )

    public_url = (os.environ.get("GSF_MCP_PUBLIC_URL") or "").strip()
    if not public_url:
        # Not derived from host and port here, unlike the GSF sign-in mode: the
        # redirect URI is built from this and has to match what the provider has
        # registered, so a guess would fail at the provider with a message that
        # points nowhere near this setting.
        raise ConfigError(
            "GSF_MCP_PUBLIC_URL is required with the GSF_MCP_OIDC_* variables. "
            "The redirect URI is built from it, so it must match the one "
            "registered for this client at the provider."
        )

    return OidcSettings(
        config_url=values["GSF_MCP_OIDC_CONFIG_URL"],
        client_id=values["GSF_MCP_OIDC_CLIENT_ID"],
        client_secret=values["GSF_MCP_OIDC_CLIENT_SECRET"],
        public_url=public_url.rstrip("/"),
        scopes=_oidc_scopes(),
        redirect_path=_oidc_redirect_path(),
    )


def _public_url(host: str, port: int) -> str:
    """Where callers reach this server, for sign-in redirects and metadata."""
    raw = (os.environ.get("GSF_MCP_PUBLIC_URL") or "").strip()
    if raw:
        return raw.rstrip("/")

    # 0.0.0.0 means "every interface", which is a fine thing to bind to and a
    # useless thing to send a browser to.
    hostname = "localhost" if host in {"", "0.0.0.0", "::"} else host
    return f"http://{hostname}:{port}"


def _sign_in_with_gsf(transport: str, token: str, oidc: OidcSettings | None) -> bool:
    """Read ``GSF_MCP_SIGN_IN``, the one variable this mode needs."""
    raw = (os.environ.get("GSF_MCP_SIGN_IN") or "").strip().lower()
    if not raw or raw == "off":
        return False

    if raw != "gsf":
        raise ConfigError(
            f"GSF_MCP_SIGN_IN must be 'gsf' or 'off', got {raw!r}. 'gsf' has "
            "callers sign in against the GSF deployment itself; to delegate to "
            "an identity provider directly, set the GSF_MCP_OIDC_* group "
            "instead."
        )

    if transport != "http":
        raise ConfigError(
            "GSF_MCP_SIGN_IN=gsf needs GSF_MCP_TRANSPORT=http. Signing in ends "
            "in a browser redirect back to this server, and stdio has no "
            "address to redirect to; there, GSF_API_TOKEN is the identity."
        )

    if token:
        # Reachable only with GSF_MCP_ALLOW_SHARED_TOKEN, since a bare token is
        # already rejected under http.
        raise ConfigError(
            "GSF_API_TOKEN is set alongside GSF_MCP_SIGN_IN=gsf. Every caller "
            "would act as that token's owner, so the sign-in they were sent "
            "through would decide nothing. Unset one of the two."
        )

    if oidc is not None:
        raise ConfigError(
            "GSF_MCP_SIGN_IN=gsf conflicts with the GSF_MCP_OIDC_* variables: "
            "both make this server hand out sign-ins, and a caller can only be "
            "sent to one authorization server. Signing in against GSF needs no "
            "client id or secret, so unset the OIDC group unless you "
            "specifically need to bypass GSF."
        )

    return True


def _oidc_redirect_path() -> str:
    """The path the provider redirects back to, relative to ``public_url``."""
    raw = (os.environ.get("GSF_MCP_OIDC_REDIRECT_PATH") or "").strip()
    if not raw:
        return DEFAULT_OIDC_REDIRECT_PATH
    if not raw.startswith("/"):
        raise ConfigError(
            f"GSF_MCP_OIDC_REDIRECT_PATH must start with '/', got {raw!r}. It is "
            "a path under GSF_MCP_PUBLIC_URL, not a full URL."
        )
    return raw.rstrip("/") or DEFAULT_OIDC_REDIRECT_PATH


def _oidc_scopes() -> tuple[str, ...]:
    """The scopes to request at sign-in, defaulting to a sensible set."""
    raw = (os.environ.get("GSF_MCP_OIDC_SCOPES") or "").strip()
    if not raw:
        return DEFAULT_OIDC_SCOPES

    # Accept either separator: providers document scopes space-separated, but a
    # comma is the reflex in an env var.
    scopes = tuple(dict.fromkeys(raw.replace(",", " ").split()))
    if _REQUIRED_SCOPE not in scopes:
        raise ConfigError(
            f"GSF_MCP_OIDC_SCOPES must include {_REQUIRED_SCOPE!r}. Without it "
            "the provider returns no id token, and an id token is the only "
            "credential GSF can verify, so every call would fail after an "
            "apparently successful sign-in."
        )
    return scopes


def load_settings() -> Settings:
    """Build :class:`Settings` from the environment.

    Raises :class:`ConfigError` with an actionable message rather than failing
    later on the first request — a missing token otherwise surfaces as a 401
    from every tool call, which is a confusing way to learn the server was
    never configured.
    """

    transport = (os.environ.get("GSF_MCP_TRANSPORT") or "stdio").strip().lower()
    if transport not in TRANSPORTS:
        raise ConfigError(
            f"GSF_MCP_TRANSPORT must be one of {', '.join(TRANSPORTS)}, "
            f"got {transport!r}"
        )

    token = (os.environ.get("GSF_API_TOKEN") or "").strip()
    allow_shared_token = _flag("GSF_MCP_ALLOW_SHARED_TOKEN")

    if transport == "stdio" and not token:
        raise ConfigError(
            "GSF_API_TOKEN is required. Mint one in the GSF UI under "
            "user menu → API Tokens, then export it."
        )

    if transport == "http" and token and not allow_shared_token:
        # Refusing here is the point. Serving many users from one token is a
        # legitimate choice for a single-user deployment or an automation
        # account, but it is never one to make by accident, and a running
        # server gives no sign that it happened.
        raise ConfigError(
            "GSF_API_TOKEN is set with GSF_MCP_TRANSPORT=http, which would "
            "authenticate every caller as that token's owner — their "
            "permissions and their conversation history.\n"
            "Unset GSF_API_TOKEN to have each caller send its own credential "
            "(x-api-key, or Authorization: Bearer), which is what a "
            "multi-user deployment wants. If a single shared identity is "
            "intended, set GSF_MCP_ALLOW_SHARED_TOKEN=1 to confirm it."
        )

    oidc = _oidc(transport, token)

    spec_path = Path(
        (os.environ.get("GSF_OPENAPI_SPEC") or "").strip() or DEFAULT_SPEC_PATH
    )
    if not spec_path.is_file():
        # The packaged copy is always present in a sound install, so this is
        # either a bad GSF_OPENAPI_SPEC override or a broken package.
        raise ConfigError(
            f"OpenAPI spec not found at {spec_path}. Unset GSF_OPENAPI_SPEC to "
            "use the copy shipped with gsf-mcp, or reinstall the package."
        )

    api_url = (os.environ.get("GSF_API_URL") or DEFAULT_API_URL).strip()
    host = (os.environ.get("GSF_MCP_HOST") or DEFAULT_HOST).strip()
    port = _port("GSF_MCP_PORT", DEFAULT_PORT)

    return Settings(
        # Trailing slashes make httpx base_url joins produce doubled separators.
        api_url=api_url.rstrip("/"),
        api_token=token,
        spec_path=spec_path,
        transport=transport,
        host=host,
        port=port,
        timeout_s=_positive_float("GSF_MCP_TIMEOUT_S", DEFAULT_TIMEOUT_S),
        chat_timeout_s=_positive_float(
            "GSF_MCP_CHAT_TIMEOUT_S", DEFAULT_CHAT_TIMEOUT_S
        ),
        public_url=_public_url(host, port),
        allow_shared_token=allow_shared_token,
        oidc=oidc,
        sign_in_with_gsf=_sign_in_with_gsf(transport, token, oidc),
    )


__all__ = [
    "ConfigError",
    "DEFAULT_OIDC_REDIRECT_PATH",
    "DEFAULT_OIDC_SCOPES",
    "DEFAULT_SPEC_PATH",
    "OidcSettings",
    "Settings",
    "TRANSPORTS",
    "load_settings",
]
