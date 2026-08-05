"""Tests for the verify/revise second reasoning pass."""

from __future__ import annotations

import json

import pytest

from gsf.retrieval.text_to_sql import verify_revise


class _QR:
    def __init__(self, rows=None, error=None):
        self.error = error
        self.result = [json.dumps(rows)] if rows is not None else None
        self.sliced = False


class _Cand:
    def __init__(self, sql):
        self.sql_code = sql
        self.thought = ""

    def model_copy(self, update):
        c = _Cand(update.get("sql_code", self.sql_code))
        c.thought = update.get("thought", self.thought)
        return c


class _Rev:
    def __init__(self, sql, verdict="WRONG", diagnosis="d", alternative=""):
        self.sql = sql
        self.verdict = verdict
        self.diagnosis = diagnosis
        self.alternative = alternative


def _patch_llm(monkeypatch, revision):
    """Route the structured call the module makes to a canned revision."""
    monkeypatch.setattr(
        "gsf.utils.llm_invoke.safe_invoke_with_structured_output",
        lambda *a, **k: revision,
    )


def test_disabled_by_default(monkeypatch):
    monkeypatch.delenv("BIRD_VERIFY_REVISE", raising=False)
    assert verify_revise.enabled() is False
    assert (
        verify_revise.revise_pool(
            question="q",
            candidates=[_Cand("SELECT 1")],
            query_responses=[_QR([{"a": 1}])],
            schema_block="s",
            llm=object(),
            run_sql=lambda s, c: _QR([{"a": 1}]),
            connector=None,
        )
        == []
    )


def test_appends_a_differing_revision(monkeypatch):
    monkeypatch.setenv("BIRD_VERIFY_REVISE", "1")
    _patch_llm(monkeypatch, _Rev("SELECT name FROM t"))
    out = verify_revise.revise_pool(
        question="which name",
        candidates=[_Cand("SELECT id FROM t")],
        query_responses=[_QR([{"id": 7}])],
        schema_block="t(id, name)",
        llm=object(),
        run_sql=lambda s, c: _QR([{"name": "x"}]),
        connector=None,
    )
    assert len(out) == 1
    assert out[0].sql_code == "SELECT name FROM t"


def test_identical_revision_is_not_appended(monkeypatch):
    """A CORRECT verdict repeats the draft; that must not duplicate the pool."""
    monkeypatch.setenv("BIRD_VERIFY_REVISE", "1")
    _patch_llm(monkeypatch, _Rev("SELECT id FROM t", verdict="CORRECT"))
    out = verify_revise.revise_pool(
        question="q",
        candidates=[_Cand("SELECT id FROM t")],
        query_responses=[_QR([{"id": 7}])],
        schema_block="s",
        llm=object(),
        run_sql=lambda s, c: _QR([{"id": 7}]),
        connector=None,
    )
    assert out == []


def test_revision_that_fails_to_execute_is_dropped(monkeypatch):
    monkeypatch.setenv("BIRD_VERIFY_REVISE", "1")
    _patch_llm(monkeypatch, _Rev("SELECT nope FROM t"))
    out = verify_revise.revise_pool(
        question="q",
        candidates=[_Cand("SELECT id FROM t")],
        query_responses=[_QR([{"id": 7}])],
        schema_block="s",
        llm=object(),
        run_sql=lambda s, c: _QR(error="no such column: nope"),
        connector=None,
    )
    assert out == []


def test_llm_failure_does_not_propagate(monkeypatch):
    """This is an enhancement pass; it must never take generation down."""
    monkeypatch.setenv("BIRD_VERIFY_REVISE", "1")

    def boom(*a, **k):
        raise RuntimeError("gateway down")

    monkeypatch.setattr(
        "gsf.utils.llm_invoke.safe_invoke_with_structured_output", boom
    )
    out = verify_revise.revise_pool(
        question="q",
        candidates=[_Cand("SELECT id FROM t")],
        query_responses=[_QR([{"id": 7}])],
        schema_block="s",
        llm=object(),
        run_sql=lambda s, c: _QR([{"id": 7}]),
        connector=None,
    )
    assert out == []


def test_markdown_fence_is_stripped(monkeypatch):
    monkeypatch.setenv("BIRD_VERIFY_REVISE", "1")
    _patch_llm(monkeypatch, _Rev("```sql\nSELECT name FROM t\n```"))
    out = verify_revise.revise_pool(
        question="q",
        candidates=[_Cand("SELECT id FROM t")],
        query_responses=[_QR([{"id": 7}])],
        schema_block="s",
        llm=object(),
        run_sql=lambda s, c: _QR([{"name": "x"}]),
        connector=None,
    )
    assert len(out) == 1
    assert out[0].sql_code == "SELECT name FROM t"


