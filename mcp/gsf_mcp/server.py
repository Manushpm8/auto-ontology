# SPDX-FileCopyrightText: Copyright (c) 2026, NVIDIA CORPORATION & AFFILIATES.
# All rights reserved.
# SPDX-License-Identifier: Apache-2.0

"""Assemble the GSF MCP server from the published OpenAPI spec."""

from __future__ import annotations

import base64
import json
import logging
from pathlib import Path
from typing import Any

import httpx
from fastmcp import FastMCP
from fastmcp.exceptions import ToolError
from fastmcp.server.dependencies import get_http_headers
from mcp.types import Icon

from gsf_mcp import chat
from gsf_mcp.config import ConfigError, Settings
from gsf_mcp.tools import (
    apply_description,
    missing_from_spec,
    names_by_operation_id,
    route_maps,
)
from gsf_mcp import get_version

logger = logging.getLogger(__name__)

SERVER_NAME = "gsf"

# Shipped in the package and inlined as a data URI rather than linked, so it
# needs no network and no reachable GSF to display — a client may well draw its
# server list before anything is connected.
ICON_PATH = Path(__file__).resolve().parent / "nvidia-mark.svg"
ICON_MIME_TYPE = "image/svg+xml"

# Advertised to the host at initialize. Tool descriptions say what each tool
# does; this says how they fit together, which is the part a model otherwise
# has to infer from names — and infers badly, usually by reaching straight for
# the expensive one.
INSTRUCTIONS = """\
GSF (Generative Semantic Fabric) answers questions about an organisation's
structured data. It holds a compiled semantic layer — a glossary of business
terms mapped onto real database columns and reviewed SQL expressions — over the
databases this deployment is connected to.

Use `ask_data` for anything that needs an actual answer from the data. It runs
a full text-to-SQL agent and returns the answer, the SQL it ran, and the rows.

The other tools exist so you can understand the vocabulary before you ask, and
check your assumptions after. A productive sequence is usually:

1. `search_terms` to find out what a business word means here. Deployments
   differ: "active customer" is a defined term with specific SQL behind it,
   not something to guess at.
2. `check_answerable` if you are unsure the question is in scope. It is far
   cheaper than `ask_data` and tells you whether the semantic layer covers the
   entities involved.
3. `ask_data` to get the answer.

`list_databases`, `list_schemas`, `list_tables`, `list_columns`, and
`describe_table` walk the physical catalog when you need the shape of the data
rather than its meaning. `list_example_queries` shows how this data is
conventionally queried, which is often the fastest way to understand its join
paths.

Every tool reads. Nothing here modifies the catalog, the glossary, or the
underlying databases.
"""


def load_spec(settings: Settings) -> dict[str, Any]:
    """Read and validate the OpenAPI document the tools are generated from."""
    try:
        spec = json.loads(settings.spec_path.read_text(encoding="utf-8"))
    except json.JSONDecodeError as exc:
        raise ConfigError(f"{settings.spec_path} is not valid JSON: {exc}") from exc

    if not isinstance(spec, dict) or not spec.get("paths"):
        raise ConfigError(f"{settings.spec_path} has no paths; is it an OpenAPI spec?")

    # A curated entry that matches nothing would otherwise drop that tool
    # silently, leaving a server that looks healthy but is missing capability.
    missing = missing_from_spec(spec)
    if missing:
        raise ConfigError(
            "The OpenAPI spec no longer publishes these curated operations: "
            + ", ".join(missing)
            + ". Regenerate the spec, or update gsf_mcp/tools.py to match."
        )
    return spec


def load_icons() -> list[Icon]:
    """The NVIDIA mark, for clients that show an icon beside each server.

    Purely cosmetic, so a missing or unreadable file degrades to no icon rather
    than stopping a working server from starting.

    ``sizes`` is left unset deliberately: it is optional, an SVG has no natural
    pixel size, and clients have historically disagreed about whether the field
    is a string or a list.
    """
    try:
        svg = ICON_PATH.read_bytes()
    except OSError as exc:
        logger.warning("No icon at %s (%s); serving without one.", ICON_PATH, exc)
        return []

    encoded = base64.b64encode(svg).decode("ascii")
    return [
        Icon(src=f"data:{ICON_MIME_TYPE};base64,{encoded}", mimeType=ICON_MIME_TYPE)
    ]


API_KEY_HEADER = "x-api-key"
BEARER_HEADER = "authorization"

# GSF mints API tokens with this prefix, which is what makes a credential in the
# bearer slot distinguishable from an SSO id token.
GSF_TOKEN_PREFIX = "gsf_"


