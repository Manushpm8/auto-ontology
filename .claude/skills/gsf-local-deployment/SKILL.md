---
name: gsf-local-deployment
description: >-
  Bring up, connect, verify and diagnose the local Docker GSF stack — the write
  and operate side that the read-only GSF MCP server does not cover. Covers
  attaching a database connection, ingesting a catalog, compiling the semantic
  layer, deleting stale databases, and working out why ask_data returns an empty
  or confidently wrong answer. Use when the user says "ask_data returns nothing",
  "GSF can't see my database", "no database connection is configured", "connect
  dw/wwi to GSF", "re-ingest", "recompile the semantic layer", "GSF answers are
  wrong", "check_readiness says not ready", or when GSF is running locally in
  Docker and behaving as though it has no data.
---

# Operating the local GSF stack

## Read this first: the MCP covers reading, not operating

The `gsf` MCP server's 14 tools all **read**. None of them attaches a
connection, ingests a catalog, compiles the semantic layer, or repairs a
deployment. Use the MCP to observe (`check_readiness` especially) and this skill
to change things.

## The model — three independent preconditions

`ask_data` needs **all three** to be true. They fail independently, and GSF
stays superficially healthy when any one is missing:

1. **A catalog exists** — a `Database` node in Neo4j with `Schema`/`Table`/`Column` beneath it.
2. **That database is *connected*** — the `Database` node carries a `connection`
   property (or Vault holds a secret named for the database). Without it, SQL
   cannot execute.
3. **The semantic layer is compiled** — `Term` nodes exist.

The trap is that **1 and 3 can be true while 2 is false**. GSF then holds a
detailed description of data it cannot reach: the retrieval layer happily
matches your question to terms and tables, writes plausible SQL, and returns an
empty answer after a full (~2 minute) agent run. Worse, the model reasons
confidently about tables that no reachable database has.

`check_readiness` (MCP) is the cheapest first probe, but it only *gates* on 2 and
3. The catalog fills its informational `databases` list and can never raise a
blocker, so `ready: true` with `databases: []` still means there is nothing to
answer over. **Start there — then read `databases`, not just `ready`.**

## Stack facts (constants)

| Thing | Value |
|---|---|
| API (`gsf`) | `http://127.0.0.1:3001` — no auth locally |
| Ingestion service | `http://127.0.0.1:3002` — no auth locally |
| Frontend proxy | `http://localhost:3000` — **needs auth** |
| Frontend auth | `x-api-key: gsf_…` (or `Authorization: Bearer gsf_…`); mint in the UI |
| Neo4j | `bolt://localhost:7687`, browser `:7474`; password from repo `.env` `NEO4J_PASSWORD` |
| Postgres (app + vector + warehouses) | container `postgres` (`pgvector/pgvector:pg17`); host `localhost:5434` → container `5432` |
| Postgres creds | `POSTGRES_USER=gsf`, `POSTGRES_DB=gsf`; password in the container env |
| Docker network | `gsf_default` — shared by `gsf`, `gsf-ingestion-service`, `postgres` |

**Host from inside a container is `postgres:5432`, never `localhost:5434`.** The
5434 mapping exists only on the host. A connection stored with `localhost:5434`
tests fine from your shell and fails from the GSF container.

Handy shell setup. Shell state does **not** survive between agent tool calls, so
put the cypher helper on disk once instead of defining a function you cannot reuse:

```bash
REPO=/Users/lfleishman/Projects/GSF
cat > /tmp/cy.sh <<SH
#!/bin/sh
PW=\$(grep -m1 '^NEO4J_PASSWORD=' "$REPO/.env" | cut -d= -f2-)
exec docker exec neo4j cypher-shell -u neo4j -p "\$PW" "\$1"
SH
chmod +x /tmp/cy.sh
```

Every `cy "…"` below means `/tmp/cy.sh "…"`. `$PGPW` is not a persistent variable
either — read it in the same call that uses it:

```bash
PGPW=$(docker exec postgres env | grep -m1 '^POSTGRES_PASSWORD=' | cut -d= -f2-)
```

## Where connections actually live

`gsf/dal/connections.py`. There is **no `Connection` node label** — don't look
for one. Connection metadata is a JSON string on the `connection` property of
the `Database` node itself (`Labels.DB` resolves to `"Database"`), so the UI
connection and the catalog database are one node.

`list_connections()` resolves each `Database` node from Vault first (by database
name), then falls back to the `connection` property, and **skips databases with
neither**. So a database can be fully catalogued and still absent from
`/api/connections`.

`VAULT_ADDR` is unset locally, so `read_secret()` can never resolve — the node
property is the only working path.

