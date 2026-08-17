# SPDX-FileCopyrightText: Copyright (c) 2024-2026, NVIDIA CORPORATION & AFFILIATES.
# All rights reserved.
# SPDX-License-Identifier: Apache-2.0

"""NIM text-embedding config shared by ingest pipelines and the retriever."""

from __future__ import annotations

import logging
import os
import random
import threading
import time
from typing import TYPE_CHECKING

from nemo_retriever.common.params.models import EmbedParams

from gsf.utils.model_config import resolve

if TYPE_CHECKING:
    from nemo_retriever.common.vdb.adt_vdb import VDB

logger = logging.getLogger(__name__)

# Remote NIM embedding endpoint — no local GPU required.
# MUST match the model used at ingest time; a mismatch produces garbage results
# or a dimension error from pgvector. Each field falls back to DEFAULT_MODELS_<field>.
_EMBED_ENDPOINT = resolve("EMBED", "ENDPOINT")
_EMBED_MODEL = resolve("EMBED", "MODEL")
_EMBED_API_KEY = resolve("EMBED", "API_KEY")

# Public NIM /v1/embeddings is flaky under parallel eval (502 Bad Gateway).
# Mirror rerank.py: retry transient failures + optional in-flight cap.
_RETRY_MAX = max(1, int(os.environ.get("EMBED_RETRY_MAX", "6")))
_RETRY_BASE_S = float(os.environ.get("EMBED_RETRY_BASE_S", "1.0"))
_MAX_INFLIGHT = max(1, int(os.environ.get("EMBED_MAX_INFLIGHT", "2")))
_INFLIGHT = threading.BoundedSemaphore(_MAX_INFLIGHT)
_HTTP_PATCHED = False


def _is_transient_embed_error(exc: BaseException) -> bool:
    text = str(exc).lower()
    markers = (
        "502",
        "503",
        "504",
        "429",
        "bad gateway",
        "gateway timeout",
        "service unavailable",
        "too many requests",
        "connection reset",
        "timed out",
        "timeout",
        "temporarily unavailable",
    )
    return any(m in text for m in markers)


def install_embed_http_retries() -> None:
    """Monkeypatch nemo_retriever HTTP embed with retry/backoff for 5xx/429."""
    global _HTTP_PATCHED
    if _HTTP_PATCHED:
        return
    from nemo_retriever.models.inference import main_text_embed as mte

    orig = mte._http_embed_openai_compat

    def _wrapped(prompts, *args, **kwargs):
        last_err: BaseException | None = None
        for attempt in range(1, _RETRY_MAX + 1):
            _INFLIGHT.acquire()
            try:
                result = orig(prompts, *args, **kwargs)
                return result
            except Exception as err:
                last_err = err
                transient = _is_transient_embed_error(err)
                if not transient or attempt >= _RETRY_MAX:
                    raise
                sleep_s = _RETRY_BASE_S * (2 ** (attempt - 1)) + random.uniform(0, 0.5)
                logger.warning(
                    "Embed HTTP transient failure (attempt %d/%d): %s; retry in %.1fs",
                    attempt,
                    _RETRY_MAX,
                    err,
                    sleep_s,
                )
                time.sleep(sleep_s)
            finally:
                _INFLIGHT.release()
        assert last_err is not None
        raise last_err

    mte._http_embed_openai_compat = _wrapped  # type: ignore[assignment]
    _HTTP_PATCHED = True
    logger.info(
        "Installed embed HTTP retries (max=%d, inflight=%d) on %s",
        _RETRY_MAX,
        _MAX_INFLIGHT,
        _EMBED_ENDPOINT,
    )


def get_embed_kwargs() -> dict[str, str]:
    """Keyword args for ``Retriever`` embed configuration."""
    install_embed_http_retries()
    return {
        "model_name": _EMBED_MODEL,
        "embed_invoke_url": _EMBED_ENDPOINT,
        "api_key": _EMBED_API_KEY,
    }


def get_embed_params() -> EmbedParams:
    install_embed_http_retries()
    return EmbedParams(
        embed_invoke_url=_EMBED_ENDPOINT,
        model_name=_EMBED_MODEL,
        api_key=_EMBED_API_KEY,
        embed_modality="text",
    )


def embed_docs_into_vdb(
    docs: list[dict],
    embed_params: "EmbedParams",
    vdb: "VDB",
    database_name: str | None = None,
) -> int:
    """Embed *docs* and upsert them into *vdb*.

    Each doc must have at least ``id``, ``name``, ``label``, and ``text`` keys
    (the shape returned by ``fetch_sql_attribute_docs`` and
    ``fetch_suggested_sql_attribute_docs``).

    Returns the number of rows successfully embedded and ingested.
    Raises ``RuntimeError`` when the embedding call produces zero embedded rows
    so the caller can decide how to handle the failure.
    """
    import time

    import pandas as pd

    from nemo_retriever.models.inference.runtime import embed_text_main_text_embed
    from nemo_retriever.operators.vdb import IngestVdbOperator

    if not docs:
        return 0

    rows = []
    for item in docs:
        node_id = item.get("id")
        path = f"neo4j:{node_id}" if node_id is not None else "neo4j:unknown"
        tabular_fields = {
            "id": node_id,
            "label": item.get("label", ""),
            "name": item.get("name", ""),
            "source_path": path,
            "database_name": database_name,
        }
        extras = {
            key: item[key]
            for key in ("question", "sql", "evidence", "db_id", "masked_question")
            if item.get(key) is not None
        }
        rows.append(
            {
                "text": (item.get("text") or "").strip(),
                "_embed_modality": "text",
                "path": path,
                "page_number": -1,
                "metadata": {
                    **tabular_fields,
                    **extras,
                    "content_metadata": {**tabular_fields, **extras},
                },
            }
        )

    before = time.time()
    embedded = embed_text_main_text_embed(
        pd.DataFrame(rows),
        model_name=embed_params.model_name,
        embed_invoke_url=embed_params.embed_invoke_url,
        api_key=embed_params.api_key,
        embed_modality=embed_params.embed_modality,
    )

    with_embeddings = [
        r
        for r in embedded.to_dict(orient="records")
        if (r.get("metadata") or {}).get("embedding")
    ]
    if not with_embeddings:
        raise RuntimeError(
            f"Embedding step produced 0/{len(embedded)} rows with embeddings; "
            f"check upstream embed errors (often a transient "
            f"{embed_params.embed_invoke_url} 5xx)."
        )

    IngestVdbOperator(vdb=vdb)(with_embeddings)
    logger.info(
        "Embedded %d/%d row(s) via %s in %.2fs.",
        len(with_embeddings),
        len(embedded),
        type(vdb).__name__,
        time.time() - before,
    )
    return len(with_embeddings)


# Install as soon as this module is imported so query-time retrieval
# (Retriever → nemo embed HTTP) gets retries even before get_embed_*.
try:
    if _EMBED_API_KEY:
        install_embed_http_retries()
except Exception:
    logger.debug("Deferred embed HTTP retry install", exc_info=True)
