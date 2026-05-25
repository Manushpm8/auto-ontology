# scripts

Helper scripts for spinning up the GSF stack.

## `setup_env.sh`

Brings up the GSF environment via `docker compose`. Two modes:

| Command | What it starts |
|---|---|
| `bash ./scripts/setup_env.sh` | Full stack — infra (Postgres, pgAdmin, Neo4j) **and** the `gsf` + `gsf-frontend` images (built with `--build`). |
| `bash ./scripts/setup_env.sh --dev` | Infra only (Postgres, pgAdmin, Neo4j). You run `gsf` / `gsf-frontend` locally yourself. |
| `bash ./scripts/setup_env.sh --ds` | Infra **+** `gsf-frontend` in docker. You run `gsf` locally on `:3001`; the containerised frontend reaches it via `host.docker.internal:3001`. |

If a `neo4j` container is already running on the host, it is reused and not restarted.

### Endpoints

| Service | URL |
|---|---|
| Postgres | `localhost:5432` |
| pgAdmin | <http://localhost:5050> |
| Neo4j | <http://localhost:7474> |
| GSF frontend (full stack only) | <http://localhost:3000> |
| GSF backend (full stack only) | <http://localhost:3001> |

### `--dev` workflow

After `./scripts/setup_env.sh --dev` brings the infra up, start the apps locally in two terminals:

```bash
# terminal 1 — Next.js
cd frontend && pnpm install && pnpm dev

# terminal 2 — FastAPI
uv sync
uv run uvicorn gsf.server.main:app --reload --host 127.0.0.1 --port 3001 --app-dir .
```

### `--ds` workflow

`--ds` runs the Next.js frontend in docker but expects you to run the FastAPI backend locally — useful when you're iterating on backend Python code but don't care about rebuilding the frontend.

The frontend image is rebuilt with `PYTHON_API_URL=http://host.docker.internal:3001` baked into its Next.js rewrites, so requests from the browser hit the host's `:3001`.

```bash
bash ./scripts/setup_env.sh --ds

# then, on the host:
uv sync
uv run uvicorn gsf.server.main:app --reload --host 127.0.0.1 --port 3001 --app-dir .
```

> Linux note: `host.docker.internal` works out-of-the-box on Docker Desktop (macOS / Windows). On native Linux Docker you may need to add `--add-host=host.docker.internal:host-gateway` or bind the backend to `0.0.0.0` and use the docker bridge IP.

### Configuration

Service ports and credentials come from `.env` at the repo root (read by `docker compose` automatically). See the root `README.md` and `CLAUDE.md` for the full variable list.

### Stopping

```bash
docker compose down       # stop everything
docker compose down -v    # stop + wipe volumes (Postgres, Neo4j, pgAdmin data)
```
