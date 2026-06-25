#!/bin/sh
# Restore the baked WWI dump into a dedicated database on the target Postgres.
# Idempotent: creates the database if missing and skips the restore when WWI is
# already present, so it is safe to run on every deploy/sync.
set -eu

: "${POSTGRES_HOST:?POSTGRES_HOST required}"
: "${POSTGRES_USER:?POSTGRES_USER required}"
: "${POSTGRES_PASSWORD:?POSTGRES_PASSWORD required}"
POSTGRES_PORT="${POSTGRES_PORT:-5432}"
# DB used for admin connections (must already exist); the app's operational DB.
ADMIN_DB="${POSTGRES_DATABASE:-postgres}"
# Target database that holds the WWI catalog the chat/text-to-SQL agent queries.
WWI_DATABASE="${WWI_DATABASE:-wwi}"
DUMP="${WWI_DUMP_PATH:-/dump/wwi.dump}"

export PGPASSWORD="$POSTGRES_PASSWORD"
PSQL="psql -v ON_ERROR_STOP=1 -h $POSTGRES_HOST -p $POSTGRES_PORT -U $POSTGRES_USER"

echo ">> waiting for postgres at ${POSTGRES_HOST}:${POSTGRES_PORT}..."
i=0
until pg_isready -h "$POSTGRES_HOST" -p "$POSTGRES_PORT" -U "$POSTGRES_USER" >/dev/null 2>&1; do
  i=$((i + 1))
  [ "$i" -gt 150 ] && {
    echo "postgres not ready after ~5m" >&2
    exit 1
  }
  sleep 2
done

exists=$($PSQL -d "$ADMIN_DB" -tAc "SELECT 1 FROM pg_database WHERE datname='${WWI_DATABASE}'" 2>/dev/null || true)
if [ "$exists" != "1" ]; then
  echo ">> creating database '${WWI_DATABASE}'..."
  $PSQL -d "$ADMIN_DB" -c "CREATE DATABASE \"${WWI_DATABASE}\""
fi

# Sentinel: the WWI schema set includes a 'warehouse' schema once restored.
seeded=$($PSQL -d "$WWI_DATABASE" -tAc "SELECT 1 FROM information_schema.schemata WHERE schema_name='warehouse'" 2>/dev/null || true)
if [ "$seeded" = "1" ]; then
  echo ">> WWI already present in '${WWI_DATABASE}', skipping restore."
  exit 0
fi

echo ">> restoring WWI into '${WWI_DATABASE}' (can take a few minutes)..."
pg_restore --no-owner --no-privileges --no-comments \
  -h "$POSTGRES_HOST" -p "$POSTGRES_PORT" -U "$POSTGRES_USER" \
  -d "$WWI_DATABASE" "$DUMP"
echo ">> WWI restore complete."
