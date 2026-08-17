<!--
SPDX-FileCopyrightText: Copyright (c) 2026, NVIDIA CORPORATION & AFFILIATES.
All rights reserved.
SPDX-License-Identifier: Apache-2.0
-->

# gsf-mcp

An [MCP](https://modelcontextprotocol.io) server for
[GSF](https://github.com/NVIDIA/GSF) (Generative Semantic Fabric). It lets any
MCP-capable agent — Cursor, Claude Desktop, an internal agent — ask questions in
natural language about the data a GSF deployment is connected to, and inspect the
semantic layer behind the answers.

## Install and run

You need a GSF deployment and an API token from it (in GSF: user menu → **API
Tokens**).

```sh
export GSF_API_URL=https://gsf.example.com
export GSF_API_TOKEN=gsf_...
```

That token is for the default `stdio` transport, where your client starts the
process and it serves only you. If you are deploying one server for several
people, do not set it: on `GSF_MCP_TRANSPORT=http` each caller sends its own
credential per request and acts as itself. See [Notes](#notes).

No clone needed — `uvx` fetches, builds, and runs it from the repository:

```sh
uvx --from "git+https://github.com/NVIDIA/GSF.git#subdirectory=mcp" gsf-mcp
```

Or install into a virtualenv, which gives you a stable path for client config:

```sh
uv venv && uv pip install -e .   # from this directory
./.venv/bin/gsf-mcp
```

> [!NOTE]
> GSF is NVIDIA-internal today, so the `git+https` install needs GitHub
> credentials with access to the repo. It becomes a plain `uvx gsf-mcp` once the
> package is published to PyPI.

## Connect a client

Cursor (`~/.cursor/mcp.json`) or Claude Desktop (`claude_desktop_config.json`).
GUI clients don't inherit your shell's PATH, so give an absolute path:

```json
{
  "mcpServers": {
    "gsf": {
      "command": "/absolute/path/to/mcp/.venv/bin/gsf-mcp",
      "args": [],
      "env": {
        "GSF_API_URL": "https://gsf.example.com",
        "GSF_API_TOKEN": "gsf_..."
      }
    }
  }
}
```

Then ask something like *"what does GSF mean by an active customer, and how many
were there last quarter?"*

## Tools

`ask_data` is the one that answers questions: it runs GSF's text-to-SQL agent and
returns the answer, the SQL it ran, and the rows. The rest — `search_terms`,
`describe_table`, `list_databases`, `check_answerable` and friends — let an agent
learn the vocabulary and check its assumptions first.

Every tool reads. Nothing here modifies the glossary, the catalog, or the
underlying databases.

## Notes

This server is a plain HTTP client of the GSF API, so it needs no database
credentials and can run anywhere that can reach your deployment. It authenticates
with your API token and therefore **acts as you**, with exactly your permissions.

With `GSF_MCP_TRANSPORT=http` it holds no token at all: each caller sends its own
in an `x-api-key` or `Authorization` header, and so acts as itself. Setting
`GSF_API_TOKEN` there would make every caller share one identity, so the server
refuses to start unless `GSF_MCP_ALLOW_SHARED_TOKEN=1` says that is intended.

Full documentation, including all configuration variables and how to extend the
tool surface, is in [`docs/mcp.md`](../docs/mcp.md).
