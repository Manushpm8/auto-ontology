<!--
SPDX-FileCopyrightText: Copyright (c) 2026, NVIDIA CORPORATION & AFFILIATES.
All rights reserved.
SPDX-License-Identifier: Apache-2.0
-->

# GSF MCP server

Lets any MCP-capable agent — Cursor, Claude Desktop, an internal agent — ask
questions about your data in natural language, and inspect the semantic layer
behind the answers. Anything that speaks [Model Context
Protocol](https://modelcontextprotocol.io) can use it.

## How it works

The server is an HTTP client of the public GSF API. It holds no database
credentials and no model configuration:

```
agent harness  ──MCP──▶  gsf-mcp  ──HTTPS──▶  Next.js (public API)
                                                   │
                                                   ▼
                                        FastAPI, Neo4j, Postgres
```

Authentication and permission checks already live in that Next.js layer, so the
server inherits them rather than reimplementing them. Two things follow: it can
run anywhere that can reach your deployment, and **a session can do exactly what
the person behind it can do** — a viewer gets a viewer's answers.

## Running it

Run one server and let people log in. Users configure a URL and nothing else:

```sh
GSF_MCP_TRANSPORT=http GSF_MCP_SIGN_IN=gsf GSF_API_URL=https://gsf.example.com gsf-mcp
```

Install with `uvx`, which fetches and builds straight from the repository, so
there is nothing to clone:

```sh
uvx --from "git+https://github.com/NVIDIA/GSF.git#subdirectory=mcp" gsf-mcp
```

> [!NOTE]
> GSF is NVIDIA-internal today, so this install needs GitHub credentials with
> access to the repository (`gh auth login`, or any cached git credential
> helper). It shortens to a plain `uvx gsf-mcp` once the package is published to
> PyPI; until then `uvx gsf-mcp` alone will not resolve.

### Connecting a client

Point the client at the URL. There are no credentials in the config:

```json
{
  "mcpServers": {
    "gsf": {
      "url": "https://gsf-mcp.example/mcp"
    }
  }
}
```

The client sees that the server wants authorization and offers to sign in —
Cursor lists it as needing login. The user gets GSF's ordinary login page, with
whatever SSO that deployment uses, and the client ends up holding a token it
manages itself. Nothing is minted or pasted by hand, and every call runs as the
person who signed in.

Note the `/mcp` suffix: that is the endpoint, not the server's root. Restart the
client after editing its config, since most read MCP configuration only at
startup.

Then ask something like *"what does GSF mean by an active customer, and how many
were there last quarter?"*

### Running it just for yourself

A client can also start the server itself, as a local process that serves only
you. This is the `stdio` transport, and it is the default. There is no URL and no
sign-in, so it authenticates with a GSF API token instead — mint one in GSF under
user menu → **API Tokens**, and give it to the client:

```json
{
  "mcpServers": {
    "gsf": {
      "command": "uvx",
      "args": ["--from", "git+https://github.com/NVIDIA/GSF.git#subdirectory=mcp", "gsf-mcp"],
      "env": {
        "GSF_API_URL": "https://gsf.example.com",
        "GSF_API_TOKEN": "gsf_..."
      }
    }
  }
}
```

The token is simply your own identity here, because the process is yours alone.
That is why it is safe in this mode and refused in the shared one: see
[`GSF_MCP_ALLOW_SHARED_TOKEN`](#one-identity-for-everyone).

### Against GSF on your own machine

`GSF_API_URL` is the only thing that changes — for the Docker stack, the web app
is on `:3000`:

```sh
GSF_MCP_TRANSPORT=http GSF_MCP_SIGN_IN=gsf GSF_API_URL=http://localhost:3000 gsf-mcp
```

Nothing else is needed. In particular there is no writable data directory to
arrange, because in this mode GSF holds the client registrations rather than this
server, and no public URL to set — it is derived, and a wildcard bind resolves to
`localhost`. If you do pin `GSF_MCP_HOST`, set `GSF_MCP_PUBLIC_URL` to the URL
clients actually use: the metadata is built from the bind address, and a client
configured for `localhost` rejects a server calling itself `127.0.0.1`.

Sign-in does require that the GSF you point at is new enough to be an
authorization server, which a previously built image will not be. Confirm it
before starting, since the failure otherwise surfaces as a client that cannot log
in:

```sh
curl -s -o /dev/null -w '%{http_code}\n' $GSF_API_URL/.well-known/oauth-authorization-server
```

A `200` is what you want. Anything else means running the frontend from your
checkout instead — `pnpm dev`, with `GSF_API_URL` pointing at its port.

## Sign-in

`GSF_MCP_SIGN_IN=gsf` makes GSF the authorization server that clients log in
against. The MCP server holds no client id, no client secret, and no redirect
URI, so there is nothing to configure per deployment and nothing to register with
an identity provider — clients register themselves with GSF automatically.

Registrations and grants live in GSF's database, so replicas share them and a
restart signs nobody out. Permissions come from the account on every call rather
than from the token, so changing a role, banning a user, or deleting a grant
takes effect immediately.

The tokens are opaque, so the server asks GSF to check each one. If GSF cannot be
reached the call fails as an error rather than as "sign in again", so an outage
does not send everyone into a login that cannot succeed either.

This needs a GSF version that serves `/.well-known/oauth-authorization-server`.
Against an older deployment it fails at discovery, before any browser opens.

## Tools

Everything here reads. Nothing modifies the glossary, the catalog, or the
underlying databases.

| Tool | What it is for |
| --- | --- |
| `ask_question` | **The primary tool.** Ask a question in plain language; get the answer, the SQL GSF ran, and the rows. |
| `check_answerable` | Grade whether the semantic layer covers a question. Cheap pre-flight before `ask_question`. |
| `check_readiness` | Whether this deployment can answer anything at all. |
| `search_terms` | Search the business glossary. |
| `get_term` | One term: description, synonyms, related terms. |
| `get_term_columns` | The physical columns a term maps to. |
| `get_term_sql_attributes` | The SQL attributes defined under a term. |
| `get_sql_attribute` | One SQL attribute: its expression and purpose. |
| `describe_table` | A table's columns, related terms, and SQL attributes together. |
| `list_example_queries` | Curated example analyses — useful few-shot context. |

There is deliberately no tool for browsing databases, schemas, or raw columns.
Consumers are meant to reach the data through the semantic layer, and a catalog
browser invites an agent to reason about raw tables instead — so those routes
stay unpublished even though the API offers them. `describe_table` covers the
legitimate case, since what it returns is the terms and SQL attributes a table
participates in.

The server also advertises **instructions** at handshake describing how the tools
sequence, which spares the model from inferring it — left to itself it tends to
reach straight for `ask_question`.

### About `ask_question`

It is a call to `POST /api/chat/completions`, the same endpoint the web UI uses,
which runs the full text-to-SQL agent: many sequential model calls, typically
tens of seconds. It returns the answer, the SQL, and the rows as separate fields
rather than the markdown the UI receives, so an agent can use the SQL without
parsing prose. Results are capped at 100 rows, with `row_count` and `truncated`
reporting what was withheld.

It emits MCP progress notifications as the agent works, mirroring the reasoning
trace the web UI shows. The client-side ceiling is `GSF_MCP_CHAT_TIMEOUT_S`
(default 900s); the backend caps individual SQL statements at 30s but does not
cap a whole run.

### About `check_readiness`

Answering a question needs two independent things: a compiled semantic layer to
resolve the question against, and a live database connection to run the SQL on.
Having one without the other is this deployment's most misleading state, because
everything looks healthy — the glossary reads, terms resolve — and every question
still fails, expensively and vaguely, after minutes of model calls.

`check_readiness` tells that apart up front, and reports every blocker at once.
Note that `databases` can be non-empty while `can_execute_sql` is false: the
catalog outlives the connection it was ingested from, so a named database is not
evidence that anything can be queried.

Its reads do not all need the same permission. Reading connections is admin-only
by default, so a viewer gets a 403 there while chat and the catalog read fine.
That is neither a broken deployment nor a bad credential, so the fact is reported
under `unverified` and the verdict stands on what could be checked. Only a 401
fails the tool, because every read would fail the same way.

## Configuration

| Variable | Default | Meaning |
| --- | --- | --- |
| `GSF_API_URL` | `http://localhost:3000` | Base URL of the GSF web app. |
| `GSF_MCP_TRANSPORT` | `stdio` | `stdio` for a local process, `http` for a shared server. |
| `GSF_MCP_SIGN_IN` | `off` | Set to `gsf` to have callers log in against GSF. `http` only. |
| `GSF_API_TOKEN` | *required on `stdio`* | API token, used only when the server runs just for you. |

Rarely needed: `GSF_MCP_HOST` and `GSF_MCP_PORT` (bind address and port, default
`0.0.0.0:3003`), `GSF_MCP_PUBLIC_URL` (where callers reach this server, derived
from those two), `GSF_MCP_TIMEOUT_S` and `GSF_MCP_CHAT_TIMEOUT_S` (request
timeouts, `30` and `900`), and `GSF_OPENAPI_SPEC` (override the spec tools are
generated from).

### One identity for everyone

Setting `GSF_API_TOKEN` on the `http` transport would make **every** caller act
as that token's owner — their permissions and their conversation history — and
nothing in the protocol would reveal it. So the server refuses to start that way
unless `GSF_MCP_ALLOW_SHARED_TOKEN=1` says it is deliberate.

It is a legitimate choice for a single-user deployment or an automation account,
and it is never what a team wants. The variable exists so that choice has to be
made on purpose rather than by leaving a token exported.

Without sign-in and without a shared token, an `http` caller authenticates itself
per request, sending its own GSF API token in an `x-api-key` or `Authorization`
header.

## Signing in through an identity provider instead

Use this only when tokens must come from a specific corporate provider rather
than from GSF. It needs a client id, a client secret, and a redirect URI that
someone has registered with that provider — which is the cost `GSF_MCP_SIGN_IN=gsf`
avoids.

```sh
export GSF_MCP_TRANSPORT=http
export GSF_MCP_PUBLIC_URL=https://gsf-mcp.example
export GSF_MCP_OIDC_CONFIG_URL=https://login.example.com/.well-known/openid-configuration
export GSF_MCP_OIDC_CLIENT_ID=...
export GSF_MCP_OIDC_CLIENT_SECRET=...
gsf-mcp
```

The provider must accept `$GSF_MCP_PUBLIC_URL/auth/callback` — this server's
callback, not GSF's. Registering only the GSF web app's callback is the common
mistake, and it fails with `invalid redirect_uri` before the login can start. Set
`GSF_MCP_OIDC_REDIRECT_PATH` to match an already-approved path if the
registration cannot be changed. Point the provider at the same issuer GSF trusts,
or GSF will reject the resulting token.

Two details worth knowing. FastMCP issues its own tokens and keeps the provider's
server-side, so the server captures the provider's **id token** during sign-in
and forwards that instead — which is why `openid` must be among
`GSF_MCP_OIDC_SCOPES` (default `openid email profile`). And FastMCP shows a
consent page naming the client before redirecting, since any client can register
itself; that is one approval the first time a client connects.

## Extending the tool surface

The tool set is an explicit allow-list in
[`mcp/gsf_mcp/tools.py`](../mcp/gsf_mcp/tools.py). The spec publishes 87
operations, and exposing all of them would degrade tool selection badly. To
publish another, add a `ToolSpec` naming its method, path, an agent-facing name,
and a description that says *when to reach for it*. Startup fails loudly if a
curated entry no longer exists in the spec, so a rename upstream cannot silently
drop a tool.

Parameters and descriptions come from
[`gsf-api.json`](./openapi/gsf-api.json), which is committed twice: canonically
under `docs/openapi/`, and again inside the package, because the server reads it
at startup and must work from an install where no `docs/` directory exists.
`pnpm openapi` writes both and CI diffs both, so never edit the packaged copy by
hand.

## Troubleshooting

**`ask_question` returned an empty answer, with no SQL and no error** — the run
completed but retrieval found nothing to build a query from. Call
`check_readiness` first: most often no database connection is configured, so no
question can succeed however it is phrased. If the deployment is ready, the
question's vocabulary is the problem — `search_terms` for the nouns in it.

**"GSF cannot answer right now"** — usually the semantic layer was never
compiled; confirm with `check_readiness`. It also appears when a
`conversation_id` already has a turn in flight.

**"GSF rejected the credentials"** — the token is expired or revoked, the
signed-in session has lapsed, or the account lacks chat permission. With a token,
check it with `curl -H "x-api-key: $GSF_API_TOKEN" $GSF_API_URL/api/terms`.

**"This request carried no signed-in session"** — the grant behind the call
expired or was revoked in GSF. Sign in again from the client.

**"Protected resource ... does not match expected ..."** — the server advertises
the address it was bound to, and the client is reaching it under a different
spelling of the same host, almost always `127.0.0.1` against `localhost`. Set
`GSF_MCP_PUBLIC_URL` to the URL the client uses, or leave `GSF_MCP_HOST` unset.

**A client cannot discover how to sign in** — check that
`$GSF_API_URL/.well-known/oauth-authorization-server` returns JSON. A redirect to
the login page instead means the deployment predates that route.

**"GSF_API_TOKEN is required"** — not exported, or missing from the client's `env`
block. GUI clients do not inherit your shell.

**"GSF_API_TOKEN is set with GSF_MCP_TRANSPORT=http"** — the shared-identity
guard above. Unset the token, or opt in deliberately.

**Client shows no tools** — check the client's MCP logs. The server logs to
stderr, since stdout carries the protocol itself on stdio.