```bash
# Which catalog databases are actually connected?
cy "MATCH (d:Database) RETURN d.name AS db, d.connection IS NOT NULL AS connected"
```

## Diagnose an empty or wrong answer

1. **`check_readiness`** (MCP). Believe its `blockers` — and read `databases`
   alongside them, since an empty catalog never shows up as a blocker.
2. **`blockers` mentions no connection** → run the cypher above. A `connected:
   FALSE` row is your answer: attach a connection (below).
3. **The catalog names a database that no longer exists in its own engine** →
   stale metadata that outlived its source. **Read the node's `connection.type`
   first.** GSF also catalogs MySQL, Snowflake, Databricks and HeavyDB, and none
   of those databases will ever appear in `pg_database` — this stack's `dw` is a
   MySQL database in the `beaver-mysql` container:

```bash
cy "MATCH (d:Database) RETURN d.name AS db, d.connection AS connection"
# then query the engine that node actually points at:
docker exec postgres psql -U gsf -tAc \
  "SELECT datname FROM pg_database WHERE datistemplate=false;"
docker exec beaver-mysql sh -c \
  'mysql -u root -p"$MYSQL_ROOT_PASSWORD" -N -e "SHOW DATABASES;"'
```

   A `Database` node with no matching row **in its own engine** is orphaned.
   Delete it (below) — do not leave it alongside a working database, or retrieval
   keeps matching questions to unreachable tables. The delete is irreversible, so
   confirm the engine first; `d.connection IS NULL` means "not connected", never
   "safe to delete".

4. **`Term` count is 0** → compile the semantic layer (below).
5. **Terms describe different data than the reachable database** (e.g. academic
   terms over a sales warehouse) → the semantic layer outlived its catalog.
   Delete the stale database and recompile.
6. **Answers return but with no numbers** → known weakness: GSF sometimes
   projects only the grouping column on ranking questions, so the ordering is
   correct but the metric is missing. Read the returned `sql` before trusting
   the prose.

## Attach a database connection

Types: `postgresql`/`postgres`, `mysql`, `snowflake`, `databricks`, `heavydb`
(`gsf/connectors/connection_string_factory.py`). Postgres and MySQL require
`host`, `user`, `password`, `database`; `port` defaults per type.

**Test first** — non-mutating, and it catches credential and reachability errors
before anything is stored. One catch: `/test` runs the already-connected guard
*before* it builds any connection string, so testing a database that is already
attached returns `422 A connection for database 'x' already exists` without ever
contacting it. That 422 is not a credential error.

```bash
TOKEN=gsf_…   # mint in the UI
cat > /tmp/conn.json <<JSON
{"connection":{"type":"postgresql","host":"postgres","port":"5432",
 "user":"gsf","password":"$PGPW","database":"wwi"}}
JSON
curl -s -X POST http://localhost:3000/api/connections/test \
  -H "x-api-key: $TOKEN" -H 'Content-Type: application/json' --data @/tmp/conn.json
```

`{"success":true,"schemas":[]}` is a **pass**. Empty `schemas` is normal for
Postgres, MySQL and HeavyDB — only Databricks and Snowflake enumerate schemas,
and for the others the check is just a `ping()`. Do not read `[]` as "no data".

Then save it:

```bash
curl -s -X POST http://localhost:3000/api/connections \
  -H "x-api-key: $TOKEN" -H 'Content-Type: application/json' --data @/tmp/conn.json
rm -f /tmp/conn.json   # it holds the password
```

**Saving validates nothing.** `POST /api/connections` stores the JSON and fires a
best-effort ingest that swallows every exception (`gsf/server/ingestion/proxy.py`),
so a wrong password, an unreachable host or a `localhost:5434` host all return
**201 Created** and then fail silently — leaving you in exactly the
connected-but-empty state this skill exists to diagnose. A 201 means "stored",
never "works". Always `/test` first, then confirm the catalog actually lands.

**Saving a connection auto-triggers ingestion.** Do not call `/ingest`
afterwards. Watch it land:

```bash
docker logs gsf-ingestion-service --since 3m 2>&1 | tail -30
cy "MATCH (d:Database)-[:CONTAINS]->(s:Schema) OPTIONAL MATCH (s)-[:CONTAINS]->(t:Table)
    RETURN d.name AS db, s.name AS schema, count(t) AS tables ORDER BY schema"
```

A 422 on save means a connection for that database name already exists
(`_database_already_connected`). Deleting it first is **destructive**: `DELETE
/api/connections/{database_name}` tears down the catalog subgraph, the semantic
layer and the pgvector rows along with the connection (see reference.md). Changing
stored credentials therefore costs a full re-ingest and recompile — there is no
edit-in-place.

## Delete a stale database

