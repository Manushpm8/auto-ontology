# gsf Helm chart

Deploys the GSF stack to Kubernetes:

- **backend** — FastAPI / uvicorn service built from `Dockerfile` (repo root)
- **frontend** — Next.js (standalone output) built from `frontend/Dockerfile`
- optional **Ingress** that fronts both apps
- optional **HPA** + **PodDisruptionBudget**

Postgres and Neo4j are **not** packaged with this chart — they're intentionally
left to the operator. For local testing there's a minimal manifest at
[`examples/postgres-neo4j.yaml`](./examples/postgres-neo4j.yaml). For real
deployments use a managed service (RDS, Aura, etc.) or a hardened community
chart (e.g. `bitnami/postgresql`, `neo4j/neo4j`) and set `backend.env.*` /
`backend.secrets.*` accordingly.

---

## Prerequisites

| Tool | Version | Notes |
|---|---|---|
| Docker | 24+ | Build the images |
| Helm | 3.12+ (4.x works) | `brew install helm` |
| kubectl | 1.25+ | `brew install kubernetes-cli` |
| A Kubernetes cluster | 1.25+ | Local options below |

### Local cluster options

- **Docker Desktop** — Settings → Kubernetes → "Enable Kubernetes" → Apply.
  Easiest because images built on the host daemon are immediately visible to
  the cluster (no `kind load` step). The instructions below assume this.
- **kind** — `brew install kind && kind create cluster --name gsf`. After
  building each image, push it into the cluster with
  `kind load docker-image gsf-backend:0.1.0 --name gsf`.
- **minikube / k3d** — analogous; check your tool's docs for image loading.

---

## Quickstart (local Docker Desktop k8s)

End-to-end from a clean clone — verified working as of the latest commit on
this branch.

### 1. Build the images

```bash
# from the repo root
docker build -t gsf-backend:0.1.0  .
docker build -t gsf-frontend:0.1.0 ./frontend
```

The backend image sources NeMo-Retriever from a BuildKit named context. By
default that context falls back to the lightweight stub committed at
`vendor/nemo_retriever_stub/` — the resulting image starts and serves
`/api/health` but raises `NotImplementedError` on real chat/datasource
calls.

To build with the real NeMo-Retriever source, point a `--build-context` at
any local clone of the upstream repo. Nothing is copied into this repo:

```bash
docker build \
    --build-context nemo=/Users/me/Projects/NeMo-Retriever \
    -t gsf-backend:0.1.0 .
```

In CI, do a regular shallow clone first and pass the path:

```bash
git clone --depth 1 git@github.com:NVIDIA/NeMo-Retriever.git /tmp/nemo
docker build --build-context nemo=/tmp/nemo -t gsf-backend:0.1.0 .
```

The frontend image bakes the backend URL into its Next.js rewrites at build
time (Next.js evaluates `rewrites()` during `next build`, *not* at server
startup, so the runtime env var on the pod is ignored). The default points
at the in-cluster service the chart creates:

```bash
# default — matches the chart's gsf-backend Service:
docker build -t gsf-frontend:0.1.0 ./frontend

# custom backend URL (e.g. when running the frontend image standalone):
docker build \
    --build-arg PYTHON_API_URL=http://localhost:3001 \
    -t gsf-frontend:0.1.0-local ./frontend
```

### 2. Deploy Postgres + Neo4j

```bash
kubectl create namespace gsf
kubectl -n gsf apply -f helm/gsf/examples/postgres-neo4j.yaml
```

Skip this step if you already have managed Postgres / Neo4j; just point the
chart at them via `backend.env.*` in step 3.

### 3. Install the chart

```bash
helm install gsf ./helm/gsf \
    --namespace gsf \
    --set image.pullPolicy=Never \
    --set backend.image.repository=gsf-backend \
    --set backend.image.tag=0.1.0 \
    --set backend.replicaCount=1 \
    --set frontend.image.repository=gsf-frontend \
    --set frontend.image.tag=0.1.0 \
    --set frontend.replicaCount=1 \
    --set backend.env.NEO4J_URI=bolt://neo4j:7687 \
    --set backend.env.POSTGRES_HOST=postgres \
    --set backend.env.POSTGRES_PORT=5432 \
    --set backend.env.POSTGRES_USER=gsf \
    --set backend.env.POSTGRES_DATABASE=gsf \
    --set backend.secrets.NEO4J_PASSWORD=changeme123 \
    --set backend.secrets.POSTGRES_PASSWORD=gsf_dev
```

