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

Which token that is depends on who the server serves. On `stdio` each user's
client starts its own process, so `GSF_API_TOKEN` is that user's identity. On
`http` one process may serve many people, so the credential travels with each
request and the server keeps none — see [Running it
remotely](#running-it-remotely). An `http` deployment can also run the login
itself, so nobody mints a token by hand at all: see [Signing in with
SSO](#signing-in-with-sso).

It also means users install almost nothing. `gsf-mcp` is its own distribution
(source in [`mcp/`](../mcp/)) depending only on `fastmcp`, `httpx`,
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

This is the `stdio` setup, where the process is yours alone. For a shared server
the token goes in the caller's request instead — see [Running it
remotely](#running-it-remotely).

Then install and run it. No clone needed — `uvx` fetches the package straight
from the repository, builds it, and runs it:

```sh
uvx --from "git+https://github.com/NVIDIA/GSF.git#subdirectory=mcp" gsf-mcp
```

Working from a checkout, point `--from` at the directory instead:

```sh
uvx --from ./mcp gsf-mcp
```

Or install into a virtualenv, which gives you a stable path to point a client at
and avoids a build on every start:

```sh
cd mcp && uv venv && uv pip install -e .
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

If `uvx` is on the PATH your client sees, this keeps no virtualenv around, at the
cost of a build on each start — and needs no checkout at all:

```json
"command": "uvx",
"args": ["--from", "git+https://github.com/NVIDIA/GSF.git#subdirectory=mcp", "gsf-mcp"]
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
| `check_readiness` | Whether this deployment can answer anything at all: semantic layer compiled, and a connection to execute SQL. |
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

### About `check_readiness`

Answering a question needs two independent things: a compiled semantic layer to
resolve the question against, and a live database connection to run the SQL on.
Having one without the other is the deployment's most misleading state, because
it looks entirely healthy — the glossary reads, terms resolve, columns and
calculations come back — and every question still fails.

It fails expensively and vaguely. `ask_data` generates SQL it cannot execute,
retries, and eventually returns an empty answer after minutes of model calls,
which is indistinguishable from a question that was simply not understood. The
two have nothing in common: one is fixed in the GSF UI, the other by rewording.

`check_readiness` is three reads that tell those apart up front, and it reports
every blocker at once rather than one per round trip. Note that `databases` can
be non-empty while `can_execute_sql` is false — the catalog outlives the
connection it was ingested from, so a named database is not evidence that
anything can be queried.

The three reads do not need the same permission, which matters once callers sign
in as themselves rather than sharing an admin token. Reading connections is
admin-only on a default deployment, so a viewer gets a 403 there while chat and
the catalog read fine. That is not a broken deployment and not a bad token, so
the fact is reported under `unverified` with the matching field set to null, and
the verdict stands on what could be checked. A `ready` result alongside an
`unverified` entry means questions can be asked. Only a 401 — the credential
itself — fails the tool, because every read would fail the same way.

## Configuration

| Variable | Default | Meaning |
| --- | --- | --- |
| `GSF_API_TOKEN` | *required on `stdio`* | API token. Acts as its owner. Not used on `http`, where callers authenticate themselves. |
| `GSF_API_URL` | `http://localhost:3000` | Base URL of the GSF web app. |
| `GSF_MCP_TRANSPORT` | `stdio` | `stdio` or `http`. |
| `GSF_MCP_HOST` | `0.0.0.0` | Bind address, `http` transport only. |
| `GSF_MCP_PORT` | `3003` | Bind port, `http` transport only. |
| `GSF_MCP_TIMEOUT_S` | `30` | Timeout for catalog and glossary calls. |
| `GSF_MCP_CHAT_TIMEOUT_S` | `900` | Timeout for one `ask_data` run. |
| `GSF_MCP_ALLOW_SHARED_TOKEN` | unset | Permit one `GSF_API_TOKEN` to serve every `http` caller. See below. |
| `GSF_OPENAPI_SPEC` | bundled with the package | Override the spec tools are generated from. |
| `GSF_MCP_OIDC_CONFIG_URL` | unset | Provider discovery document. Enables browser sign-in; `http` only. |
| `GSF_MCP_OIDC_CLIENT_ID` | unset | This server's client id at that provider. |
| `GSF_MCP_OIDC_CLIENT_SECRET` | unset | Its client secret. Also derives the key the server signs its own tokens with. |
| `GSF_MCP_PUBLIC_URL` | unset | Where callers reach *this* server. The redirect URI is built from it. |
| `GSF_MCP_OIDC_SCOPES` | `openid email profile` | Scopes to request. Must include `openid`. |
| `GSF_MCP_OIDC_REDIRECT_PATH` | `/auth/callback` | Callback path under `GSF_MCP_PUBLIC_URL`, to match an existing registration. |

## Running it remotely

```sh
GSF_MCP_TRANSPORT=http GSF_MCP_PORT=3003 gsf-mcp
```

Note the absence of a token. **On the HTTP transport each caller authenticates as
itself and the server holds no credential of its own.** Callers send their own GSF
API token per request, in either header:

```
x-api-key: gsf_...
Authorization: Bearer gsf_...
```

An `Authorization` header that is not a GSF API token is forwarded as a bearer
token, so an SSO id token works too. A request carrying no credential is refused
with an error saying so, rather than quietly acting as somebody else.

This is what makes a shared deployment sound: a session can do exactly what its
caller's token allows, and conversation history belongs to that caller.

> [!IMPORTANT]
> Setting `GSF_API_TOKEN` on the HTTP transport makes **every** caller act as that
> token's owner — their permissions and their conversation history. Because
> nothing in the protocol would reveal that, the server refuses to start in that
> configuration unless you also set `GSF_MCP_ALLOW_SHARED_TOKEN=1`.
>
> That combination is legitimate for a single-user deployment or an automation
> account. It is never what you want for a team.

## Signing in with SSO

Everything above still expects each caller to hold a GSF API token, minted by
hand and pasted into a client config. Point the server at an OIDC provider
instead and that step disappears:

```sh
export GSF_MCP_TRANSPORT=http
export GSF_MCP_PUBLIC_URL=https://gsf-mcp.example
export GSF_MCP_OIDC_CONFIG_URL=https://login.example.com/.well-known/openid-configuration
export GSF_MCP_OIDC_CLIENT_ID=...
export GSF_MCP_OIDC_CLIENT_SECRET=...
gsf-mcp
```

The client now discovers that the server wants authorization, opens a browser,
and the user signs in with the identity they already have. Nothing is copied
between systems.

**Register the redirect URI.** The provider must accept
`$GSF_MCP_PUBLIC_URL/auth/callback`, which is this server's callback and not
GSF's. Registering only the GSF web app's callback is the common mistake, and it
fails at the provider with `invalid redirect_uri` before the login can start. Set
`GSF_MCP_OIDC_REDIRECT_PATH` if the registration cannot be changed and an
approved path has to be matched instead.

**Use the provider GSF already trusts.** GSF verifies a bearer token against the
SSO provider configured in its own settings, so signing in against a different
one produces a token it will reject. Point both at the same issuer.

### What GSF receives

FastMCP does not hand clients their upstream tokens — it issues its own and keeps
the provider's server-side. So the `Authorization` header arriving on a tool call
is a *FastMCP* token, which GSF cannot verify: it is not signed by the provider.

The server therefore captures the provider's **id token** during sign-in and
forwards that instead. An id token is always a JWT verifiable through the
provider's JWKS and always carries the identity claims GSF resolves an account
by, whereas access tokens are often opaque or scoped to an audience no third
party can check. This is also why `openid` is not optional: without it the
provider returns no id token, every call fails to authenticate, and the sign-in
that preceded it looked perfectly successful.

Two consequences worth knowing:

- The id token is embedded in the token FastMCP issues, so the client holds it
  too. That is a narrow widening — the client just proved it controls that
  identity — but it is a real one.
- The signing key is derived from `GSF_MCP_OIDC_CLIENT_SECRET`, so sessions
  survive a restart and replicas accept each other's tokens. Rotating the secret
  invalidates outstanding sessions, which then simply re-authenticate.

Registered clients are cached under FastMCP's data directory. In a container,
either mount it or point `FASTMCP_HOME` somewhere writable, or clients
re-register on every restart.

### One extra click

Before redirecting to the provider, FastMCP shows a consent page naming the
client that asked. It is deliberate: any client can register itself here, so
without it a link could silently obtain a token in the user's name. The cost is
one approval the first time a given client connects — worth keeping, but it does
mean sign-in is not literally zero interaction.

## Extending the tool surface

The tool set is an explicit allow-list in
[`mcp/gsf_mcp/tools.py`](../mcp/gsf_mcp/tools.py). The spec publishes 82
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
again inside the package at `mcp/gsf_mcp/gsf-api.json`, because the server
reads it at startup and must work from an ordinary install where no `docs/`
directory exists. `pnpm openapi` writes both, and CI diffs both — so never edit
the packaged copy by hand.

## Troubleshooting

**"GSF_API_TOKEN is required"** — not exported, or the client's `env` block does
not include it. GUI clients do not inherit your shell.

**"GSF rejected the credentials"** — the token is expired or revoked, the signed-in
session has lapsed, or the account lacks chat permission. With a token, check it
with `curl -H "x-api-key: $GSF_API_TOKEN" $GSF_API_URL/api/terms`; with SSO, sign
in again.

**"No GSF credential on this request"** — an `http` caller sent neither
`x-api-key` nor `Authorization`. Add the token to that client's headers; the
server has none to fall back on by design.

**"GSF_API_TOKEN is set with GSF_MCP_TRANSPORT=http"** — the shared-identity
guard. Unset the token so callers authenticate themselves, or set
`GSF_MCP_ALLOW_SHARED_TOKEN=1` if one identity for everyone is genuinely intended.

**"GSF cannot answer right now"** — usually the semantic layer was never
compiled. Confirm with `check_readiness`. It also appears when a
`conversation_id` already has a turn in flight.

**`ask_data` returned an empty answer, with no SQL and no error** — the run
completed but retrieval found nothing to build a query from. Call
`check_readiness` first: most often no database connection is configured, so no
question can succeed no matter how it is phrased. If the deployment is ready,
the question's vocabulary is the problem — `search_terms` for the nouns in it,
since a concept named differently in the glossary, or a metric with no
calculation defined, both land here.

**Client shows no tools** — check the client's MCP logs. The server logs to
stderr, since stdout carries the protocol itself on stdio.

**"no longer publishes these curated operations"** — the spec and the allow-list
disagree. Run `pnpm openapi`, or update `mcp/gsf_mcp/tools.py`.

**"OpenAPI spec not found"** — `GSF_OPENAPI_SPEC` points somewhere wrong, or the
install is incomplete. Unset it to fall back to the packaged copy.

**"The GSF_MCP_OIDC_* variables need GSF_MCP_TRANSPORT=http"** — sign-in ends in
a browser redirect back to this server, and `stdio` has no address to redirect
to. There, the client's own `GSF_API_TOKEN` is the identity.

**"Incomplete OIDC configuration"** — one of the group is unset. Half-configured
sign-in is refused rather than quietly falling back to hand-minted tokens, which
would look fine until a caller arrived without one.

**`invalid redirect_uri` from the provider** — `$GSF_MCP_PUBLIC_URL/auth/callback`
is not registered for this client. Registering GSF's own callback is not enough;
the MCP server has its own.

**"Signed in, but the provider issued no id token"** — the client registration
does not grant `openid`, so nothing came back that GSF could verify. Check the
scopes on the registration, and `GSF_MCP_OIDC_SCOPES` if it was overridden.

**Signed in, but GSF answers 401** — the id token is from a different issuer than
the SSO provider configured in GSF's own settings, so GSF will not verify it.
Point both at the same one.