def test_max_candidates_is_respected(monkeypatch):
    monkeypatch.setenv("BIRD_VERIFY_REVISE", "1")
    monkeypatch.setenv("BIRD_VERIFY_REVISE_MAX", "1")
    calls = {"n": 0}

    def counting(*a, **k):
        calls["n"] += 1
        return _Rev(f"SELECT c{calls['n']} FROM t")

    monkeypatch.setattr(
        "gsf.utils.llm_invoke.safe_invoke_with_structured_output", counting
    )
    verify_revise.revise_pool(
        question="q",
        candidates=[_Cand("SELECT a FROM t"), _Cand("SELECT b FROM t")],
        query_responses=[_QR([{"a": 1}]), _QR([{"b": 2}])],
        schema_block="s",
        llm=object(),
        run_sql=lambda s, c: _QR([{"x": 1}]),
        connector=None,
    )
    assert calls["n"] == 1


@pytest.mark.parametrize(
    "rows,expected",
    [([], "EMPTY"), (None, "no result payload")],
)
def test_preview_flags_degenerate_results(rows, expected):
    text = verify_revise._preview(_QR(rows), 10)
    assert expected in text


def test_preview_reports_error():
    assert "FAILED" in verify_revise._preview(_QR(error="syntax"), 10)


def test_arbiter_returns_a_novel_query(monkeypatch):
    _patch_llm(monkeypatch, _Rev("SELECT name FROM t JOIN u ON t.id = u.id"))
    out = verify_revise.arbitrate(
        question="which name",
        candidates=[_Cand("SELECT id FROM t"), _Cand("SELECT name FROM t")],
        query_responses=[_QR([{"id": 1}]), _QR([{"name": "a"}])],
        schema_block="t(id,name) u(id)",
        llm=object(),
        run_sql=lambda s, c: _QR([{"name": "a"}]),
        connector=None,
    )
    assert [c.sql_code for c in out] == ["SELECT name FROM t JOIN u ON t.id = u.id"]


def test_arbiter_declines_when_it_just_copies_an_attempt(monkeypatch):
    """Copying an existing attempt adds nothing to the pool, so it is dropped."""
    _patch_llm(monkeypatch, _Rev("SELECT name FROM t"))
    out = verify_revise.arbitrate(
        question="q",
        candidates=[_Cand("SELECT id FROM t"), _Cand("SELECT name FROM t")],
        query_responses=[_QR([{"id": 1}]), _QR([{"name": "a"}])],
        schema_block="s",
        llm=object(),
        run_sql=lambda s, c: _QR([{"name": "a"}]),
        connector=None,
    )
    assert out == []


def test_arbiter_survives_an_llm_failure(monkeypatch):
    def boom(*a, **k):
        raise RuntimeError("down")

    monkeypatch.setattr(
        "gsf.utils.llm_invoke.safe_invoke_with_structured_output", boom
    )
    assert (
        verify_revise.arbitrate(
            question="q",
            candidates=[_Cand("SELECT 1")],
            query_responses=[_QR([{"a": 1}])],
            schema_block="s",
            llm=object(),
            run_sql=lambda s, c: _QR([{"a": 1}]),
            connector=None,
        )
        == []
    )


def test_alternative_reading_is_appended_alongside_the_correction(monkeypatch):
    """The second reading is the point: it is a free extra shot at the oracle."""
    monkeypatch.setenv("BIRD_VERIFY_REVISE", "1")
    _patch_llm(
        monkeypatch,
        _Rev("SELECT name FROM t", alternative="SELECT DISTINCT name FROM t"),
    )
    out = verify_revise.revise_pool(
        question="q",
        candidates=[_Cand("SELECT id FROM t")],
        query_responses=[_QR([{"id": 7}])],
        schema_block="s",
        llm=object(),
        run_sql=lambda s, c: _QR([{"name": "x"}]),
        connector=None,
    )
    assert [c.sql_code for c in out] == [
        "SELECT name FROM t",
        "SELECT DISTINCT name FROM t",
    ]


def test_alternative_equal_to_the_correction_is_not_duplicated(monkeypatch):
    monkeypatch.setenv("BIRD_VERIFY_REVISE", "1")
    _patch_llm(
        monkeypatch, _Rev("SELECT name FROM t", alternative="SELECT name FROM t")
    )
    out = verify_revise.revise_pool(
        question="q",
        candidates=[_Cand("SELECT id FROM t")],
        query_responses=[_QR([{"id": 7}])],
        schema_block="s",
        llm=object(),
        run_sql=lambda s, c: _QR([{"name": "x"}]),
        connector=None,
    )
    assert len(out) == 1


