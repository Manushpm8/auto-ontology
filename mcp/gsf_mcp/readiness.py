# SPDX-FileCopyrightText: Copyright (c) 2026, NVIDIA CORPORATION & AFFILIATES.
# All rights reserved.
# SPDX-License-Identifier: Apache-2.0

"""``check_readiness`` — whether this deployment can answer questions yet.

Hand-written rather than generated, because the answer is not any one endpoint.
A deployment can only answer a question when a catalog has been ingested, a live
database connection exists to run the SQL against, *and* the semantic layer has
been compiled to resolve the question with. Those three facts come from
unrelated routes, and no one of them is sufficient alone.

The combination is worth a tool of its own because its failure is silent. A
compiled glossary over no connection reads perfectly: ``search_terms`` returns
terms, ``get_term`` returns columns, ``get_semantic_layer_status`` reports the
layer as built. Nothing looks wrong until ``ask_data`` spends minutes writing
SQL it can never execute and returns an empty answer — which is
indistinguishable, from the caller's side, from a question that was simply not
understood. One cheap call up front turns that dead end into a fact.
"""

from __future__ import annotations

import asyncio
import logging
from typing import Any

import httpx
from fastmcp import FastMCP
from fastmcp.exceptions import ToolError
from pydantic import BaseModel, Field

from gsf_mcp.config import Settings

logger = logging.getLogger(__name__)

STATUS_PATH = "/api/semantic-compilation/status"
CONNECTIONS_PATH = "/api/connections"
DATABASES_PATH = "/api/datasources/dbs"


class Readiness(BaseModel):
    """What this deployment can currently do."""

    ready: bool = Field(
        description=(
            "True when a question can be understood, executed, and has data to "
            "run against."
        )
    )
    semantic_layer_built: bool = Field(
        default=False,
        description="Whether the glossary of business terms has been compiled.",
    )
    can_execute_sql: bool = Field(
        default=False,
        description="Whether a live database connection exists to run SQL against.",
    )
    catalog_present: bool = Field(
        default=False,
        description="Whether any database has been ingested for questions to reach.",
    )
    databases: list[str] = Field(
        default_factory=list,
        description=(
            "Databases present in the catalog. These can be non-empty while "
            "can_execute_sql is false: the catalog outlives the connection it "
            "was ingested from."
        ),
    )
    blockers: list[str] = Field(
        default_factory=list,
        description="What stands in the way, and what to do about it. Empty when ready.",
    )


async def _probe(client: httpx.AsyncClient, path: str) -> Any:
    """GET *path*, returning ``None`` when it cannot be read.

    A readiness check that dies on its first bad response is not much of a
    readiness check, so an unreadable endpoint degrades to an unknown that the
    caller is told about. Rejected credentials are the exception: every probe
    would fail the same way, and reporting "not ready" for what is really a bad
    token would send the caller off fixing the wrong thing.
    """
    try:
        response = await client.get(path)
    except httpx.HTTPError as exc:
        logger.warning("Readiness probe %s failed: %s", path, exc)
        return None

    if response.status_code in (401, 403):
        raise ToolError(
            "GSF rejected the API token while checking readiness. Check "
            "GSF_API_TOKEN is current and its owner has permission to read "
            f"the catalog. (HTTP {response.status_code})"
        )
    if response.status_code != 200:
        logger.warning(
            "Readiness probe %s returned HTTP %d", path, response.status_code
        )
        return None
    try:
        return response.json()
    except ValueError:
        logger.warning("Readiness probe %s did not return JSON", path)
        return None


def _summarise(status: Any, connections: Any, databases: Any) -> Readiness:
    """Fold the three probes into one verdict."""
    blockers: list[str] = []

    if status is None:
        blockers.append(
            "Could not read whether the semantic layer is compiled "
            f"({STATUS_PATH} was unreadable). GSF may be starting up or "
            "partially deployed."
        )
        built = False
    else:
        built = bool(status.get("calculated"))
        if not built:
            blockers.append(
                "The semantic layer has not been compiled, so there is no "
                "glossary to resolve a question against. Compile it in the GSF "
                "UI; nothing can be asked of the data until it finishes."
            )

    if connections is None:
        blockers.append(
            f"Could not read the configured connections ({CONNECTIONS_PATH} was "
            "unreadable), so it is unknown whether SQL can execute."
        )
        can_execute = False
    else:
        can_execute = int(connections.get("count") or 0) > 0
        if not can_execute:
            blockers.append(
                "No database connection is configured, so generated SQL cannot "
                "execute. Questions will still be accepted and will still cost "
                "a full agent run before failing with an empty answer. Add a "
                "connection in the GSF UI."
            )

    if databases is None:
        blockers.append(
            f"Could not read the catalog ({DATABASES_PATH} was unreadable), so "
            "it is unknown whether any data has been ingested."
        )
        names: list[str] = []
    else:
        names = [
            str(entry.get("name"))
            for entry in (databases.get("data") or [])
            if entry.get("name")
        ]
        if not names:
            blockers.append(
                "No database has been ingested, so there is nothing for a "
                "question to reach. A connection can exist before its catalog "
                "does: ingestion runs after the connection is saved, and an "
                "unfinished or failed ingest leaves the catalog empty. Check "
                "the ingestion service, or re-save the connection in the GSF UI."
            )

    return Readiness(
        ready=not blockers,
        semantic_layer_built=built,
        can_execute_sql=can_execute,
        catalog_present=bool(names),
        databases=names,
        blockers=blockers,
    )


def register(mcp: FastMCP, settings: Settings, client: httpx.AsyncClient) -> None:
    """Attach ``check_readiness`` to *mcp*."""

    @mcp.tool(
        name="check_readiness",
        description=(
            "Report whether this GSF deployment can answer questions at all: "
            "whether a catalog has been ingested, whether a live database "
            "connection exists to run SQL against, and whether the semantic "
            "layer has been compiled. All three are required.\n\n"
            "Use it before the first question against an unfamiliar "
            "deployment, and whenever ask_data returns an empty answer. It is "
            "three cheap reads, and it separates 'this deployment is not set "
            "up' from 'the question was not understood' — which otherwise look "
            "identical and are fixed in completely different places."
        ),
    )
    async def check_readiness() -> Readiness:
        """Probe the deployment and report what it can currently do."""
        status, connections, databases = await asyncio.gather(
            _probe(client, STATUS_PATH),
            _probe(client, CONNECTIONS_PATH),
            _probe(client, DATABASES_PATH),
        )
        readiness = _summarise(status, connections, databases)
        if not readiness.ready:
            logger.info("Deployment not ready: %s", "; ".join(readiness.blockers))
        return readiness


__all__ = [
    "CONNECTIONS_PATH",
    "DATABASES_PATH",
    "STATUS_PATH",
    "Readiness",
    "register",
]
