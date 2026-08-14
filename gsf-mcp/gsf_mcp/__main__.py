# SPDX-FileCopyrightText: Copyright (c) 2026, NVIDIA CORPORATION & AFFILIATES.
# All rights reserved.
# SPDX-License-Identifier: Apache-2.0

"""GSF MCP server entrypoint.

Exposes the GSF semantic layer to any MCP-capable agent harness. It holds no
database connections and no model configuration: it is an HTTP client of the
public GSF API and needs only a URL and an API token, so it runs equally well
beside a deployment or on a laptop far away from one.

Usage::

    export GSF_API_URL=https://gsf.example.com
    export GSF_API_TOKEN=gsf_...

    gsf-mcp                              # stdio, for a local client
    GSF_MCP_TRANSPORT=http gsf-mcp       # listen on a port instead

Without installing anything::

    uvx --from gsf-mcp gsf-mcp
"""

from __future__ import annotations

import logging
import sys

from dotenv import load_dotenv

# Load before importing config so a `.env` beside the working directory can
# supply the token, matching how the rest of GSF is configured locally.
load_dotenv()

from gsf_mcp import get_version  # noqa: E402
from gsf_mcp.config import ConfigError, load_settings  # noqa: E402
from gsf_mcp.server import build_server  # noqa: E402

logger = logging.getLogger(__name__)


def main() -> int:
    # On stdio the protocol itself occupies stdout, so logs must go to stderr
    # or they corrupt the session. basicConfig already defaults to stderr;
    # naming it keeps a later edit from quietly redirecting to stdout.
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s %(levelname)s %(name)s: %(message)s",
        stream=sys.stderr,
    )

    try:
        settings = load_settings()
    except ConfigError as exc:
        logger.error("%s", exc)
        return 2

    logger.info(
        "Starting GSF MCP server — version %s, transport %s",
        get_version(),
        settings.transport,
    )

    mcp, _client = build_server(settings)

    # FastMCP owns the event loop and closes the client's connections when the
    # transport shuts down, so there is no separate teardown to run here.
    if settings.transport == "http":
        mcp.run(transport="http", host=settings.host, port=settings.port)
    else:
        mcp.run(transport="stdio")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
