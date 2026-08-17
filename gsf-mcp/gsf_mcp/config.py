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
    allow_shared_token: bool = False


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

    return Settings(
        # Trailing slashes make httpx base_url joins produce doubled separators.
        api_url=api_url.rstrip("/"),
        api_token=token,
        spec_path=spec_path,
        transport=transport,
        host=(os.environ.get("GSF_MCP_HOST") or DEFAULT_HOST).strip(),
        port=_port("GSF_MCP_PORT", DEFAULT_PORT),
        timeout_s=_positive_float("GSF_MCP_TIMEOUT_S", DEFAULT_TIMEOUT_S),
        chat_timeout_s=_positive_float(
            "GSF_MCP_CHAT_TIMEOUT_S", DEFAULT_CHAT_TIMEOUT_S
        ),
        allow_shared_token=allow_shared_token,
    )


__all__ = [
    "ConfigError",
    "DEFAULT_SPEC_PATH",
    "Settings",
    "TRANSPORTS",
    "load_settings",
]
