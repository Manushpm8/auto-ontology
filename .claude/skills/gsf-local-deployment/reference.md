# Local GSF deployment — reference

Detail behind [SKILL.md](SKILL.md). Read the sections you need.

## Endpoint inventory

Three services. The frontend proxies `/api/**` to the backend and enforces auth;
the backend and ingestion service are unauthenticated on localhost. Prefer
`:3000` with a token when a call would be authorised in production, and the
direct ports when debugging.

### Connections — `:3000/api` (or `:3001/api`)

| Call | Notes |
|---|---|
| `GET /api/connections` | Resolved connections, credential-free. `count: 0` with a populated catalog is the classic broken state. |
| `GET /api/connections/source` | `true` when `CONNECTION_STRINGS` is set in the env. Unset locally. |
| `POST /api/connections/test` | Non-mutating. Body `{"connection": {...}}`. Also 422s if the database is already connected. |
| `POST /api/connections` | 201. Same body. Stores the JSON on the `Database` node and **auto-triggers ingestion**. |
| `DELETE /api/connections/{database_name}` | Detaches the connection. Leaves the catalog in place. |

### Semantic compilation — `:3000/api`

| Call | Notes |
|---|---|
| `GET /api/semantic-compilation/status` | `{"calculated": bool}` — true when any `Term` exists. |
| `POST /api/semantic-compilation/trigger` | 202. Scheduler pass. |
| `POST /api/semantic-compilation/reset` | 202. Wipe and rebuild. |

### Catalog — `:3000/api`

| Call | Notes |
|---|---|
| `GET /api/datasources/dbs` | Catalog databases. |
| `GET /api/schemas/{db_id}` | |
| `GET /api/tables/{schema_id}` | |
| `GET /api/columns/{table_id}` | |
| `GET /api/health` | On `:3001`. Probes Neo4j **and** Postgres; 503 if either is down. |

`POST /api/sql-attributes` exists; there is **no GET** — a GET returns 405.

### Ingestion service — `:3002` (no `/api` prefix)

| Call | Notes |
|---|---|
| `GET /health` | Liveness only. Checks no dependencies. |
| `POST /ingest` | 202. Body is the connection object. Rarely needed — saving a connection already ingests. |
| `POST /ingest/delete?database_name=X` | 202. Removes catalog nodes, semantic nodes and pgvector rows for X. Required param. |
| `POST /semantic/compile` | 202. Ensures the scheduler is running, then compiles. |
| `POST /semantic/reset?database_name=X` | 202. Deletes X's semantic layer and recompiles. Omit the param to reset **every** database. |
| `POST /semantic/stop` | 202. Aborts at the next database boundary; the in-flight database finishes first. |

Both `:3000/api/semantic-compilation/trigger` and `:3002/semantic/compile` start
a pass. The ingestion service owns the scheduler, so `:3002` is the more direct
route when debugging.

## Connection payloads by type

Built by `gsf/connectors/connection_string_factory.py`, dispatching on `type`
(lower-cased). Missing required fields raise `Connection is missing required
field: '<key>'`. Every payload is wrapped as `{"connection": {...}}`.

### Postgres — `postgresql` or `postgres`

```json
{"type":"postgresql","host":"postgres","port":"5432",
 "user":"gsf","password":"…","database":"wwi"}
```

Required: `host`, `user`, `password`, `database`. `port` defaults to `5432`.

### MySQL — `mysql`

Same fields; `port` defaults to `3306`.

### Snowflake — `snowflake`

Required: `account`, `warehouse`, `user`, `database`, plus **either** `password`
**or** `private_key`. Key-pair auth is the only option on accounts that enforce
MFA, since those reject passwords for PERSON users and forbid them on SERVICE
users. A PEM in `private_key` is base64-encoded automatically (raw PEM newlines
and `+`/`/` would be mangled in a query string); `private_key_passphrase` is
optional. No `host`/`port`.

### Databricks — `databricks`

Required: `host` (scheme stripped), `http_path`, `database` (the catalog), and a
token via `password`. `access_token_override` takes precedence when present — an
SSO-exchanged token, so the query runs with the caller's privileges rather than
the stored PAT; the resulting URL is tagged `auth=sso` and the connector logs
which credential ran each statement.

### HeavyDB — `heavydb`

Required: `host`, `user`, `password`, `database`. `port` defaults to `6274`,
`protocol` to `binary`.

## Neo4j map

Labels in a healthy single-database deployment:

| Label | Meaning |
|---|---|
| `Database` | A catalog database. **Also carries the connection** in its `connection` property. `Labels.DB` == `"Database"`. |
| `Schema`, `Table`, `Column` | Physical catalog, linked by `CONTAINS`. |
| `Term` | Business glossary entry — the semantic layer. |
| `ColumnAttribute` | Term → physical column mapping. |
| `SqlAttribute`, `Sql` | Reviewed SQL expressions / calculations. |
| `CustomAnalysis` | Saved analyses. |

Relationships, with direction — **the semantic edges point *into* `Term`**, so
traversing from a term to its data runs against the arrows:

