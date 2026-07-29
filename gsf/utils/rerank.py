# SPDX-FileCopyrightText: Copyright (c) 2026, NVIDIA CORPORATION & AFFILIATES.
# All rights reserved.
# SPDX-License-Identifier: Apache-2.0

"""NVIDIA NIM text-reranking client (``/v1/retrieval/nvidia/reranking``).

Used by few-shot retrieval and entity-candidate retrieval to reorder a larger
embedding recall set by cross-encoder relevance before the final top-k is kept.

Public NIM rate-limits aggressively under parallel eval (few-shot + per-entity
candidate reranks × multi-shard workers). This module therefore:
* caps in-flight rerank POSTs process-wide (``RERANK_MAX_INFLIGHT``), and
* retries HTTP 429 with exponential backoff.
"""

from __future__ import annotations

import logging
import os
import random
import tempfile
import threading
import time
from typing import Sequence

import requests

logger = logging.getLogger(__name__)

# Public NVIDIA AI API hosts the mistral-4b reranker under this path (not
# ``/v1/ranking`` on integrate.api.nvidia.com, which 404s for this account).
_DEFAULT_ENDPOINT = "https://ai.api.nvidia.com/v1/retrieval/nvidia/reranking"
_DEFAULT_MODEL = "nvidia/rerank-qa-mistral-4b"

_MAX_INFLIGHT = max(1, int(os.environ.get("RERANK_MAX_INFLIGHT", "1")))
_INFLIGHT = threading.BoundedSemaphore(_MAX_INFLIGHT)
_RETRY_MAX = max(1, int(os.environ.get("RERANK_RETRY_MAX", "5")))
_RETRY_BASE_S = float(os.environ.get("RERANK_RETRY_BASE_S", "1.0"))
# Cross-process gate so parallel eval shards (separate PIDs) don't all hit
# NIM at once. Override with RERANK_LOCK_PATH; empty disables the file lock.
_LOCK_PATH = os.environ.get(
    "RERANK_LOCK_PATH", os.path.join(tempfile.gettempdir(), "gsf-rerank.lock")
)
_MIN_INTERVAL_S = float(os.environ.get("RERANK_MIN_INTERVAL_S", "0.35"))
_last_call_mono = 0.0
_last_call_lock = threading.Lock()


def _cross_process_slot():
    """Context manager: exclusive file lock + min spacing between POSTs."""
    import contextlib

    @contextlib.contextmanager
    def _cm():
        global _last_call_mono
        fh = None
        if _LOCK_PATH:
            try:
                os.makedirs(os.path.dirname(_LOCK_PATH) or ".", exist_ok=True)
                import fcntl

                fh = open(_LOCK_PATH, "a+")
                fcntl.flock(fh.fileno(), fcntl.LOCK_EX)
            except Exception:
                if fh is not None:
                    try:
                        fh.close()
                    except Exception:
                        pass
                    fh = None
        try:
            with _last_call_lock:
                now = time.monotonic()
                wait = _MIN_INTERVAL_S - (now - _last_call_mono)
            if wait > 0:
                time.sleep(wait)
            yield
            with _last_call_lock:
                _last_call_mono = time.monotonic()
        finally:
            if fh is not None:
                try:
                    import fcntl

                    fcntl.flock(fh.fileno(), fcntl.LOCK_UN)
                except Exception:
                    pass
                try:
                    fh.close()
                except Exception:
                    pass

    return _cm()


def _rerank_endpoint() -> str:
    return os.environ.get("RERANK_ENDPOINT", _DEFAULT_ENDPOINT).rstrip("/")


def _rerank_model() -> str:
    return os.environ.get("RERANK_MODEL", _DEFAULT_MODEL)


def _rerank_api_key() -> str:
    # Prefer a dedicated key, then the embedding key (same public NVIDIA account),
    # then NVIDIA_API_KEY. The agent LLM ``sk-`` proxy key is NOT valid here.
    return (
        os.environ.get("RERANK_API_KEY", "")
        or os.environ.get("EMBED_API_KEY", "")
        or os.environ.get("NVIDIA_API_KEY", "")
    )


def _timeout_s() -> float:
    try:
        return float(os.environ.get("RERANK_TIMEOUT_S", "30"))
    except (TypeError, ValueError):
        return 30.0


def rerank_enabled() -> bool:
    """True when few-shot reranking is on and a key is available."""
    flag = os.environ.get("BIRD_FEW_SHOT_RERANK", "1").strip().lower()
    if flag in {"0", "false", "no", "off"}:
        return False
    return bool(_rerank_api_key())


def rerank_passages(
    query: str,
    passages: Sequence[str],
    *,
    top_n: int | None = None,
) -> list[tuple[int, float]]:
    """Rerank *passages* for *query*; return ``[(original_index, logit), ...]``.

    Results are sorted by descending logit (most relevant first). When *top_n*
    is set, only the first *top_n* entries are returned. On failure returns
    ``[]`` so callers can fall back to the embedding order.
    """
    if not query.strip() or not passages:
        return []
    api_key = _rerank_api_key()
    if not api_key:
        logger.warning("rerank_passages: no RERANK/EMBED/NVIDIA API key — skipping")
        return []

    endpoint = _rerank_endpoint()
    model = _rerank_model()
    payload = {
        "model": model,
        "query": {"text": query},
        "passages": [{"text": p} for p in passages],
        "truncate": "END",
    }
    headers = {
        "Authorization": f"Bearer {api_key}",
        "Content-Type": "application/json",
        "Accept": "application/json",
    }

    body: dict | None = None

    with _INFLIGHT:
        with _cross_process_slot():
            for attempt in range(1, _RETRY_MAX + 1):
                try:
                    resp = requests.post(
                        endpoint,
                        json=payload,
                        headers=headers,
                        timeout=_timeout_s(),
                    )
                    if resp.status_code == 429:
                        retry_after = resp.headers.get("Retry-After")
                        try:
                            wait = (
                                float(retry_after)
                                if retry_after
                                else _RETRY_BASE_S * (2 ** (attempt - 1))
                            )
                        except (TypeError, ValueError):
                            wait = _RETRY_BASE_S * (2 ** (attempt - 1))
                        wait = min(wait, 30.0) + random.uniform(0, 0.5)
                        logger.warning(
                            "rerank_passages: 429 (attempt %d/%d), sleeping %.1fs",
                            attempt,
                            _RETRY_MAX,
                            wait,
                        )
                        time.sleep(wait)
                        continue
                    resp.raise_for_status()
                    body = resp.json()
                    break
                except Exception:
                    logger.warning(
                        "rerank_passages: NIM ranking call failed (%s / %s) attempt %d/%d",
                        endpoint,
                        model,
                        attempt,
                        _RETRY_MAX,
                        exc_info=True,
                    )
                    break

    if body is None:
        return []

    rankings = body.get("rankings") or body.get("ranking") or []
    scored: list[tuple[int, float]] = []
    for item in rankings:
        try:
            idx = int(item["index"])
            logit = float(item.get("logit", item.get("score", 0.0)))
        except (KeyError, TypeError, ValueError):
            continue
        if 0 <= idx < len(passages):
            scored.append((idx, logit))

    # Defensive: if the API returned unsorted / partial indices, sort by logit.
    scored.sort(key=lambda t: t[1], reverse=True)
    if top_n is not None:
        scored = scored[: max(0, top_n)]
    return scored
