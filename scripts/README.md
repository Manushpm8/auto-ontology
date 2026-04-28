# scripts

Scripts for setting up and managing the local development environment.

## setup_env.sh

Starts Docker infrastructure and (optionally) the full application stack.

### Prerequisites

- [Docker](https://docs.docker.com/get-docker/) running locally
- [pnpm](https://pnpm.io/) and [uv](https://docs.astral.sh/uv/) installed
- `.env` file in the repo root (copy from `.env.example`)

### Usage

```bash
# Full stack — infrastructure + Next.js + Python API
./scripts/setup_env.sh

# Infrastructure only — Postgres, pgAdmin, Neo4j
./scripts/setup_env.sh --dev

# Stop all services
./scripts/setup_env.sh --down

# Stop all services and delete volumes (fresh start)
./scripts/setup_env.sh --reset

# Show running containers
./scripts/setup_env.sh --status

# Tail logs (all services or a specific one)
./scripts/setup_env.sh --logs
./scripts/setup_env.sh --logs postgres
```

### Services

| Service  | URL                      |
| -------- | ------------------------ |
| Postgres | `localhost:5432`         |
| pgAdmin  | http://localhost:5050    |
| Neo4j    | http://localhost:7474    |
| Next.js  | http://localhost:3000    |
| FastAPI  | http://localhost:3001    |

### pgAdmin server registration

After first launch, register the Postgres server in pgAdmin:

1. Open http://localhost:5050 and log in with the credentials from `.env`
2. Right-click **Servers** > **Register** > **Server...**
3. **General** tab: Name = `GSF`
4. **Connection** tab: Host = `postgres`, Port = `5432`, Database / Username / Password from `.env`
