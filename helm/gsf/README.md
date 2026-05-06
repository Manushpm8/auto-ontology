# gsf Helm chart

Deploys the GSF stack to Kubernetes:

- **backend** — FastAPI / uvicorn service built from `Dockerfile` (repo root)
- **frontend** — Next.js (standalone output) built from `frontend/Dockerfile`
- optional **Ingress** that fronts both apps
- optional **HPA** + **PodDisruptionBudget**

PostgreSQL and Neo4j are **not** packaged with this chart. Run them as managed
services or install separately (e.g. `bitnami/postgresql`, `neo4j/neo4j`) and
point `backend.env.POSTGRES_HOST` / `backend.env.NEO4J_URI` at them.

## TL;DR

```bash
# 1. Build & push images
#    By default the backend image uses the bundled nemo_retriever stub
#    in ./vendor/nemo_retriever (lets the API start and serve /api/health
#    but raises NotImplementedError on real chat/datasource calls).
#    Pass --build-arg NEMO_RETRIEVER_SRC=path/to/real/source to use the
#    real package.
docker build -t <registry>/gsf-backend:0.1.0 .
docker build -t <registry>/gsf-frontend:0.1.0 ./frontend
docker push <registry>/gsf-backend:0.1.0
docker push <registry>/gsf-frontend:0.1.0

# 2. Install
helm upgrade --install gsf ./helm/gsf \
    --namespace gsf --create-namespace \
    --set backend.image.repository=<registry>/gsf-backend \
    --set frontend.image.repository=<registry>/gsf-frontend \
    --set backend.image.tag=0.1.0 \
    --set frontend.image.tag=0.1.0 \
    --set backend.secrets.POSTGRES_PASSWORD=$POSTGRES_PASSWORD \
    --set backend.secrets.NEO4J_PASSWORD=$NEO4J_PASSWORD
```

## Values

See [`values.yaml`](./values.yaml) for the full schema. Highlights:

| Key | Default | Description |
|---|---|---|
| `backend.replicaCount` | `2` | Backend pod count (ignored when `autoscaling.enabled`) |
| `backend.image.repository` | `gsf-backend` | Backend image repo |
| `backend.image.tag` | `""` (= `.Chart.AppVersion`) | Backend image tag |
| `backend.env` | postgres/neo4j connection vars | Rendered into a ConfigMap |
| `backend.secrets` | passwords | Rendered into a Secret (override at install) |
| `backend.existingSecret` | `""` | Use a pre-existing Secret instead |
| `backend.autoscaling.enabled` | `false` | Toggle HPA |
| `frontend.replicaCount` | `2` | Frontend pod count |
| `frontend.image.repository` | `gsf-frontend` | Frontend image repo |
| `frontend.env.PYTHON_API_URL` | `http://gsf-backend:3001` | In-cluster backend URL |
| `ingress.enabled` | `false` | Create a single Ingress for the stack |
| `ingress.routeBackendDirectly` | `false` | Also expose `/api/*` straight to the backend |
| `podDisruptionBudget.enabled` | `false` | Add a PDB per workload |

## Production checklist

- Override `backend.secrets.*` (or set `backend.existingSecret`) — the defaults
  match `.env.example` and must not be used in real deployments.
- Set `image.pullSecrets` if your registry is private.
- Enable `backend.autoscaling` and `frontend.autoscaling` based on load testing.
- Enable `podDisruptionBudget` when running >1 replica per workload.
- Configure `ingress.tls` + cert-manager annotations.

## Lint / dry-run

```bash
helm lint ./helm/gsf
helm template gsf ./helm/gsf --debug | less
```
