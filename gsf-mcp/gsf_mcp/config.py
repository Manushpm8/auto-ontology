# SPDX-FileCopyrightText: Copyright (c) 2026, NVIDIA CORPORATION & AFFILIATES.
# All rights reserved.
# SPDX-License-Identifier: Apache-2.0

"""Settings for the GSF MCP server, read from the environment.

The server talks to the **public** GSF API, not to the Python services behind
it: those are ClusterIP-only and reachable solely through the Next.js proxy,
which is where authentication and permission checks live. So all it needs is a
base URL and an API token —
the same credential a script or notebook would use, carrying exactly the
permissions of the user who minted it.

That also makes the deployed and local cases identical: pointing at a
self-hosted GSF or at a hosted one differs only in ``GSF_API_URL``.
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
    api_token: str
    spec_path: Path
    transport: str
    host: str
    port: int
    timeout_s: float
    chat_timeout_s: float


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

    token = (os.environ.get("GSF_API_TOKEN") or "").strip()
    if not token:
        raise ConfigError(
            "GSF_API_TOKEN is required. Mint one in the GSF UI under "
            "user menu → API Tokens, then export it."
        )

    transport = (os.environ.get("GSF_MCP_TRANSPORT") or "stdio").strip().lower()
    if transport not in TRANSPORTS:
        raise ConfigError(
            f"GSF_MCP_TRANSPORT must be one of {', '.join(TRANSPORTS)}, "
            f"got {transport!r}"
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
    )


__all__ = [
    "ConfigError",
    "DEFAULT_SPEC_PATH",
    "Settings",
    "TRANSPORTS",
    "load_settings",
]
