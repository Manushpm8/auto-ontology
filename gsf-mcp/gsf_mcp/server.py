# SPDX-FileCopyrightText: Copyright (c) 2026, NVIDIA CORPORATION & AFFILIATES.
# All rights reserved.
# SPDX-License-Identifier: Apache-2.0

"""Assemble the GSF MCP server from the published OpenAPI spec."""

from __future__ import annotations

import json
import logging
from typing import Any

import httpx
from fastmcp import FastMCP

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
            + ". Regenerate the spec, or update gsf/mcp/tools.py to match."
        )
    return spec


def build_client(settings: Settings) -> httpx.AsyncClient:
    """HTTP client for the public GSF API, authenticated as the token's owner.

    ``x-api-key`` rather than a bearer header: both are accepted, but only this
    one is unambiguous, since the bearer slot is also where an SSO id token
    would arrive.
    """
    return httpx.AsyncClient(
        base_url=settings.api_url,
        headers={"x-api-key": settings.api_token},
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
    )

    chat.register(mcp, settings, client)

    logger.info(
        "GSF MCP server built against %s (spec: %s)",
        settings.api_url,
        settings.spec_path,
    )
    return mcp, client


__all__ = ["INSTRUCTIONS", "SERVER_NAME", "build_client", "build_server", "load_spec"]