| Edge | Direction |
|---|---|
| `CONTAINS` | `Database` → `Schema` → `Table` → `Column` |
| `HAS_ATTRIBUTE` | `Column` → `ColumnAttribute` |
| `PROPERTY_OF` | `ColumnAttribute` → `Term`, and `SqlAttribute` → `Term` |
| `HAS_SQL` | `SqlAttribute` → `Sql` |
| `SQL` | `Sql` → `Column`, and `Sql` → `Table` |
| `REPRESENTS` | `Table` → `Term` (one term per table after a default compile) |
| `SEMANTIC_FK` | `Column` → `ColumnAttribute` |
| `FOREIGN_KEY` | `Column` → `Column` |

Avoid unbounded `[*]` traversals from `Term`; they run the wrong way and return
nothing useful. Name the edges.

`Db` may appear in `db.labels()` with zero nodes — Neo4j keeps label tokens after
the last node carrying them is deleted. Don't infer anything from its presence.
There is **no `Connection` label**.

### Cypher recipes

```cypher
// Connected vs merely catalogued
MATCH (d:Database) RETURN d.name AS db, d.connection IS NOT NULL AS connected;

// Catalog shape
MATCH (d:Database)-[:CONTAINS]->(s:Schema)
OPTIONAL MATCH (s)-[:CONTAINS]->(t:Table)
RETURN d.name AS db, s.name AS schema, count(t) AS tables ORDER BY schema;

// Node census — quick health read
MATCH (n) UNWIND labels(n) AS l RETURN l, count(*) AS n ORDER BY n DESC;

// Semantic layer size
MATCH (t:Term) RETURN count(t);

// A term and the physical columns behind it
MATCH (c:Column)-[:HAS_ATTRIBUTE]->(:ColumnAttribute)-[:PROPERTY_OF]->(t:Term)
RETURN t.name AS term, collect(DISTINCT c.name)[..4] AS columns ORDER BY term;

// Which table each term was compiled from — the fastest way to spot junk terms
MATCH (tb:Table)-[:REPRESENTS]->(t:Term) RETURN t.name AS term, tb.name AS table ORDER BY term;

// Terms per source schema. A count under `public` on a Postgres connection is
// usually extension views compiled as business terms.
MATCH (s:Schema)-[:CONTAINS]->(:Table)-[:REPRESENTS]->(t:Term)
RETURN s.name AS schema, count(t) AS terms ORDER BY terms DESC;

// Name the junk so it can be reviewed or deleted in the UI
MATCH (s:Schema {name:'public'})-[:CONTAINS]->(tb:Table)-[:REPRESENTS]->(t:Term)
RETURN t.name AS term, tb.name AS table;

// Terms carrying reviewed SQL calculations
MATCH (sa:SqlAttribute)-[:HAS_SQL]->(s:Sql), (sa)-[:PROPERTY_OF]->(t:Term)
RETURN t.name AS term, count(s) AS calcs ORDER BY calcs DESC;

// Foreign keys GSF knows about (its join paths)
MATCH ()-[r:FOREIGN_KEY]->() RETURN count(r);

// Actual edge map of this deployment — run when the shape surprises you
MATCH (a)-[r]->(b)
RETURN labels(a)[0] AS from, type(r) AS rel, labels(b)[0] AS to, count(*) AS n
ORDER BY n DESC;
```

## Reading ingestion and compilation logs

```bash
docker logs gsf-ingestion-service --since 10m 2>&1 | tail -40
```

Signals worth knowing:

| Line | Means |
|---|---|
| `schemas_parser: Started/Finished parsing schema X` | Catalog crawl progressing. |
| `write_to_graph: Added schema X to db` | Catalog written to Neo4j. |
| `write_to_graph: Adding FKs / Adding PKs` | Join paths being recorded. |
| `PostgresVDB.write_to_index: inserted N rows` | Embeddings landed in pgvector. |
| `ingest: Tabular ingest result: N rows written to pgvector` | Ingest finished. |
| `gsf.dal.reset: _delete_semantic_nodes / _delete_database_nodes … removed N` | A delete took effect. |
| `nim.probe: embed endpoint … responded 404/400` | Remote NIM unavailable; local CPU embedder used. Benign. |
| `visit_enter: [X] column profiling query failed — skipping` | One table couldn't be profiled. Benign for `pg_stat_statements*`; investigate for real tables. |

## Full rebuild from a broken state

When the catalog, connections and semantic layer disagree and it isn't worth
untangling:

1. `GET /api/connections` and `MATCH (d:Database) RETURN d.name` — inventory what
   GSF thinks it has.
2. `POST :3002/ingest/delete?database_name=X` for every stale database.
3. Confirm the census is empty of `Database`/`Table`/`Term` nodes.
4. `POST /api/connections/test`, then `POST /api/connections` for the real
   database. Ingestion runs itself.
5. Wait for the catalog to appear, then `POST :3002/semantic/compile`.
6. Poll term count and `semantic-compilation/status` until both settle.
7. `check_readiness` → `ready: true`, then verify one real question's SQL against
   `psql`.

Deleting a database removes the terms compiled over it, so step 2 makes step 5
mandatory.