`image.pullPolicy=Never` tells the kubelet to use the image already on the
local Docker daemon (it would otherwise try to pull `gsf-backend:0.1.0` from
Docker Hub and fail). Drop this flag once you push the images to a real
registry.

### 4. Wait for the rollout and verify

```bash
kubectl -n gsf get pods -w
# wait for all four pods to reach 1/1 Running, then ^C

# Direct backend probe:
kubectl -n gsf port-forward svc/gsf-backend 3001:3001 &
curl http://127.0.0.1:3001/api/health    # → {"status":"ok"}

# Full path through the frontend proxy + open the UI:
kubectl -n gsf port-forward svc/gsf-frontend 3000:3000 &
curl http://127.0.0.1:3000/api/health    # → {"status":"ok"}
open  http://127.0.0.1:3000              # GSF chat UI
```

### 5. Cleanup

```bash
pkill -f "kubectl.*port-forward"
helm -n gsf uninstall gsf
kubectl delete namespace gsf
```

---

## Publishing images to NGC (`nvcr.io`)

The repo ships a `Makefile` at the root that wraps `docker buildx build`
for both images and pushes them multi-arch (`linux/amd64,linux/arm64`)
to NVIDIA's NGC container registry.

One-time setup:

1. Get an NGC API token at <https://ngc.nvidia.com/setup/api-key>.
2. Decide on your `nvcr.io/<org>/<team>` path (whichever NGC org / team
   owns GSF).
3. Authenticate Docker:

   ```bash
   make login NGC_TOKEN=<your NGC API token>
   # equivalent to:
   #   docker login nvcr.io --username '$oauthtoken' --password-stdin
   ```

Publish both images for a release:

```bash
make publish \
    REGISTRY=nvcr.io/<org>/<team> \
    TAG=0.1.0
```

This builds the backend with the real NeMo-Retriever source from
`../NeMo-Retriever` (override with `NEMO=/some/other/path` or
`NEMO=stub` for a stub-only image), and the frontend with the in-cluster
default `PYTHON_API_URL=http://gsf-backend:3001` (override per-build
with `PYTHON_API_URL=...`).

Individual targets are also available — `make publish-backend`,
`make publish-frontend`, `make build` (local single-arch loads), etc.
Run `make help` for the full list.

CI-driven publish (release tags / nightly) is not wired up yet; the
intent is to layer that on top of these targets once the manual flow is
validated.

## Production deployments

For anything beyond local testing:

```bash
# Push images first (see "Publishing images to NGC" above):
make publish REGISTRY=nvcr.io/<org>/<team> TAG=0.1.0

helm upgrade --install gsf ./helm/gsf \
    --namespace gsf --create-namespace \
    --values  prod-values.yaml \
    --set     backend.image.repository=nvcr.io/<org>/<team>/gsf-backend \
    --set     backend.image.tag=0.1.0 \
    --set     frontend.image.repository=nvcr.io/<org>/<team>/gsf-frontend \
    --set     frontend.image.tag=0.1.0 \
    --set     image.pullSecrets[0].name=ngc-imagepull \
    --set     backend.secrets.POSTGRES_PASSWORD="$POSTGRES_PASSWORD" \
    --set     backend.secrets.NEO4J_PASSWORD="$NEO4J_PASSWORD"
```

If your cluster doesn't have a `ngc-imagepull` Secret yet, create one
with the same NGC token:

```bash
kubectl -n gsf create secret docker-registry ngc-imagepull \
    --docker-server=nvcr.io \
    --docker-username='$oauthtoken' \
    --docker-password=<your NGC API token>
```

### Production checklist

- [ ] Override `backend.secrets.*` (or set `backend.existingSecret`) — the
      defaults in `values.yaml` match `.env.example` and must not be used in
      real deployments.
- [ ] Set `image.pullSecrets` if your registry is private.
- [ ] Enable `backend.autoscaling` / `frontend.autoscaling` based on load
      testing.
