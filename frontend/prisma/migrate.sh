#!/bin/sh
# SPDX-FileCopyrightText: Copyright (c) 2024-2026, NVIDIA CORPORATION & AFFILIATES.
# All rights reserved.
# SPDX-License-Identifier: Apache-2.0

# Guarded Prisma schema sync for the `frontend-migrate` Helm hook.
#
# Runs from /app/migrate, where the image ships the Prisma CLI plus
# prisma.config.ts (datasource url) and prisma/schema.prisma.
#
# Why the guards:
#   1. conversation_analytics gained a REQUIRED user_id column.
#      `prisma db push` cannot add a NOT NULL column to a table that already
#      holds rows, so a DB seeded before the column existed makes the push
#      fail. Those pre-feature rows have no user to attribute them to, so we
#      drop them once.
#   2. Better Auth 1.7 rebuilt the MCP OAuth tables. The 1.6
#      `oauthApplication` / `oauthAccessToken.accessToken` shape is not
#      column-compatible, and `db push` without --accept-data-loss refuses
#      the rewrite. MCP clients re-register via DCR, so outstanding grants
#      are dropped once.
#
# Both cleanups are idempotent: they only fire when the old shape is still
# present. They touch only the `frontend` schema, matching the scope `db push`
# operates on.
set -eu

# Both subcommands read the schema path and datasource url from
# prisma.config.ts (shipped alongside this script in /app/migrate). Prisma 7's
# `db execute` has no --schema flag; it takes the datasource from the config.
PRISMA="node node_modules/prisma/build/index.js"

# `db push` does not create the schema it targets, so this has to exist first
# or the very first push fails with "schema \"frontend\" does not exist".
echo "migrate: ensuring the frontend schema exists..."
$PRISMA db execute --stdin <<'SQL'
CREATE SCHEMA IF NOT EXISTS frontend;
SQL

echo "migrate: checking conversation_analytics for the pre-user_id schema..."
$PRISMA db execute --stdin <<'SQL'
DO $$
DECLARE
  stale_rows bigint;
BEGIN
  IF EXISTS (
    SELECT 1 FROM information_schema.tables
    WHERE table_schema = 'frontend' AND table_name = 'conversation_analytics'
  ) AND NOT EXISTS (
    SELECT 1 FROM information_schema.columns
    WHERE table_schema = 'frontend'
      AND table_name = 'conversation_analytics'
      AND column_name = 'user_id'
  ) THEN
    SELECT count(*) INTO stale_rows FROM frontend.conversation_analytics;
    RAISE NOTICE 'conversation_analytics is missing user_id; truncating % pre-feature row(s) so the required column can be added', stale_rows;
    TRUNCATE TABLE frontend.conversation_analytics;
  END IF;
END $$;
SQL

# Better Auth 1.7 rebuilt the MCP OAuth tables: `oauthApplication` became
# `oauthClient`, `oauthAccessToken.accessToken` became `token`, and refresh
# tokens moved to their own table. The shapes are not column-compatible, and
# `db push` without --accept-data-loss will refuse the rewrite if the 1.6
# tables still hold rows. Grants are ephemeral (MCP clients re-register via
# DCR), so dropping the old tables is the cutover. The guard is idempotent:
# it only fires when the 1.6 tables/columns are still present.
echo "migrate: dropping Better Auth 1.6 OAuth tables if they are still present..."
$PRISMA db execute --stdin <<'SQL'
DO $$
BEGIN
  IF EXISTS (
    SELECT 1 FROM information_schema.tables
    WHERE table_schema = 'frontend' AND table_name = 'oauthApplication'
  ) OR EXISTS (
    SELECT 1 FROM information_schema.columns
    WHERE table_schema = 'frontend'
      AND table_name = 'oauthAccessToken'
      AND column_name = 'accessToken'
  ) THEN
    RAISE NOTICE 'dropping 1.6 OAuth tables so Better Auth 1.7 can recreate them';
    DROP TABLE IF EXISTS frontend."oauthConsent" CASCADE;
    DROP TABLE IF EXISTS frontend."oauthAccessToken" CASCADE;
    DROP TABLE IF EXISTS frontend."oauthApplication" CASCADE;
  END IF;
END $$;
SQL

echo "migrate: running prisma db push..."
exec $PRISMA db push
