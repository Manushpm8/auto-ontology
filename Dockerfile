# syntax=docker/dockerfile:1.7

# ---------------------------------------------------------------------------
# GSF FastAPI backend image
# ---------------------------------------------------------------------------
# Build:   docker build -t gsf-backend:latest .
# Run:     docker run --rm -p 3001:3001 --env-file .env gsf-backend:latest
#
# NOTE: pyproject.toml declares an editable local dependency on `nemo-retriever`
# at ./vendor/nemo-project/nemo_retriever. That package itself depends (via
# editable path) on its sibling packages ./vendor/nemo-project/{api,client}, so
# this image copies the entire nemo-project tri-folder layout into
# /opt/nemo-project/ and leaves the relative paths intact.
#
# To build with the lightweight stub instead (small image, /api/health works
# but real chat/data calls raise NotImplementedError):
#
#   docker build \
#       --build-arg NEMO_RETRIEVER_SRC=vendor/nemo_retriever_stub \
#       --build-arg NEMO_RETRIEVER_DEST=/opt/nemo_retriever .
# ---------------------------------------------------------------------------

ARG PYTHON_VERSION=3.12
ARG UV_VERSION=0.5.11

############################
# Stage 1: builder
############################
FROM python:${PYTHON_VERSION}-slim-bookworm AS builder

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PIP_DISABLE_PIP_VERSION_CHECK=1 \
    UV_LINK_MODE=copy \
    UV_COMPILE_BYTECODE=1 \
    UV_PROJECT_ENVIRONMENT=/opt/venv

RUN apt-get update \
 && apt-get install -y --no-install-recommends \
        build-essential \
        ca-certificates \
        curl \
        git \
        libpq-dev \
 && rm -rf /var/lib/apt/lists/*

# Install uv
COPY --from=ghcr.io/astral-sh/uv:0.5.11 /uv /usr/local/bin/uv

WORKDIR /app

# Bring in the local NeMo-Retriever source so the editable path dep resolves.
# Defaults to vendor/nemo-project (full real source incl. nv-ingest-{api,client}).
# Override NEMO_RETRIEVER_SRC + NEMO_RETRIEVER_DEST to swap to the stub.
ARG NEMO_RETRIEVER_SRC=vendor/nemo-project
ARG NEMO_RETRIEVER_DEST=/opt/nemo-project
COPY ${NEMO_RETRIEVER_SRC}/ ${NEMO_RETRIEVER_DEST}/

# Copy lock + project metadata first to maximise layer caching.
COPY pyproject.toml uv.lock ./
COPY gsf/ ./gsf/

# Patch pyproject.toml for the in-image build:
#   - rewrite the nemo-retriever editable path so it points at the in-image
#     copy of the source (whichever NEMO_RETRIEVER_DEST resolved to)
#   - drop the darwin/arm64-only required-environments marker
#   - drop the darwin-only torch CPU index override; let torch resolve from
#     the default index (PyPI) which has wheels for the build platform
#   - delete uv.lock — the lock was generated against the original local path
#     (and possibly a different dep closure). Re-resolve from scratch.
ARG NEMO_RETRIEVER_DEST
ENV NEMO_RETRIEVER_DEST=${NEMO_RETRIEVER_DEST}
RUN python - <<'PY'
import os, re, pathlib


def patch(path: pathlib.Path) -> None:
    """Strip torch/torchvision/vllm source overrides + custom indexes.

    The vendored NeMo-Retriever pins torch to a CUDA-only PyTorch index that
    has no arm64-linux wheels; gsf's own pyproject also pins torch to a
    darwin-only CPU index. Removing all of these overrides lets uv resolve
    torch from the default index (PyPI), which has wheels for every platform
    we care about for image builds.
    """
    text = path.read_text()
    text = re.sub(r"required-environments\s*=\s*\[[^\]]*\]\s*,?\s*", "", text)
    # Drop torch, torchvision, vllm source overrides (whether list-form or
    # single-table form).
    text = re.sub(
        r"^(torch|torchvision|vllm)\s*=\s*\[[\s\S]*?\]\s*$",
        "",
        text,
        flags=re.MULTILINE,
    )
    text = re.sub(
        r"^(torch|torchvision|vllm)\s*=\s*\{[^\n]*\}\s*$",
        "",
        text,
        flags=re.MULTILINE,
    )
    # Drop any single-line source-table entry that references a custom index
    # (e.g. `nemotron-* = { index = "test-pypi" }`). The matching index
    # block is removed below, so the entry would otherwise fail to parse.
    text = re.sub(
        r'^[A-Za-z0-9_.\-]+\s*=\s*\{[^\n]*\bindex\s*=\s*"[^"]+"[^\n]*\}\s*$',
        "",
        text,
        flags=re.MULTILINE,
    )
    # Drop every [[tool.uv.index]] block (CPU + CUDA torch indexes etc.).
    text = re.sub(
        r"\[\[tool\.uv\.index\]\][^\[]*?(?=(\[|\Z))",
        "",
        text,
        flags=re.DOTALL,
    )
    path.write_text(text)


dest = os.environ["NEMO_RETRIEVER_DEST"].rstrip("/")
gsf_pyproject = pathlib.Path("pyproject.toml")
text = gsf_pyproject.read_text()
# Rewrite both the "full" tri-folder path and the legacy single-folder path.
text = text.replace("vendor/nemo-project/nemo_retriever", f"{dest}/nemo_retriever")
text = text.replace("vendor/nemo_retriever_stub", dest)
gsf_pyproject.write_text(text)

patch(gsf_pyproject)
# Patch the vendored nemo-retriever pyproject too (same torch/index issues).
nr_pyproject = pathlib.Path(f"{dest}/nemo_retriever/pyproject.toml")
if nr_pyproject.exists():
    patch(nr_pyproject)

pathlib.Path("uv.lock").unlink(missing_ok=True)
PY

# Resolve and install the dependency closure into /opt/venv.
RUN --mount=type=cache,target=/root/.cache/uv \
    uv sync --no-dev --no-install-project

############################
# Stage 2: runtime
############################
FROM python:${PYTHON_VERSION}-slim-bookworm AS runtime

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PATH="/opt/venv/bin:${PATH}" \
    PYTHONPATH="/app/gsf" \
    PORT=3001

RUN apt-get update \
 && apt-get install -y --no-install-recommends \
        ca-certificates \
        libpq5 \
        tini \
 && rm -rf /var/lib/apt/lists/* \
 && groupadd --system --gid 1000 gsf \
 && useradd  --system --uid 1000 --gid gsf --home /app --shell /usr/sbin/nologin gsf

WORKDIR /app

ARG NEMO_RETRIEVER_DEST=/opt/nemo-project
COPY --from=builder /opt/venv /opt/venv
COPY --from=builder ${NEMO_RETRIEVER_DEST} ${NEMO_RETRIEVER_DEST}
COPY --chown=gsf:gsf gsf/ ./gsf/
COPY --chown=gsf:gsf pyproject.toml ./

USER gsf

EXPOSE 3001

HEALTHCHECK --interval=30s --timeout=5s --start-period=20s --retries=3 \
    CMD python -c "import urllib.request,sys; sys.exit(0 if urllib.request.urlopen(f'http://127.0.0.1:{__import__(\"os\").environ.get(\"PORT\",\"3001\")}/api/health', timeout=3).status == 200 else 1)"

ENTRYPOINT ["/usr/bin/tini", "--"]
CMD ["sh", "-c", "uvicorn server.main:app --host 0.0.0.0 --port ${PORT} --app-dir /app/gsf"]