Removes its catalog nodes, semantic nodes and pgvector rows. Asynchronous (202):

```bash
curl -s -X POST "http://127.0.0.1:3002/ingest/delete?database_name=dw"
```

Confirm from the logs (`gsf.dal.reset` lines report node and row counts), then
re-check the graph. Deleting a database also removes the terms compiled over it.

## Compile the semantic layer

```bash
curl -s -X POST http://127.0.0.1:3002/semantic/compile          # compile what needs it
curl -s -X POST "http://127.0.0.1:3002/semantic/reset?database_name=wwi"  # wipe + rebuild one db
curl -s -X POST http://127.0.0.1:3002/semantic/stop             # abort at next db boundary
```

LLM-heavy and slow — budget **a term per table and 10-15 minutes per 30 tables**
(measured here: 13m04s for 32 tables). Table visits are only ~60% of the run; FK
resolution, SqlAttribute suggestion and bridge tables follow the
`Compilation complete` line. Needs an API key resolvable by
`gsf/utils/model_config.py`: `REASONING_API_KEY` / `EMBED_API_KEY`, else
`DEFAULT_MODELS_API_KEY`, else the legacy `NVIDIA_API_KEY`. **`OPENAI_API_KEY` is
ignored** — nothing in the repo reads it, and an unresolved key raises
`EnvironmentError` rather than compiling. Poll progress rather than guessing:

```bash
cy "MATCH (t:Term) RETURN count(t)"
curl -s http://localhost:3000/api/semantic-compilation/status -H "x-api-key: $TOKEN"
# {"calculated": true} as soon as ANY Term exists — it flips mid-pass
```

Treat it as done when the term count has stopped moving. `calculated` only reports
that a Term exists — it flips true after the *first* table of a running pass, and
stays true from an earlier one.

## Verify end to end

Never trust `ready: true` alone — confirm GSF's SQL against the database:

1. `check_readiness` → `ready: true`, `blockers: []`.
2. Ask a question that needs a join and an aggregation.
3. Re-run the returned `sql` directly with `psql` and compare rows.

```bash
docker exec postgres psql -U gsf -d wwi -c "<the sql GSF returned>"
```

## Gotchas (hard-won — read before debugging)

- **Catalog metadata outlives connections.** The headline failure. A database
  with a full catalog and compiled terms but no `connection` property produces
  confident, empty, expensive answers. `check_readiness` is the cheap guard.
- **`localhost:5434` vs `postgres:5432`.** Host mapping vs container network.
  Store the container form.
- **`schemas: []` from `/connections/test` is a pass**, not a failure — see above.
- **Saving a connection already ingests.** Calling `/ingest` yourself duplicates work.
- **`n_live_tup` is 0 for never-analyzed tables.** `pg_stat_user_tables` will
  claim every table is empty after a bulk load. Use `count(*)` to decide whether
  data exists.
- **`pg_stat_statements` profiling warnings during compilation are benign** —
  those are extension views in `public` that need `shared_preload_libraries`.
  GSF logs a WARNING with a traceback and skips them. Not a failure.
- **Compilation happily builds terms over Postgres extension views.** Anything
  in `public` is treated as business data, so a default compile mints junk
  glossary entries beside the real ones — on a stock WWI connection, 3 of 32
  terms are *Buffer Cache Entry* (`pg_buffercache`), *Statement Execution
  Statistic* and *Statement Statistics Info* (`pg_stat_statements*`). They
  consume retrieval budget and can be matched against a user's question. Audit
  by source schema with the `REPRESENTS` recipes in reference.md.
- **Embed-probe `404`/`400` against `inference-api.nvidia.com` is benign** — the
  remote NIM probe failing over to the local CPU embedder. Rows still get written.
- **`/api/health` on :3001 probes both Neo4j and Postgres** and returns 503 if
  either is down. It says nothing about connections or the semantic layer, so a
  200 here is compatible with a deployment that answers nothing.
- **`GET /api/sql-attributes` 405s on `:3000` but works on `:3001`.** The route
  exists (`gsf/server/sql_attributes/router.py:54`) and lists every SqlAttribute
  with its linked Term — the frontend simply doesn't proxy the GET. Read it from
  `:3001`, or use the MCP's `get_calculation` / `get_term_calculations`.
- **Don't use `rg -r`** when grepping this repo for facts; `-r` takes a
  replacement argument and silently rewrites matches in the output. It will make
  `x-api-key` read as whatever you passed to `-n`.

## Deeper reference

See [reference.md](reference.md) for the non-Postgres connector payloads, the
full endpoint inventory across the three services, the Neo4j label and
relationship map, and cypher recipes for inspecting the catalog and semantic
layer.
