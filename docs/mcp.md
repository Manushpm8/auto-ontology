<!--
SPDX-FileCopyrightText: Copyright (c) 2026, NVIDIA CORPORATION & AFFILIATES.
All rights reserved.
SPDX-License-Identifier: Apache-2.0
-->

# GSF MCP server

Exposes the GSF semantic layer to any MCP-capable agent harness — Cursor, Claude
Desktop, an internal agent, anything that speaks [Model Context
Protocol](https://modelcontextprotocol.io). An agent gets one tool that answers
questions about your data in natural language, plus a set of read-only tools for
inspecting the glossary and catalog behind those answers.

## How it fits

The MCP server is an **HTTP client of the public GSF API**. It holds no database
credentials, no Neo4j connection, and no model configuration:

```
agent harness  ──MCP──▶  gsf-mcp  ──HTTPS + x-api-key──▶  Next.js (public API)
                                                              │
                                                              ▼
                                                   FastAPI, Neo4j, Postgres
```

This is deliberate. Authentication, permission checks, and audit all live in the
Next.js layer; the Python services behind it are ClusterIP-only and are not
reachable directly (see [`docs/openapi/README.md`](./openapi/README.md)). Going
through the public API means the MCP server inherits all of that for free
instead of reimplementing it, and it means the server does not have to run
anywhere near the deployment it talks to. Pointing it at a laptop's dev stack or
at a production instance differs only in `GSF_API_URL`.

It follows that **an MCP session can do exactly what its token's owner can do**,
no more. A viewer's token yields a viewer's answers.

It also means users install almost nothing. `gsf-mcp` is its own distribution
(source in [`gsf-mcp/`](../gsf-mcp/)) depending only on `fastmcp`, `httpx`,
`pydantic`, and `python-dotenv` — around 140 packages installed in seconds,
against the 450-plus and gigabyte-plus that `gsf-server` needs for its database
drivers and model tooling. Nobody has to clone GSF or run its backend locally to
use this.

## Quickstart

Mint an API token: user menu (top right) → **API Tokens** → **New token**. It is
shown once. See [API tokens](../README.md#api-tokens-scripting) for the details.

```sh
export GSF_API_URL=https://gsf.example.com   # default: http://localhost:3000
export GSF_API_TOKEN=gsf_...
```

Then install and run it. No clone needed — `uvx` fetches the package straight
from the repository, builds it, and runs it:

```sh
uvx --from "git+https://github.com/NVIDIA/GSF.git#subdirectory=gsf-mcp" gsf-mcp
```

Working from a checkout, point `--from` at the directory instead:

```sh
uvx --from ./gsf-mcp gsf-mcp
```

Or install into a virtualenv, which gives you a stable path to point a client at
and avoids a build on every start:

```sh
cd gsf-mcp && uv venv && uv pip install -e .
./.venv/bin/gsf-mcp
```

> [!NOTE]
> The repository is NVIDIA-internal today, so the `git+https` install needs
> GitHub credentials with access to it (`gh auth login`, or any cached git
> credential helper). It requires no NVIDIA network access beyond that. Once GSF
> is published to PyPI this becomes a plain `uvx gsf-mcp`; until then, `uvx
> gsf-mcp` and `pip install gsf-mcp` on their own will not resolve.

Any of these starts a stdio server, which is what local clients expect. It exits
immediately with an explanation if the environment is incomplete — a missing
token is reported at startup rather than as a 401 on every later tool call.

## Connecting a client

Most clients take the same shape of config — Cursor reads `~/.cursor/mcp.json`,
Claude Desktop reads `claude_desktop_config.json`. GUI clients do not inherit
your shell's PATH, so an absolute path to the console script is the most reliable
thing to point them at:

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

If `uvx` is on the PATH your client sees, this keeps no virtualenv around, at the
cost of a build on each start — and needs no checkout at all:

```json
"command": "uvx",
"args": ["--from", "git+https://github.com/NVIDIA/GSF.git#subdirectory=gsf-mcp", "gsf-mcp"]
```

Restart the client after editing its config — most read MCP configuration only at
startup. An editable install picks up code changes on the next server start.

Ask the agent something like *"what does GSF mean by an active customer, and how
many were there last quarter?"* — it should call `search_terms` and then
`ask_data`.

## Tools

Everything here reads. Nothing modifies the glossary, the catalog, or the
underlying databases.

| Tool | What it is for |
| --- | --- |
| `ask_data` | **The primary tool.** Ask a question in plain language; get the answer, the SQL GSF ran, and the rows. |
| `check_answerable` | Grade whether the semantic layer covers a question's entities. Cheap pre-flight before `ask_data`. |
| `search_terms` | Search the business glossary. |
| `get_term` | One term: description, synonyms, related terms. |
| `get_term_columns` | The physical columns a term maps to. |
| `get_term_calculations` | Reviewed SQL expressions defined under a term. |
| `get_calculation` | One calculation's expression and purpose. |
| `list_databases` | Databases connected to this deployment. |
| `list_schemas` | Schemas in a database. |
| `list_tables` | Tables in a schema. |
| `list_columns` | A table's columns, with curated descriptions. |
| `describe_table` | A table's columns, related terms, and calculations together. |
| `list_example_queries` | Curated example analyses — good few-shot context. |
| `get_semantic_layer_status` | Whether the semantic layer has been compiled. |

The server also advertises **instructions** at handshake describing how the
tools sequence, which spares the model from inferring it — left to itself it
tends to reach straight for `ask_data`.

### About `ask_data`

One call runs the full text-to-SQL agent: many sequential model calls, typically
tens of seconds. Two consequences worth knowing:

- It emits **MCP progress notifications** as the agent works, mirroring the
  reasoning trace the web UI shows. Clients that render progress will show it
  moving; those that do not will simply wait.
- The client-side ceiling is `GSF_MCP_CHAT_TIMEOUT_S` (default 900s). The
  backend caps individual SQL statements at 30s but does not cap a whole run.

It returns the answer, SQL, and rows as separate fields rather than the markdown
the web UI receives, so a calling agent can use the SQL without parsing prose.
Results are capped at 100 rows, with `row_count` and `truncated` reporting what
was withheld.

## Configuration

| Variable | Default | Meaning |
| --- | --- | --- |
| `GSF_API_TOKEN` | *required* | API token. Acts as its owner. |
| `GSF_API_URL` | `http://localhost:3000` | Base URL of the GSF web app. |
| `GSF_MCP_TRANSPORT` | `stdio` | `stdio` or `http`. |
| `GSF_MCP_HOST` | `0.0.0.0` | Bind address, `http` transport only. |
| `GSF_MCP_PORT` | `3003` | Bind port, `http` transport only. |
| `GSF_MCP_TIMEOUT_S` | `30` | Timeout for catalog and glossary calls. |
| `GSF_MCP_CHAT_TIMEOUT_S` | `900` | Timeout for one `ask_data` run. |
| `GSF_OPENAPI_SPEC` | bundled with the package | Override the spec tools are generated from. |

## Running it remotely

```sh
GSF_MCP_TRANSPORT=http GSF_MCP_PORT=3003 gsf-mcp
```

> [!IMPORTANT]
> **The HTTP transport currently serves every caller as a single identity.** The
> server authenticates to GSF with the one token in its environment, so a shared
> remote instance collapses all its users into that token's owner — their
> permissions, and their conversation history.
>
> This is fine for a single-user deployment or a trusted automation account. It
> is **not** suitable for exposing one instance to a team. Until the server
> accepts per-caller credentials (OAuth 2.1, which the MCP authorization spec
> defines for exactly this), give each user their own stdio process with their
> own token.

## Extending the tool surface

The tool set is an explicit allow-list in
[`gsf-mcp/gsf_mcp/tools.py`](../gsf-mcp/gsf_mcp/tools.py). The spec publishes 82
operations; exposing all of them would degrade tool selection badly and would
hand agents things like SSO configuration and token management. Everything not
named is excluded by a catch-all.

Parameters and their descriptions come from
[`gsf-api.json`](./openapi/gsf-api.json) and stay accurate because CI fails when
the spec drifts from the routes. Names and descriptions are overridden, because
the generated ones (`get_api_terms_term_id_`, "termsApi.list — ...") are written
for developers reading API docs, and a tool description is really a prompt.

To publish another endpoint, add a `ToolSpec` naming its method, path, an
agent-facing name, and a description that says *when to reach for it*. Startup
fails loudly if a curated entry no longer exists in the spec, so a rename
upstream cannot silently drop a tool.

The spec is committed twice: canonically at `docs/openapi/gsf-api.json`, and
again inside the package at `gsf-mcp/gsf_mcp/gsf-api.json`, because the server
reads it at startup and must work from an ordinary install where no `docs/`
directory exists. `pnpm openapi` writes both, and CI diffs both — so never edit
the packaged copy by hand.

## Troubleshooting

**"GSF_API_TOKEN is required"** — not exported, or the client's `env` block does
not include it. GUI clients do not inherit your shell.

**"GSF rejected the API token"** — expired, revoked, or its owner lacks chat
permission. Check with `curl -H "x-api-key: $GSF_API_TOKEN" $GSF_API_URL/api/terms`.

**"GSF cannot answer right now"** — usually the semantic layer was never
compiled. Confirm with `get_semantic_layer_status`. It also appears when a
`conversation_id` already has a turn in flight.

**Client shows no tools** — check the client's MCP logs. The server logs to
stderr, since stdout carries the protocol itself on stdio.

**"no longer publishes these curated operations"** — the spec and the allow-list
disagree. Run `pnpm openapi`, or update `gsf-mcp/gsf_mcp/tools.py`.

**"OpenAPI spec not found"** — `GSF_OPENAPI_SPEC` points somewhere wrong, or the
install is incomplete. Unset it to fall back to the packaged copy.
