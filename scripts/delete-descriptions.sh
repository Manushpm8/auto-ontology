#!/usr/bin/env bash
# Delete table/column descriptions from the Neo4j catalog graph.
#
# Wipes the `description` property on every Table (and its Columns) under the
# given database (default: snowflake_sample_data). Use load-descriptions.sh to
# bring them back from OpenMetadata.
#
# Usage: scripts/delete-descriptions.sh [database-name]
set -euo pipefail

REPO_ROOT="$(cd "$(dirname "$0")/.." && pwd)"
cd "$REPO_ROOT"

DB_NAME="${1:-snowflake_sample_data}"

DB_NAME="$DB_NAME" uv run python - <<'PY'
import os

from gsf.server.env import load_server_env

load_server_env()
from nemo_retriever.tabular_data.neo4j import get_neo4j_conn

db_name = os.environ["DB_NAME"].lower()
res = get_neo4j_conn().query_write(
    """
    MATCH (db:Database)-[:CONTAINS]->(s:Schema)-[:CONTAINS]->(t:Table)
    WHERE toLower(db.name) = $db_name
    OPTIONAL MATCH (t)-[:CONTAINS]->(c:Column)
    SET t.description = NULL, c.description = NULL
    RETURN count(DISTINCT t) AS tables
    """,
    {"db_name": db_name},
)
print("reset tables:", res[0]["tables"])
PY
