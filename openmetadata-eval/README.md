<!--
SPDX-FileCopyrightText: Copyright (c) 2024-2026, NVIDIA CORPORATION & AFFILIATES.
All rights reserved.
SPDX-License-Identifier: Apache-2.0
-->

# OpenMetadata → GSF descriptions

A minimal OpenMetadata stack whose only job is to pull **table and column
descriptions out of Snowflake** so GSF can show them wherever it shows
descriptions (the `/data` workspace) and use them for text-to-SQL retrieval.

The flow:

```
Snowflake (table/column comments)
  └─ OpenMetadata metadata ingestion  ← this folder
       └─ GSF ingestion service overlays them onto the Neo4j catalog graph
            └─ shown in /data + embedded into pgvector for text-to-SQL
```

GSF reads OpenMetadata over its REST API via
`gsf.connectors.openmetadata.OpenMetadataConnector` and applies the descriptions
with `gsf.server.datasources.dal.apply_descriptions`. That runs automatically at
the end of each ingestion pass when `OPENMETADATA_HOST` is set, or on demand:

```bash
uv run python -m gsf.ingestion_service.enrich_openmetadata            # apply
uv run python -m gsf.ingestion_service.enrich_openmetadata --dry-run  # preview
```

## Setup

1. **Configure secrets**

   ```bash
   cd openmetadata-eval
   cp .env.example .env
   # fill in SNOWFLAKE_* (read-only creds are fine). OM_HOST / OM_API are preset.
   ```

2. **Start OpenMetadata**

   ```bash
   docker compose up -d
   ./scripts/01-wait-for-server.sh
   ```

3. **Mint the ingestion-bot token**

   ```bash
   ./scripts/02-mint-jwt.sh        # writes OM_TOKEN into ./.env
   ```

4. **Ingest Snowflake metadata (descriptions)**

   ```bash
   mkdir -p logs
   ./scripts/run-ingestion.sh metadata logs/metadata.log
   ```

   Edit `ingestion/snowflake-metadata.yaml.tmpl` to point at your database /
   schemas (it defaults to `TPCH_SF1`).

5. **Point GSF at OpenMetadata**

   In the repo-root `.env`:

   ```bash
   OPENMETADATA_HOST=http://localhost:8585
   OPENMETADATA_TOKEN=<the OM_TOKEN minted in step 3>
   ```

   The token is tied to your own local OM instance — generate your own, don't
   reuse a teammate's. Then run a GSF ingestion pass; descriptions appear in
   `/data` on the matching tables and columns.

## Notes

- `apply_descriptions` matches nodes by case-insensitive
  database/schema/table/column **name** and, by default, only fills in
  descriptions that are missing — hand-edited descriptions in GSF are preserved
  (pass `--overwrite` to replace them).
- Everything here is local/eval only. OpenMetadata's quickstart uses default
  demo signing keys; use real keys and rotated tokens for anything beyond dev.
- `.env`, rendered ingestion YAMLs, logs, and the OM data volume are gitignored
  because they contain Snowflake credentials and the bot JWT.