def test_confirmed_draft_still_yields_its_alternative(monkeypatch):
    """A CORRECT verdict used to waste the call; now it still adds a reading."""
    monkeypatch.setenv("BIRD_VERIFY_REVISE", "1")
    _patch_llm(
        monkeypatch,
        _Rev(
            "SELECT id FROM t",
            verdict="CORRECT",
            alternative="SELECT id FROM t WHERE x IS NOT NULL",
        ),
    )
    out = verify_revise.revise_pool(
        question="q",
        candidates=[_Cand("SELECT id FROM t")],
        query_responses=[_QR([{"id": 7}])],
        schema_block="s",
        llm=object(),
        run_sql=lambda s, c: _QR([{"id": 7}]),
        connector=None,
    )
    assert [c.sql_code for c in out] == ["SELECT id FROM t WHERE x IS NOT NULL"]


class _Gate:
    def __init__(self, verdict="WRONG", confidence="HIGH", reason="r"):
        self.verdict = verdict
        self.confidence = confidence
        self.reason = reason


def _patch_gate(monkeypatch, gate):
    monkeypatch.setattr(
        "gsf.utils.llm_invoke.safe_invoke_with_structured_output",
        lambda *a, **k: gate,
    )


def test_wrongness_gate_disabled_keeps_winner(monkeypatch):
    monkeypatch.delenv("BIRD_WRONGNESS_GATE", raising=False)
    idx, stats = verify_revise.maybe_wrongness_switch(
        question="q",
        winner_idx=0,
        candidates=[_Cand("SELECT 1"), _Cand("SELECT 2")],
        query_responses=[_QR([{"a": 1}]), _QR([{"a": 2}])],
        signatures=[(("1",),), (("2",),)],
        clusters={(("1",),): [0], (("2",),): [1]},
        llm=object(),
    )
    assert idx == 0
    assert stats["triggered"] is False


def test_wrongness_gate_empty_switches_without_llm(monkeypatch):
    monkeypatch.setenv("BIRD_WRONGNESS_GATE", "1")
    idx, stats = verify_revise.maybe_wrongness_switch(
        question="q",
        winner_idx=0,
        candidates=[_Cand("SELECT 1"), _Cand("SELECT 2")],
        query_responses=[_QR([]), _QR([{"a": 2}])],
        signatures=[(), (("2",),)],
        clusters={(): [0], (("2",),): [1]},
        llm=object(),
    )
    assert idx == 1
    assert stats["applied"] is True
    assert stats["reason"] == "auto_empty"


def test_wrongness_gate_wrong_with_margin_switches(monkeypatch):
    monkeypatch.setenv("BIRD_WRONGNESS_GATE", "1")
    monkeypatch.setenv("BIRD_WRONGNESS_MARGIN", "2")
    monkeypatch.delenv("BIRD_WRONGNESS_MODEL", raising=False)
    monkeypatch.delenv("ENTITY_EXTRACTION_MODEL", raising=False)
    monkeypatch.delenv("JUDGE_MODEL_NAME", raising=False)
    _patch_gate(monkeypatch, _Gate("WRONG", "HIGH", "bad type"))
    # winner cluster size 1, other size 3 → margin 2
    sig_w = (("w",),)
    sig_o = (("o",),)
    idx, stats = verify_revise.maybe_wrongness_switch(
        question="q",
        winner_idx=0,
        candidates=[_Cand(f"s{i}") for i in range(4)],
        query_responses=[_QR([{"a": 1}])] * 4,
        signatures=[sig_w, sig_o, sig_o, sig_o],
        clusters={sig_w: [0], sig_o: [1, 2, 3]},
        llm=object(),
    )
    assert idx == 1
    assert stats["applied"] is True
    assert stats["reason"] == "wrong_margin"
    assert stats["margin"] == 2


def test_wrongness_gate_wrong_low_margin_keeps(monkeypatch):
    monkeypatch.setenv("BIRD_WRONGNESS_GATE", "1")
    monkeypatch.setenv("BIRD_WRONGNESS_MARGIN", "2")
    monkeypatch.delenv("BIRD_WRONGNESS_MODEL", raising=False)
    monkeypatch.delenv("ENTITY_EXTRACTION_MODEL", raising=False)
    monkeypatch.delenv("JUDGE_MODEL_NAME", raising=False)
    _patch_gate(monkeypatch, _Gate("WRONG", "HIGH", "maybe"))
    sig_w = (("w",),)
    sig_o = (("o",),)
    idx, stats = verify_revise.maybe_wrongness_switch(
        question="q",
        winner_idx=0,
        candidates=[_Cand("a"), _Cand("b")],
        query_responses=[_QR([{"a": 1}]), _QR([{"a": 2}])],
        signatures=[sig_w, sig_o],
        clusters={sig_w: [0], sig_o: [1]},
        llm=object(),
    )
    assert idx == 0
    assert stats["applied"] is False
    assert "margin" in stats["reason"]


