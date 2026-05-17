# GSF

Generative semantic fabric.

> **Licensing & contributions.** GSF is distributed under the
> [Apache License 2.0](./LICENSE). Third-party open-source components
> bundled, linked, or otherwise used by this project are listed in
> [`THIRD_PARTY_NOTICES.md`](./THIRD_PARTY_NOTICES.md). **This project
> is currently not accepting external contributions.**

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

End-to-end build, install and verification steps live in
[`helm/gsf/README.md`](helm/gsf/README.md). TL;DR for a local Docker Desktop
Kubernetes cluster:

```bash
# Stub backend (small, /api/health works, real chat/data calls are no-ops):
docker build -t gsf-backend:0.1.0 .

# Real backend (point at any local NeMo-Retriever clone):
docker build --build-context nemo=/path/to/NeMo-Retriever -t gsf-backend:0.1.0 .

docker build -t gsf-frontend:0.1.0 ./frontend
kubectl create namespace gsf
kubectl -n gsf apply -f helm/gsf/examples/postgres-neo4j.yaml
helm install gsf ./helm/gsf -n gsf \
    --set image.pullPolicy=Never \
    --set backend.image.tag=0.1.0 \
    --set frontend.image.tag=0.1.0
kubectl -n gsf port-forward svc/gsf-frontend 3000:3000
```
## License

GSF is licensed under the [Apache License, Version 2.0](./LICENSE).
SPDX identifier: `Apache-2.0`.

Each NVIDIA-authored source file in this repository carries an SPDX header
of the form:

```text
SPDX-FileCopyrightText: Copyright (c) 2024-2026, NVIDIA CORPORATION & AFFILIATES.
All rights reserved.
SPDX-License-Identifier: Apache-2.0
```

Third-party open-source components used by GSF are enumerated, with their
upstream licenses and project URLs, in
[`THIRD_PARTY_NOTICES.md`](./THIRD_PARTY_NOTICES.md).

## Contributing

**This project is currently not accepting contributions.** Issues, pull
requests, and patches submitted from outside the GSF maintainer team will
not be reviewed or merged. Security-relevant reports should follow the
process described in [`SECURITY.md`](./SECURITY.md).
