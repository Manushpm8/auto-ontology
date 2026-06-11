#!/usr/bin/env bash
# Load table/column descriptions into the Neo4j catalog graph by pulling them
# from OpenMetadata (the inverse of delete-descriptions.sh).
#
# Requires OPENMETADATA_HOST / OPENMETADATA_TOKEN in .env and a reachable
# OpenMetadata server. Extra args are passed through to the underlying module
# (e.g. --overwrite, --dry-run, --no-embeddings).
#
# Usage: scripts/load-descriptions.sh [--overwrite] [--dry-run] [--no-embeddings]
set -euo pipefail

REPO_ROOT="$(cd "$(dirname "$0")/.." && pwd)"
cd "$REPO_ROOT"

uv run python -m gsf.ingestion_service.enrich_openmetadata "$@"
