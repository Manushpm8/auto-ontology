# GSF

Generative Semantic Fabric adds the structured-data ontology layer to any partner or NVidia agent harness  interface, like NVIDIA AI-Q Claws, etc

> **Licensing & contributions.** GSF is distributed under the
> [Apache License 2.0](./LICENSE). Third-party open-source components
> bundled, linked, or otherwise used by this project are listed in
> [`THIRD_PARTY_NOTICES.md`](./THIRD_PARTY_NOTICES.md). **This project
> is currently not accepting external contributions.**

<p>
<img src="./docs/assets/arch.png" alt="GSF Architecture" width="800">
</p>

## Key Features

- **Natural-language querying** of structured data — questions are translated to
  SQL and executed against connected databases, powered by NVIDIA NIM.
- **Semantic catalog / ontology layer** over relational sources, stored as a
  graph in Neo4j.
- **Authentication & RBAC** —using [Better Auth](https://github.com/better-auth/better-auth).
- **Web UI** for chat, analysis, data catalog, analytics, and settings.
- **Flexible deployment** — Helm chart for Kubernetes or Docker Compose for a
  local stack.

## Software Components

| Component | Technology | Role | Default port |
|---|---|---|---|
| Frontend | Next.js 16 (React 19, TypeScript, Tailwind CSS 4), Better Auth, Prisma | Web UI, authentication, API gateway | 3000 |
| Backend | FastAPI (Python 3.12+), NeMo-Retriever, LangChain | Chat / NL-to-SQL, catalog, datasource APIs | 3001 |
| Ingestion worker | FastAPI (Python 3.12+), NeMo-Retriever | Ingests tabular data and writes embeddings | 3002 |
| Postgres + pgvector | Relational Database | App metadata and vector store | 5432 |
| Neo4j | Graph Database | Ontology graph | 7474 / 7687 |
| HashiCorp Vault | Optional | Secure storage of connection credentials | — |

### NVIDIA NIM

GSF uses NVIDIA NIM endpoints for inference — either the hosted endpoints on
[build.nvidia.com](https://build.nvidia.com) or self-hosted NIMs:

- **LLM:**
  [nemotron-3-nano-30b-a3b](https://build.nvidia.com/nvidia/nemotron-3-nano-30b-a3b/modelcard)
- **Embeddings:**
  [llama-nemotron-embed-vl-1b-v2](https://build.nvidia.com/nvidia/llama-nemotron-embed-vl-1b-v2)

The endpoints and models are configured via the `BASE_URL`, `MODEL_NAME`,
`EMBED_ENDPOINT`, and `EMBED_MODEL` environment variables and require an
`NVIDIA_API_KEY`.

## Deployment

### Prerequisites

- **Docker** with **Docker Compose v2** (e.g. Docker Desktop on macOS/Windows,
  Docker Engine on Linux) for the local stack. For Kubernetes deployments see
  [`DEPLOYMENT.md`](./DEPLOYMENT.md).
- An **NVIDIA API key** for NVIDIA NIM (chat and ingestion). Get one at
  <https://build.nvidia.com>.
- Connection details for the source database(s) you want to query
  (Databricks, Postgres, Snowflake, or DuckDB).

### Installation

**Kubernetes**

To deploy GSF on a Kubernetes cluster, see [`DEPLOYMENT.md`](./DEPLOYMENT.md).

**Local (Docker Compose)**

1. Clone the repository:

   ```bash
   git clone <repo-url> gsf && cd gsf
   ```

2. Create your environment file (.env) from the template and fill in the values
   (Postgres/Neo4j credentials, `NVIDIA_API_KEY`, `CONNECTION_STRINGS`, etc.).
   See [`.env.example`](./.env.example) for the full list of variables:

   ```bash
   cp .env.example .env
   # edit .env
   ```

   Note:
   - `AUTH_SECRET` and `APP_URL` are **required** — without them every page
     fails with a Better Auth "default secret" error.
   - `POSTGRES_PORT` only changes the **host-side** port mapping (set it if
     5432 is already taken on your machine); containers always reach Postgres
     on the internal network port.

3. Build the images and start the stack:

   ```bash
   docker compose up -d --build
   ```

   This builds the backend (`gsf`) and frontend (`gsf-frontend`) images, brings
   up Postgres, Neo4j, and pgAdmin, runs the one-shot `frontend-migrate` job to
   sync the database schema, and starts the app (backend, ingestion service,
   and frontend).

4. Open the UI at <http://localhost:3000> (the backend API is on `:3001`,
   pgAdmin on `:5050`).

   > **Troubleshooting:** if the UI loads without the left navigation panel
   > (or otherwise looks broken after a restart), your browser is holding a
   > stale session cookie — this happens when `AUTH_SECRET` changes or the
   > database is reset. Clear the site data for `localhost:3000` (or delete
   > the `better-auth.session_token` cookie) and sign in again.

## Connections Management

GSF resolves the source databases it connects to from two sources:

1. **Added through the UI** — connections created from within the app. With this
   option the connection credentials are **stored in plaintext in Neo4j unless
   Vault is configured**:
   - **Without Vault:** the full connection object (including the password) is
     JSON-serialized and stored, unencrypted, on the database's Neo4j node.
   - **With Vault:** the credentials are written to Vault, keyed by the database
     name, and the Neo4j node stores no credentials (the database name is the
     lookup key — there is no separate reference field).

   Vault is enabled only when all of `VAULT_ADDR`, `VAULT_NAMESPACE`,
   `VAULT_ROLE_ID`, and `VAULT_SECRET_ID` are set (`VAULT_AUTH_MOUNT` and
   `VAULT_KV_MOUNT` are optional overrides). It is off by default; a partial
   configuration is ignored with a warning and falls back to plaintext storage.

2. **`CONNECTION_STRINGS` environment variable** — a comma-separated list of
   connection strings supplied at deploy time (e.g. the Helm
   `--set connectionStrings=<CONNECTION-STRINGS>` flag). This is a fallback: it
   is used only when there are no UI-added connections.

## Standalone Semantic Model Files

GSF can import and export its graph-native semantic layer as standalone YAML.
The physical datasource must be ingested before import so terms, columns, and
SQL references can resolve to existing catalog nodes.

```yaml
version: "1.0"
model:
  name: sales
  database: analytics
terms:
  - name: orders
    source:
      database: analytics
      schema: public
      table: orders
    column_attributes:
      - name: order_id
        source_column: order_id
    sql_attributes:
      - name: revenue
        kind: metric
        expressions:
          - dialect: ANSI_SQL
            expression: SUM(orders.amount)
        sql: SELECT SUM(orders.amount) FROM analytics.public.orders AS orders
        table_refs: [orders]
semantic_foreign_keys: []
```

Import a GSF model file:

```bash
uv run python -m gsf.semantic import \
  --database-name analytics \
  --input sales.gsf.yaml
```

Import validates all catalog references and SQL before writing, then
applies all Neo4j changes in one transaction. By default, a previous import of
the same model is replaced and the semantic vector index is refreshed. Use
`--no-replace` or `--skip-embeddings` to change those behaviors.

Neo4j persistence and vector embedding are separate storage operations. If the
post-commit embedding refresh fails, the graph remains imported and the
command reports that embeddings must be retried.

Export a model:

```bash
uv run python -m gsf.semantic export \
  --database-name analytics \
  --model-name sales \
  --output sales.gsf.yaml
```

Omit `--model-name` when the database has zero or one model; if it has multiple
models, choose one explicitly. With no model envelope, GSF exports all semantic
terms for the database. Omit `--output` to write YAML to stdout.

Apache Ossie conversion is intentionally external to GSF. Use the offline
`apache-ossie-gsf` converter to translate between this file format and Apache
Ossie YAML.

Current limitations:

- Only GSF model-file version `1.0` is accepted.
- One semantic model is stored per YAML document.
- Imported model names are global across the GSF graph, including databases.
- Term sources must identify physical tables.
- GSF `SqlAttribute` names are global, so conflicting computed field or metric
  names fail validation.
- Existing compiled Terms are not overwritten; import fails if a term name is
  already owned outside the same model.
- Every SQL attribute must declare `kind: attribute`, `kind: field`, or
  `kind: metric`, plus full SQL and its referenced terms. `field` and `metric`
  preserve Apache Ossie classification when converted.
- A Term represented by multiple physical tables cannot be serialized as one
  native term.

When an expression provides several dialect variants, import selects the
variant matching the active GSF connector and preserves the full dialect list
for export.

The original compilation invocation remains supported:

```bash
uv run python -m gsf.semantic --database-name analytics
```

## Authentication

GSF Supports SSO for authentication.
The Redirect URI should be configured in the IdP as: APP_URL/api/auth/sso/callback

## License

GSF is licensed under the [Apache License, Version 2.0](./LICENSE).
SPDX identifier: `Apache-2.0`.

Each NVIDIA-authored source file in this repository carries an SPDX header
of the form:

```text
SPDX-FileCopyrightText: Copyright (c) 2024-2026, NVIDIA CORPORATION & AFFILIATES.
All rights reserved.
SPDX-License-Identifier: Apache-2.0
```

Third-party open-source components used by GSF are enumerated, with their
upstream licenses and project URLs, in
[`THIRD_PARTY_NOTICES.md`](./THIRD_PARTY_NOTICES.md).

## Contributing

**This project is currently not accepting contributions.** Issues, pull
requests, and patches submitted from outside the GSF maintainer team will
not be reviewed or merged. Security-relevant reports should follow the
process described in [`SECURITY.md`](./SECURITY.md).
