"""The oracle pruner must keep every gold column and stay off unless asked."""

from __future__ import annotations

import pytest

from gsf.retrieval.text_to_sql.oracle_prune import (
    oracle_prune_enabled,
    prune_tables_to_gold,
)


def _table(name: str, cols: list[str]) -> dict:
    return {
        "name": name,
        "columns": [{"name": c, "data_type": "TEXT"} for c in cols],
    }


def _names(tables: list[dict], idx: int = 0) -> list[str]:
    return [c["name"] for c in tables[idx]["columns"]]


def test_disabled_unless_env_set(monkeypatch):
    monkeypatch.delenv("BIRD_ORACLE_PRUNE_COLS", raising=False)
    assert oracle_prune_enabled() is False
    monkeypatch.setenv("BIRD_ORACLE_PRUNE_COLS", "5")
    assert oracle_prune_enabled() is True


def test_non_numeric_env_does_not_enable(monkeypatch):
    """A typo must not silently contaminate a run."""
    monkeypatch.setenv("BIRD_ORACLE_PRUNE_COLS", "yes")
    assert oracle_prune_enabled() is False


def test_every_gold_column_survives():
    """The whole design rests on this: pruning removes noise, never information."""
    t = _table("schools", ["Charter", "FundingType", "City", "MailCity", "DOC"])
    out = prune_tables_to_gold(
        [t], "SELECT Charter FROM schools WHERE City = 'X'", keep_distractors=0
    )
    assert _names(out) == ["Charter", "City"]


def test_distractors_are_added_up_to_the_budget():
    t = _table("schools", ["Charter", "FundingType", "City", "MailCity", "DOC"])
    out = prune_tables_to_gold([t], "SELECT Charter FROM schools", keep_distractors=2)
    kept = _names(out)
    assert "Charter" in kept
    assert len(kept) == 3


def test_budget_larger_than_table_keeps_everything():
    t = _table("t", ["a", "b"])
    out = prune_tables_to_gold([t], "SELECT a FROM t", keep_distractors=99)
    assert sorted(_names(out)) == ["a", "b"]


def test_quoted_column_with_spaces_is_matched():
    """BIRD gold quotes names like `Charter School (Y/N)`; those must be kept."""
    t = _table("frpm", ["Charter School (Y/N)", "Low Grade", "High Grade"])
    gold = "SELECT `Charter School (Y/N)` FROM frpm"
    out = prune_tables_to_gold([t], gold, keep_distractors=0)
    assert _names(out) == ["Charter School (Y/N)"]


def test_word_boundary_prevents_substring_matches():
    """``id`` must not be considered gold just because ``CustomerID`` appears."""
    t = _table("t", ["id", "CustomerID"])
    out = prune_tables_to_gold([t], "SELECT CustomerID FROM t", keep_distractors=0)
    assert _names(out) == ["CustomerID"]


def test_column_order_is_preserved():
    """Pruning must not double as reordering, or the arm confounds two changes."""
    t = _table("t", ["a", "b", "c", "d"])
    out = prune_tables_to_gold([t], "SELECT d, a FROM t", keep_distractors=0)
    assert _names(out) == ["a", "d"]


def test_deterministic_for_the_same_seed():
    t = _table("t", [f"c{i}" for i in range(20)])
    gold = "SELECT c0 FROM t"
    a = _names(prune_tables_to_gold([t], gold, keep_distractors=3, seed=7))
    b = _names(prune_tables_to_gold([t], gold, keep_distractors=3, seed=7))
    assert a == b


def test_tables_are_never_dropped():
    """The arm varies column noise only; losing tables would confound it."""
    tables = [_table("a", ["x"]), _table("b", ["y"]), _table("c", [])]
    out = prune_tables_to_gold(tables, "SELECT x FROM a", keep_distractors=0)
    assert [t["name"] for t in out] == ["a", "b", "c"]


def test_no_gold_sql_is_a_noop():
    tables = [_table("a", ["x", "y", "z"])]
    assert prune_tables_to_gold(tables, "") is tables


@pytest.mark.parametrize("cols", [None, "notalist"])
def test_malformed_columns_pass_through(cols):
    out = prune_tables_to_gold([{"name": "t", "columns": cols}], "SELECT x FROM t")
    assert out[0]["columns"] == cols
