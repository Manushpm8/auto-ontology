# SPDX-FileCopyrightText: Copyright (c) 2026, NVIDIA CORPORATION & AFFILIATES.
# All rights reserved.
# SPDX-License-Identifier: Apache-2.0

"""The rerank circuit breaker.
Reranking is optional — callers fall back to embedding order — but its retry runs
inside an exclusive cross-process lock, so a hung endpoint serialises every worker
behind a full timeout each instead of merely degrading quality.
"""

from __future__ import annotations

import pytest

from gsf.utils import rerank as rr


@pytest.fixture(autouse=True)
def _reset(monkeypatch):
    """Fresh breaker per test; no file lock, no inter-call sleep."""
    monkeypatch.setattr(rr, "_consecutive_failures", 0, raising=False)
    monkeypatch.setattr(rr, "_CIRCUIT_FAILS", 3, raising=False)
    monkeypatch.setattr(rr, "_LOCK_PATH", "", raising=False)
    monkeypatch.setattr(rr, "_MIN_INTERVAL_S", 0.0, raising=False)
    monkeypatch.setenv("NVIDIA_API_KEY", "test-key")


def _always_timeout(calls: list):
    def _post(*args, **kwargs):
        calls.append(1)
        raise TimeoutError("read timed out")

    return _post


def test_breaker_stops_calling_a_dead_endpoint(monkeypatch):
    calls: list = []
    monkeypatch.setattr(rr.requests, "post", _always_timeout(calls))
    for _ in range(10):
        assert rr.rerank_passages("q", ["a", "b"]) == []
    # Three failures trip it; the remaining seven never reach the network.
    assert len(calls) == 3


def test_success_resets_the_breaker(monkeypatch):
    calls: list = []

    class _Resp:
        status_code = 200

        def raise_for_status(self):
            return None

        def json(self):
            return {"rankings": [{"index": 0, "logit": 1.0}]}

    state = {"fail": 2}

    def _post(*args, **kwargs):
        calls.append(1)
        if state["fail"] > 0:
            state["fail"] -= 1
            raise TimeoutError("read timed out")
        return _Resp()

    monkeypatch.setattr(rr.requests, "post", _post)
    assert rr.rerank_passages("q", ["a"]) == []
    assert rr.rerank_passages("q", ["a"]) == []
    assert rr.rerank_passages("q", ["a"]) == [(0, 1.0)]
    # Two failures then a success: the breaker must not be one failure from tripping.
    for _ in range(5):
        rr.rerank_passages("q", ["a"])
    assert len(calls) == 8


def test_breaker_can_be_disabled(monkeypatch):
    calls: list = []
    monkeypatch.setattr(rr, "_CIRCUIT_FAILS", 0, raising=False)
    monkeypatch.setattr(rr.requests, "post", _always_timeout(calls))
    for _ in range(6):
        rr.rerank_passages("q", ["a"])
    assert len(calls) == 6


def test_breaker_short_circuits_before_taking_the_lock(monkeypatch):
    """A breaker that still queues for the lock would save nothing."""
    calls: list = []
    monkeypatch.setattr(rr.requests, "post", _always_timeout(calls))
    for _ in range(3):
        rr.rerank_passages("q", ["a"])

    entered = []

    def _boom():
        entered.append(1)
        raise AssertionError("lock acquired while the circuit was open")

    monkeypatch.setattr(rr, "_cross_process_slot", _boom)
    assert rr.rerank_passages("q", ["a"]) == []
    assert not entered