def test_wrongness_gate_correct_keeps(monkeypatch):
    monkeypatch.setenv("BIRD_WRONGNESS_GATE", "1")
    monkeypatch.delenv("BIRD_WRONGNESS_MODEL", raising=False)
    monkeypatch.delenv("ENTITY_EXTRACTION_MODEL", raising=False)
    monkeypatch.delenv("JUDGE_MODEL_NAME", raising=False)
    _patch_gate(monkeypatch, _Gate("CORRECT", "HIGH", "ok"))
    sig_w = (("w",),)
    sig_o = (("o",),)
    idx, stats = verify_revise.maybe_wrongness_switch(
        question="q",
        winner_idx=0,
        candidates=[_Cand("a"), _Cand("b"), _Cand("c"), _Cand("d")],
        query_responses=[_QR([{"a": 1}])] * 4,
        signatures=[sig_w, sig_o, sig_o, sig_o],
        clusters={sig_w: [0], sig_o: [1, 2, 3]},
        llm=object(),
    )
    assert idx == 0
    assert stats["reason"] == "kept_correct"


def test_fail_alone_drops_empty(monkeypatch):
    monkeypatch.setenv("BIRD_VERIFY_REVISE_FAIL_ALONE", "1")
    ok, reason = verify_revise._should_append_revision(
        probe=_QR([]),
        draft_qr=_QR([{"a": 1}]),
        plurality_sig=(("1",),),
    )
    assert ok is False
    assert reason == "empty_or_error"


def test_fail_alone_drops_same_as_draft(monkeypatch):
    monkeypatch.setenv("BIRD_VERIFY_REVISE_FAIL_ALONE", "1")
    rows = [{"a": 1}]
    ok, reason = verify_revise._should_append_revision(
        probe=_QR(rows),
        draft_qr=_QR(rows),
        plurality_sig=None,
    )
    assert ok is False
    assert reason == "same_as_draft"


def test_fail_alone_drops_same_as_plurality(monkeypatch):
    monkeypatch.setenv("BIRD_VERIFY_REVISE_FAIL_ALONE", "1")
    probe = _QR([{"a": 2}])
    draft = _QR([{"a": 1}])
    plur = verify_revise._result_signature_local(probe)
    ok, reason = verify_revise._should_append_revision(
        probe=probe, draft_qr=draft, plurality_sig=plur
    )
    assert ok is False
    assert reason == "same_as_plurality"


def test_fail_alone_keeps_divergent(monkeypatch):
    monkeypatch.setenv("BIRD_VERIFY_REVISE_FAIL_ALONE", "1")
    ok, reason = verify_revise._should_append_revision(
        probe=_QR([{"a": 9}]),
        draft_qr=_QR([{"a": 1}]),
        plurality_sig=verify_revise._result_signature_local(_QR([{"a": 1}])),
    )
    assert ok is True
    assert reason == "ok"


def test_fail_alone_can_be_disabled(monkeypatch):
    monkeypatch.setenv("BIRD_VERIFY_REVISE_FAIL_ALONE", "0")
    ok, reason = verify_revise._should_append_revision(
        probe=_QR([]),
        draft_qr=_QR([{"a": 1}]),
        plurality_sig=None,
    )
    # empty still blocked by caller for error, but helper with empty sig:
    # when disabled, empty_or_error only if probe.error — empty list is empty sig
    # With fail_alone off, _should_append returns True unless exec error.
    assert ok is True


def test_revise_targets_unanimous_smart():
    qrs = [_QR([{"a": 1}])] * 4
    assert verify_revise.revise_targets(qrs, 4, mode="smart") == [
        (0, "unanimous_suspect")
    ]
    assert verify_revise.revise_targets(qrs, 4, mode="disagree") == []


def test_revise_targets_broken_and_disagree():
    qrs = [_QR([]), _QR([{"a": 1}]), _QR([{"a": 2}]), _QR([{"a": 1}])]
    got = dict(verify_revise.revise_targets(qrs, 4, mode="smart"))
    assert got[0] == "broken"
    assert got[2] == "disagree"