class CallerAuth(httpx.Auth):
    """Authenticate each outbound call as the caller who triggered it.

    Credentials belong to requests, not to the server. Holding one token for the
    process was fine while every process served one user — which is what stdio
    is — but on the HTTP transport it makes every caller act as that token's
    owner, inheriting their permissions and their conversation history. So the
    credential is resolved per request, from the incoming request itself.

    ``settings.api_token`` remains as a fallback for the cases where a
    process-wide identity is the correct one: stdio, and an HTTP deployment that
    explicitly opted into a single shared identity.

    Attaching this as httpx auth rather than at each call site is deliberate: it
    covers the generated tools and the hand-written streaming one through the
    single client they share, so no tool can be added later that forgets to
    authenticate.
    """

    def __init__(self, fallback_token: str = "") -> None:
        self._fallback = fallback_token

    def auth_flow(self, request: httpx.Request):  # type: ignore[override]
        header, value = self._credential()
        # Drop both first: a request must never carry two competing identities,
        # whichever slot the incoming one arrived in.
        for name in (API_KEY_HEADER, BEARER_HEADER):
            if name in request.headers:
                del request.headers[name]
        request.headers[header] = value
        yield request

    def _credential(self) -> tuple[str, str]:
        """Return the header name and value to authenticate this request with."""
        # `authorization` is excluded from this view by default, on the sound
        # general principle that forwarding it blindly is usually wrong. Here it
        # is precisely what we are after, so ask for it back.
        headers = get_http_headers(include={BEARER_HEADER})

        api_key = (headers.get(API_KEY_HEADER) or "").strip()
        if api_key:
            return API_KEY_HEADER, api_key

        bearer = (headers.get(BEARER_HEADER) or "").strip()
        scheme, _, token = bearer.partition(" ")
        token = token.strip()
        if scheme.lower() == "bearer" and token:
            # A GSF API token is unambiguous, so hand it to the header GSF
            # resolves first. Anything else here is an SSO id token, which only
            # resolves from the bearer slot.
            if token.startswith(GSF_TOKEN_PREFIX):
                return API_KEY_HEADER, token
            return BEARER_HEADER, bearer

        if self._fallback:
            return API_KEY_HEADER, self._fallback

        raise ToolError(
            "No GSF credential on this request. This server holds no token of "
            "its own, so each caller authenticates as itself: send a GSF API "
            "token in an 'x-api-key' header, or as 'Authorization: Bearer'. "
            "Mint one in the GSF UI under user menu → API Tokens."
        )


def build_client(settings: Settings) -> httpx.AsyncClient:
    """HTTP client for the public GSF API.

    The credential is not set here: :class:`CallerAuth` resolves one per
    request, so a single client can serve callers with different identities.
    """
    return httpx.AsyncClient(
        base_url=settings.api_url,
        auth=CallerAuth(settings.api_token),
        timeout=settings.timeout_s,
        # An agent harness may fan out across several tools at once.
        limits=httpx.Limits(max_connections=10, max_keepalive_connections=5),
    )


def build_server(settings: Settings) -> tuple[FastMCP, httpx.AsyncClient]:
    """Build the server and the client it owns.

    The client is returned rather than hidden so the caller can close it; it
    outlives any single request because the streaming chat tool holds it open.
    """
    spec = load_spec(settings)
    client = build_client(settings)

    mcp: FastMCP = FastMCP.from_openapi(
        openapi_spec=spec,
        client=client,
        name=SERVER_NAME,
        route_maps=route_maps(),
        mcp_names=names_by_operation_id(spec),
        mcp_component_fn=apply_description,
        # Forwarded to the FastMCP constructor. Without an explicit version the
        # handshake advertises FastMCP's own, which reads as a GSF version to
        # anyone looking at the client's server list.
        instructions=INSTRUCTIONS,
        version=get_version(),
        icons=load_icons(),
    )

    chat.register(mcp, settings, client)

    logger.info(
        "GSF MCP server built against %s (spec: %s)",
        settings.api_url,
        settings.spec_path,
    )
    if settings.transport == "http":
        if settings.api_token:
            logger.warning(
                "Authenticating every caller as the owner of GSF_API_TOKEN "
                "(GSF_MCP_ALLOW_SHARED_TOKEN is set). Callers do not get their "
                "own permissions or conversation history."
            )
        else:
            logger.info(
                "Callers authenticate per request via x-api-key or "
                "Authorization: Bearer."
            )
    return mcp, client


__all__ = [
    "API_KEY_HEADER",
    "BEARER_HEADER",
    "ICON_PATH",
    "INSTRUCTIONS",
    "SERVER_NAME",
    "CallerAuth",
    "build_client",
    "build_server",
    "load_icons",
    "load_spec",
]
