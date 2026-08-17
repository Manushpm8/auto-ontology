# SPDX-FileCopyrightText: Copyright (c) 2026, NVIDIA CORPORATION & AFFILIATES.
# All rights reserved.
# SPDX-License-Identifier: Apache-2.0

"""Unit tests for slot-weighted majority in sql_selection."""

from __future__ import annotations

import pytest

from gsf.retrieval.text_to_sql.agents import sql_selection as sel


@pytest.fixture(autouse=True)
def _clear_weight_env(monkeypatch):
    monkeypatch.delenv("BIRD_SLOT_VOTE_WEIGHTS", raising=False)
    monkeypatch.delenv("BIRD_SLOT_VOTE_WEIGHTS_JSON", raising=False)
    monkeypatch.setenv("BIRD_NONEMPTY_FIRST", "0")


def test_flat_majority_unchanged_when_weights_off():
    # sig A: indices 0,1  (size 2); sig B: index 2 (size 1) → A wins
    clusters = {("a",): [0, 1], ("b",): [2]}
    assert sel._majority_winner(clusters) == 0
    assert sel._majority_winner(clusters, weights=None) == 0


def test_weighted_majority_can_overturn_flat():
    # Flat: weak slots 7,8,9 (size 3) beat strong 0+3 (size 2).
    # Weighted solo_acc: 0.451+0.447+0.420=1.318 < 0.729+0.720=1.449 → strong wins.
    clusters = {("wrong",): [7, 8, 9], ("right",): [0, 3]}
    flat = sel._majority_winner(clusters, weights=None)
    assert flat == 7
    weighted = sel._majority_winner(clusters, weights=list(sel._SOLO_ACC_WEIGHTS))
    assert weighted == 0


def test_slot_vote_weights_env_solo_acc(monkeypatch):
    monkeypatch.setenv("BIRD_SLOT_VOTE_WEIGHTS", "solo_acc")
    w = sel._slot_vote_weights()
    assert w is not None
    assert abs(w[0] - 0.729) < 1e-9
    assert abs(w[2] - 0.483) < 1e-9


def test_slot_vote_weights_env_voter_q(monkeypatch):
    monkeypatch.setenv("BIRD_SLOT_VOTE_WEIGHTS", "voter_q")
    w = sel._slot_vote_weights()
    assert w is not None
    assert abs(w[2] - 0.670) < 1e-9  # slot 2 kept with decent voterQ


def test_slot_vote_weights_json_override(monkeypatch):
    monkeypatch.setenv("BIRD_SLOT_VOTE_WEIGHTS", "solo_acc")
    monkeypatch.setenv("BIRD_SLOT_VOTE_WEIGHTS_JSON", "[1.0, 0.1, 0.1]")
    w = sel._slot_vote_weights()
    assert w == [1.0, 0.1, 0.1]


def test_tail_index_uses_last_weight():
    w = [0.9, 0.1]
    assert sel._weight_for_index(w, 0) == 0.9
    assert sel._weight_for_index(w, 5) == 0.1
