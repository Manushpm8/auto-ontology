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

No clone needed — `uvx` fetches, builds, and runs it from the repository:

```sh
uvx --from "git+https://github.com/NVIDIA/GSF.git#subdirectory=gsf-mcp" gsf-mcp
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
      "command": "/absolute/path/to/gsf-mcp/.venv/bin/gsf-mcp",
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

One important limit: with `GSF_MCP_TRANSPORT=http` it serves every caller as the
single identity in its environment, so don't share one instance across a team.

Full documentation, including all configuration variables and how to extend the
tool surface, is in [`docs/mcp.md`](../docs/mcp.md).