- [ ] Enable `podDisruptionBudget` when running >1 replica per workload.
- [ ] Enable and configure `ingress.tls` + cert-manager annotations.
- [ ] Point `backend.env.NEO4J_URI` / `POSTGRES_*` at managed services rather
      than the example sidecar manifest.

---

## Values reference

Highlights — see [`values.yaml`](./values.yaml) for the full schema.

| Key | Default | Description |
|---|---|---|
| `backend.replicaCount` | `2` | Backend pod count (ignored when `autoscaling.enabled`) |
| `backend.image.repository` | `gsf-backend` | Backend image repo |
| `backend.image.tag` | `""` (= `.Chart.AppVersion`) | Backend image tag |
| `backend.env` | postgres / neo4j connection vars | Rendered into a ConfigMap |
| `backend.secrets` | passwords | Rendered into a Secret (override at install) |
| `backend.existingSecret` | `""` | Use a pre-existing Secret instead |
| `backend.autoscaling.enabled` | `false` | Toggle HPA |
| `frontend.replicaCount` | `2` | Frontend pod count |
| `frontend.image.repository` | `gsf-frontend` | Frontend image repo |
| `frontend.env.PYTHON_API_URL` | `http://gsf-backend:3001` | **Read at build time** by Next.js (see note below); kept in the ConfigMap for parity |
| `image.pullPolicy` | `IfNotPresent` | Set to `Never` for local Docker Desktop k8s |
| `image.pullSecrets` | `[]` | imagePullSecrets shared by both workloads |
| `ingress.enabled` | `false` | Create a single Ingress for the stack |
| `ingress.routeBackendDirectly` | `false` | Also expose `/api/*` straight to the backend |
| `podDisruptionBudget.enabled` | `false` | Add a PDB per workload |

---

## Troubleshooting

### Backend pods `CrashLoopBackOff` with `KeyError: 'NEO4J_URI'` or DNS errors
The real `nemo_retriever` opens a Neo4j connection at module import. Make
sure step 2 succeeded and `backend.env.NEO4J_URI` resolves to a reachable
Bolt endpoint inside the cluster. `kubectl -n gsf logs deploy/gsf-backend`
will show the exact failure.

### Frontend serves the UI but `/api/*` returns 500 with `ECONNREFUSED 127.0.0.1:3001`
The frontend image was built without the `PYTHON_API_URL` build arg, so the
default fallback (`http://127.0.0.1:3001`) was baked into
`.next/routes-manifest.json`. Rebuild with:

```bash
docker build --build-arg PYTHON_API_URL=http://gsf-backend:3001 -t gsf-frontend:0.1.0 ./frontend
kubectl -n gsf rollout restart deploy gsf-frontend
```

### `helm install` errors with `ImagePullBackOff` for `gsf-backend:0.1.0`
The kubelet is trying to pull the image from Docker Hub. On local clusters
the images aren't published anywhere — pass `--set image.pullPolicy=Never`
(Docker Desktop) or run `kind load docker-image gsf-backend:0.1.0` (kind).

### Backend image build fails with `Distribution not found at: file:///opt/...`
Whatever directory you passed to `--build-context nemo=...` must contain a
`nemo_retriever/pyproject.toml` (so the editable dep resolves). For the
real upstream it's the repo root of NeMo-Retriever; for the bundled stub
the Dockerfile already takes care of it. If you set up your own custom
source, make sure that file exists at the expected path, or just fall back
to the stub by omitting `--build-context nemo=...` entirely:

```bash
docker build -t gsf-backend:0.1.0-stub .
```

### `helm lint` fails with `Chart.yaml file is missing`
Helm v4 doesn't accept `!` negation patterns in `.helmignore` and aborts
chart loading entirely. Don't add lines like `!README.md` to the helmignore.

---

## Lint / dry-run

```bash
helm lint ./helm/gsf
helm template gsf ./helm/gsf --debug | less
helm template gsf ./helm/gsf \
    --set ingress.enabled=true \
    --set backend.autoscaling.enabled=true \
    --set frontend.autoscaling.enabled=true \
    --set podDisruptionBudget.enabled=true \
    | kubectl apply --dry-run=client -f -
```
