#!/usr/bin/env bash
set -euo pipefail

REPO_ROOT="$(cd "$(dirname "$0")/.." && pwd)"
cd "$REPO_ROOT"

neo4j_running() {
    docker inspect --format='{{.State.Running}}' neo4j 2>/dev/null | grep -q true
}

resolve_infra_services() {
    local services="postgres pgadmin"
    if neo4j_running; then
        echo "Neo4j is already running — skipping"
    else
        services="$services neo4j"
    fi
    echo "$services"
}

usage() {
    echo "Usage: $0 [flags]"
    echo ""
    echo "Flags:"
    echo "  --dev       Start infrastructure only (Postgres, pgAdmin, Neo4j)"
    echo "  --down      Stop all services"
    echo "  --reset     Stop all services and delete volumes"
    echo "  --logs      Tail logs (optionally: --logs postgres)"
    echo "  --status    Show running containers"
    echo ""
    echo "No flags starts the full stack (infrastructure + Next.js + Python API)"
}

if [[ " $* " =~ \ --help\  ]]; then
    usage
    exit 0
fi

if [[ " $* " =~ \ --reset\  ]]; then
    echo "Stopping all services and removing volumes..."
    docker compose down -v
    echo "Done."
    exit 0
fi

if [[ " $* " =~ \ --down\  ]]; then
    echo "Stopping all services..."
    docker compose down
    echo "Done."
    exit 0
fi

if [[ " $* " =~ \ --status\  ]]; then
    docker compose ps
    exit 0
fi

if [[ " $* " =~ \ --logs\  ]]; then
    shift
    docker compose logs -f "$@"
    exit 0
fi

if [[ " $* " =~ \ --dev\  ]]; then
    SERVICES=$(resolve_infra_services)
    echo "Starting infrastructure: $SERVICES"
    docker compose up -d $SERVICES
    echo ""
    echo "Services started:"
    echo "  Postgres:  localhost:${POSTGRES_PORT:-5432}"
    echo "  pgAdmin:   http://localhost:5050"
    echo "  Neo4j:     http://localhost:7474"
    exit 0
fi

# Default: full stack
SERVICES=$(resolve_infra_services)
echo "Starting infrastructure: $SERVICES"
docker compose up -d $SERVICES

echo "Installing dependencies..."
pnpm install
uv sync --project .

echo ""
echo "Starting full stack..."
pnpm dev &
pnpm dev:api &

echo ""
echo "Services:"
echo "  Postgres:  localhost:${POSTGRES_PORT:-5432}"
echo "  pgAdmin:   http://localhost:5050"
echo "  Neo4j:     http://localhost:7474"
echo "  Next.js:   http://localhost:3000"
echo "  FastAPI:   http://localhost:3001"

wait
