# SPDX-FileCopyrightText: Copyright (c) 2026, NVIDIA CORPORATION & AFFILIATES.
# All rights reserved.
# SPDX-License-Identifier: Apache-2.0

"""NIM reranking config and client shared by the rerank flow.

``get_rerank_kwargs`` mirrors :mod:`gsf.utils.embedding`: a single place that
reads the endpoint, model, and API key from the environment and hands back the
keyword args ``nemo_retriever.operators.rerank.rerank_hits`` expects.

``rerank_passages`` is a separate, direct client for NVIDIA NIM text-reranking
(``/v1/retrieval/nvidia/reranking``), used by few-shot retrieval and
entity-candidate retrieval to reorder a larger embedding recall set by
cross-encoder relevance before the final top-k is kept. Public NIM
rate-limits aggressively under parallel eval (few-shot + per-entity candidate
reranks × multi-shard workers). This module therefore:
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

from gsf.utils.model_config import resolve

logger = logging.getLogger(__name__)

# Hosted NeMo reranking endpoint — no local GPU required. Override RERANK_ENDPOINT
# to point at a self-hosted vLLM/NIM ranking server. Each field falls back to
# DEFAULT_MODELS_<field> when unset.
_RERANK_ENDPOINT = resolve("RERANK", "ENDPOINT")
_RERANK_MODEL = resolve("RERANK", "MODEL")
_RERANK_API_KEY = resolve("RERANK", "API_KEY")


def get_rerank_kwargs() -> dict[str, str]:
    """Keyword args for ``rerank_hits`` (remote NeMo reranking endpoint)."""
    return {
        "rerank_invoke_url": _RERANK_ENDPOINT,
        "model_name": _RERANK_MODEL,
        "api_key": _RERANK_API_KEY,
    }


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

# Circuit breaker. Reranking is an optional improvement — callers already fall back
# to embedding order on ``[]`` — but the retry sits inside an exclusive
# cross-process lock, so a hung endpoint does not merely fail, it serialises every
# worker behind a full timeout each. Measured on a parallel BIRD run with the
# endpoint blackholed: 60 of 63 calls failed, 1812s of a 31-minute wall clock was
# spent holding that lock, mean queue 125s, and not one question finished. Once a
# host has failed this many times in a row it is down, and continuing to ask costs
# the whole run while buying nothing. Set RERANK_CIRCUIT_FAILS=0 to disable.
_CIRCUIT_FAILS = max(0, int(os.environ.get("RERANK_CIRCUIT_FAILS", "3")))
_consecutive_failures = 0
_circuit_lock = threading.Lock()


def _rerank_enabled() -> bool:
    """Whether reranking is switched on at all.
    Read per call rather than at import so an A/B can hold reranking constant
    across arms. The v15 EXPLORE run reranked almost nowhere — the breaker had
    tripped behind a blackholed endpoint — so a later arm on a healthy endpoint
    would differ from it by reranking as well as by the variable under test.
    Off yields ``[]``, the same fallback-to-embedding-order path a failure takes.
    """
    return os.environ.get("RERANK_ENABLED", "1").strip().lower() not in {
        "0",
        "false",
        "no",
    }


def _circuit_open() -> bool:
    """Whether reranking has been abandoned for the rest of this process."""
    if not _CIRCUIT_FAILS:
        return False
    with _circuit_lock:
        return _consecutive_failures >= _CIRCUIT_FAILS


def _record_outcome(ok: bool) -> None:
    global _consecutive_failures
    if not _CIRCUIT_FAILS:
        return
    with _circuit_lock:
        if ok:
            _consecutive_failures = 0
            return
        _consecutive_failures += 1
        if _consecutive_failures == _CIRCUIT_FAILS:
            logger.warning(
                "rerank_passages: %d consecutive failures — disabling reranking for "
                "this process and falling back to embedding order. Endpoint: %s",
                _consecutive_failures,
                _rerank_endpoint(),
            )


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
    # _RERANK_ENDPOINT already resolved RERANK_ENDPOINT (with the DEFAULT_MODELS_*
    # and built-in fallbacks in gsf.utils.model_config), so both reranking paths
    # share one source of truth for the endpoint.
    return _RERANK_ENDPOINT.rstrip("/")


def _rerank_model() -> str:
    return _RERANK_MODEL


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
    if not _rerank_enabled():
        return []
    # Checked before the lock is taken: the point is to stop queueing behind a
    # dead endpoint, and a breaker that still waits for the lock saves nothing.
    if _circuit_open():
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

    _record_outcome(body is not None)

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
