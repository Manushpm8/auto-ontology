## Running locally

The **frontend** (`frontend/`) and the **Python API** (`gsf/`) must both be running.

### One-time setup

```bash
cp .env.example .env   # then edit credentials as needed
pnpm install
uv sync
```

### Start development

Full stack (infrastructure + Next.js + FastAPI):

```bash
./scripts/setup_env.sh
```

Or infrastructure only (Postgres, pgAdmin, Neo4j):

```bash
./scripts/setup_env.sh --dev
```

Then start the app manually:

```bash
pnpm dev        # Next.js on port 3000
pnpm dev:api    # FastAPI on port 3001
```

Open **http://localhost:3000**.

See [scripts/README.md](scripts/README.md) for all available flags.

### Optional

- Override the API URL: set **`PYTHON_API_URL`** (used by Next rewrites and server-side API calls).

## Container & Kubernetes deployment

This repo ships Dockerfiles and a Helm chart for running the stack in
Kubernetes:

- `Dockerfile` — backend (FastAPI / uvicorn). Sources NeMo-Retriever from a
  BuildKit named context, defaulting to a small committed stub.
- `frontend/Dockerfile` — frontend (Next.js standalone)
- `helm/gsf/` — Helm chart with deployments, services, optional Ingress,
  HPA, PDB and a sample Postgres+Neo4j manifest
