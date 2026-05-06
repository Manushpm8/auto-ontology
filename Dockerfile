# syntax=docker/dockerfile:1.7

# ---------------------------------------------------------------------------
# GSF FastAPI backend image
# ---------------------------------------------------------------------------
# Build:   docker build -t gsf-backend:latest .
# Run:     docker run --rm -p 3001:3001 --env-file .env gsf-backend:latest
#
# NOTE: pyproject.toml declares an editable local dependency on `nemo-retriever`
# (../../nemo-project/NeMo-Retriever/nemo_retriever). That path is not available
# inside the build context. Provide it through ONE of the following options:
#
#   1. Set NEMO_RETRIEVER_SRC to a path inside the build context (relative to
#      the repo root) that contains the nemo_retriever package, e.g.
#         docker build --build-arg NEMO_RETRIEVER_SRC=vendor/nemo_retriever .
#   2. Replace the source in pyproject.toml with a private index / wheel.
#
# By default the build will COPY the directory referenced by NEMO_RETRIEVER_SRC
# into /opt/nemo_retriever before running `uv sync`.
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

# Bring in the local nemo_retriever source so the editable path dep resolves.
# Defaults to ./vendor/nemo_retriever; override with --build-arg if needed.
ARG NEMO_RETRIEVER_SRC=vendor/nemo_retriever
COPY ${NEMO_RETRIEVER_SRC}/ /opt/nemo_retriever/

# Copy lock + project metadata first to maximise layer caching.
COPY pyproject.toml uv.lock README.md ./
COPY gsf/ ./gsf/

# Patch pyproject.toml so uv resolves nemo-retriever from the in-image path
# and drop the macOS-only required-environments constraint for Linux builds.
RUN python - <<'PY'
import re, pathlib
p = pathlib.Path("pyproject.toml")
text = p.read_text()
text = text.replace(
    '../../nemo-project/NeMo-Retriever/nemo_retriever',
    '/opt/nemo_retriever',
)
text = re.sub(
    r"required-environments\s*=\s*\[[^\]]*\]\s*,?\s*",
    "",
    text,
)
p.write_text(text)
PY

# Resolve and install the dependency closure into /opt/venv.
RUN --mount=type=cache,target=/root/.cache/uv \
    uv sync --frozen --no-dev --no-install-project

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

COPY --from=builder /opt/venv /opt/venv
COPY --from=builder /opt/nemo_retriever /opt/nemo_retriever
COPY --chown=gsf:gsf gsf/ ./gsf/
COPY --chown=gsf:gsf pyproject.toml ./

USER gsf

EXPOSE 3001

HEALTHCHECK --interval=30s --timeout=5s --start-period=20s --retries=3 \
    CMD python -c "import urllib.request,sys; sys.exit(0 if urllib.request.urlopen(f'http://127.0.0.1:{__import__(\"os\").environ.get(\"PORT\",\"3001\")}/api/health', timeout=3).status == 200 else 1)"

ENTRYPOINT ["/usr/bin/tini", "--"]
CMD ["sh", "-c", "uvicorn server.main:app --host 0.0.0.0 --port ${PORT} --app-dir /app/gsf"]
