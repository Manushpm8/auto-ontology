#!/usr/bin/env bash
# Render the Snowflake metadata ingestion YAML template with env vars from .env,
# then run `metadata ingest -c ...` inside a one-shot container started from the
# OpenMetadata ingestion image. The container is joined to the OM network so it
# can reach openmetadata-server:8585 directly.
#
# This pulls Snowflake table/column metadata — including descriptions/comments —
# into OpenMetadata, which GSF then overlays onto its catalog graph.
#
# Usage: scripts/run-ingestion.sh metadata <log-path>
set -euo pipefail

KIND="${1:?kind required: metadata}"
EVIDENCE="${2:?log path required}"

cd "$(dirname "$0")/.."

if [[ ! -f .env ]]; then
  echo "FATAL: .env not found" >&2
  exit 2
fi
# shellcheck disable=SC1091
set -a; source .env; set +a

case "$KIND" in
  metadata) TMPL=ingestion/snowflake-metadata.yaml.tmpl; SUBCMD=ingest ;;
  *) echo "Unknown kind: $KIND (only 'metadata' is supported)" >&2; exit 2 ;;
esac

RENDERED="ingestion/.rendered-$KIND.yaml"
mkdir -p ingestion
envsubst < "$TMPL" > "$RENDERED"

IMAGE="docker.getcollate.io/openmetadata/ingestion:1.12.9"
NETWORK="openmetadata-eval_app_net"

# Build a sed program that scrubs the password, the bot JWT, the username,
# and the account locator out of *everything* before it hits the log file.
SCRUB="
  s|${SNOWFLAKE_PASSWORD}|<sf_password>|g;
  s|${OM_TOKEN}|<om_token>|g;
  s|${SNOWFLAKE_USER}|<sf_user>|g;
  s|${SNOWFLAKE_ACCOUNT}|<sf_account>|g;
  s/(password|jwtToken):.*/\\1: <redacted>/;
"

mkdir -p "$(dirname "$EVIDENCE")"
{
  echo "=== run-ingestion.sh kind=$KIND subcmd=$SUBCMD at $(date -u +%FT%TZ) ==="
  echo "--- rendered YAML (secrets scrubbed) ---"
  sed -E "$SCRUB" "$RENDERED"
  echo "--- end YAML ---"
  echo
  echo "=== metadata $SUBCMD output ==="
  set +e
  docker run --rm \
    --network "$NETWORK" \
    -v "$(pwd)/ingestion:/work" \
    --entrypoint metadata \
    "$IMAGE" \
    "$SUBCMD" -c "/work/$(basename "$RENDERED")" 2>&1
  RC=$?
  set -e
  echo "=== exit_code=$RC ==="
  exit $RC
} | sed -E "$SCRUB" | tee "$EVIDENCE"
