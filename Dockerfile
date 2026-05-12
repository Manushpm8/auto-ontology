# syntax=docker/dockerfile:1.7

# ---------------------------------------------------------------------------
# GSF FastAPI backend image
# ---------------------------------------------------------------------------
# Build (default — uses the committed nemo-retriever stub; image starts and
# serves /api/health but real chat/datasource calls raise NotImplementedError):
#
#   docker build -t gsf-backend:latest .
#
# Build with the real NeMo-Retriever source — point a BuildKit named context
# at any local clone of the upstream repo (no copy into this repo):
#
#   docker build \
#       --build-context nemo=/path/to/NeMo-Retriever \
#       -t gsf-backend:latest .
#
# Run:
#
#   docker run --rm -p 3001:3001 --env-file .env gsf-backend:latest
#
# Implementation notes:
#   - The `nemo` stage below is overridden by `--build-context nemo=...`. Its
#     contents (whether stub or real source) are copied into /opt/nemo-project/
#     in the builder. The editable nemo-retriever path in pyproject.toml is
#     rewritten to point at /opt/nemo-project/nemo_retriever.
#   - pyproject.toml's [tool.uv] override-dependencies block (security pins
#     for torch / nltk / pillow) is preserved verbatim. Only darwin-only
#     markers and torch index sources are stripped for the linux build.
# ---------------------------------------------------------------------------

ARG PYTHON_VERSION=3.12
ARG UV_VERSION=0.5.11

############################
# Stage 0: nemo source
############################
# Default: bake the committed stub. Overridden when caller passes
# `--build-context nemo=/path/to/NeMo-Retriever` on `docker build`.
FROM scratch AS nemo
COPY vendor/nemo_retriever_stub/ /

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

# Bring in NeMo-Retriever (stub by default, real source via --build-context).
# Both layouts put the editable dep target at /opt/nemo-project/nemo_retriever.
COPY --from=nemo / /opt/nemo-project/

# Copy lock + project metadata first to maximise layer caching.
COPY pyproject.toml uv.lock ./
COPY gsf/ ./gsf/

# Patch pyproject.toml for the in-image build:
#   - rewrite the editable nemo-retriever path so it points at the in-image
#     copy (handles both the current external sibling path and the older
#     vendor/ paths from earlier on this branch)
#   - drop the darwin/arm64-only [tool.uv] required-environments marker
#   - drop the darwin-only torch CPU index source overrides + the matching
#     [[tool.uv.index]] block — let torch resolve from PyPI on linux
#   - PRESERVE [tool.uv] override-dependencies (security pins for torch /
#     nltk / pillow); they apply to every platform
#   - delete uv.lock — it was generated against the original local path
#     and may not match the override-dependencies; re-resolve from scratch
RUN python - <<'PY'
import re
import pathlib

NEMO_DEST = "/opt/nemo-project/nemo_retriever"


def patch(path: pathlib.Path) -> None:
    """Strip darwin-only markers + torch/vllm source overrides + custom indexes.

    Leaves [tool.uv] override-dependencies and every other dependency pin
    untouched. Those override blocks ARE the security work and must survive.
    """
    text = path.read_text()
    # Drop the darwin/arm64-only required-environments marker.
    text = re.sub(
        r"required-environments\s*=\s*\[[^\]]*\]\s*,?\s*",
        "",
        text,
    )
    # Drop torch / torchvision / vllm source overrides (list-form).
    text = re.sub(
        r"^(torch|torchvision|vllm)\s*=\s*\[[\s\S]*?\]\s*$",
        "",
        text,
        flags=re.MULTILINE,
    )
    # Drop torch / torchvision / vllm source overrides (single-line table).
    text = re.sub(
        r"^(torch|torchvision|vllm)\s*=\s*\{[^\n]*\}\s*$",
        "",
        text,
        flags=re.MULTILINE,
    )
    # Drop any other single-line source-table entry that references a
    # custom index (the matching [[tool.uv.index]] block is removed below).
    text = re.sub(
        r'^[A-Za-z0-9_.\-]+\s*=\s*\{[^\n]*\bindex\s*=\s*"[^"]+"[^\n]*\}\s*$',
        "",
        text,
        flags=re.MULTILINE,
    )
    # Drop every [[tool.uv.index]] block.
    text = re.sub(
        r"\[\[tool\.uv\.index\]\][^\[]*?(?=(\[|\Z))",
        "",
        text,
        flags=re.DOTALL,
    )
    path.write_text(text)


gsf_pyproject = pathlib.Path("pyproject.toml")
text = gsf_pyproject.read_text()
# Rewrite every known shape of the nemo-retriever source path to the
# in-image location. Add new lines here if the path ever moves again.
for legacy in (
    "../../nemo-project/NeMo-Retriever/nemo_retriever",
    "vendor/nemo-project/nemo_retriever",
    "vendor/nemo_retriever_stub/nemo_retriever",
    "vendor/nemo_retriever_stub",
):
    text = text.replace(legacy, NEMO_DEST)
gsf_pyproject.write_text(text)

patch(gsf_pyproject)
nr_pyproject = pathlib.Path(f"{NEMO_DEST}/pyproject.toml")
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

COPY --from=builder /opt/venv /opt/venv
COPY --from=builder /opt/nemo-project /opt/nemo-project
COPY --chown=gsf:gsf gsf/ ./gsf/
COPY --chown=gsf:gsf pyproject.toml ./

USER gsf

EXPOSE 3001

HEALTHCHECK --interval=30s --timeout=5s --start-period=20s --retries=3 \
    CMD python -c "import urllib.request,sys; sys.exit(0 if urllib.request.urlopen(f'http://127.0.0.1:{__import__(\"os\").environ.get(\"PORT\",\"3001\")}/api/health', timeout=3).status == 200 else 1)"

ENTRYPOINT ["/usr/bin/tini", "--"]
CMD ["sh", "-c", "uvicorn server.main:app --host 0.0.0.0 --port ${PORT} --app-dir /app/gsf"]
