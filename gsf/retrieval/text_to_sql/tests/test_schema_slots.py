"""Per-slot schema readings must be opt-in, and slot 0 must stay untouched.
The arm rests on adding readings the pool would otherwise never write, so the
one thing it must not do is disturb the reading we already get. Slot 0 is that
reading, and ``BIRD_NCAND=1`` must reproduce single-candidate behavior exactly.
"""

from __future__ import annotations

import pytest

from gsf.retrieval.text_to_sql.agents.sql_from_semantic import (
    _ALTERNATIVE_BINDING,
    _JOIN_PREFERRING,
    _SCHEMA_SLOT_ROLES,
    _schema_directive,
    _schema_slots_enabled,
)

GROUPS = [
    {
        "entity": "charter",
        "columns": [
            {"qualified": "schools.Charter", "table": "schools"},
            {"qualified": "frpm.`Charter Funding Type`", "table": "frpm"},
        ],
    },
    {
        "entity": "funding",
        "columns": [
            {"qualified": "schools.FundingType", "table": "schools"},
            {"qualified": "frpm.`Charter Funding Type`", "table": "frpm"},
        ],
    },
]


@pytest.fixture
def on(monkeypatch):
    monkeypatch.setenv("BIRD_SCHEMA_SLOTS", "1")


@pytest.fixture
def off(monkeypatch):
    monkeypatch.delenv("BIRD_SCHEMA_SLOTS", raising=False)


def test_off_by_default(off):
    assert _schema_slots_enabled() is False
    assert _schema_directive(1, GROUPS) == ""


def test_slot_zero_never_gets_a_directive(on):
    """Slot 0 is the reading we already get; the arm may only add to it."""
    assert _schema_directive(0, GROUPS) == ""


def test_join_preferring_lands_on_slot_one(on):
    assert _schema_directive(1, GROUPS) == _JOIN_PREFERRING


def test_alternative_binding_names_the_ambiguous_terms(on):
    d = _schema_directive(2, GROUPS)
    assert d.startswith(_ALTERNATIVE_BINDING)
    assert '"charter"' in d and '"funding"' in d


def test_alternative_binding_falls_back_without_ambiguity(on):
    """With nothing concrete to point at, 'pick something else' is just noise."""
    assert _schema_directive(2, []) == _JOIN_PREFERRING
    assert _schema_directive(2, None) == _JOIN_PREFERRING


def test_single_column_groups_are_not_ambiguous(on):
    one = [{"entity": "x", "columns": [{"qualified": "t.x", "table": "t"}]}]
    assert _schema_directive(2, one) == _JOIN_PREFERRING


def test_fourth_slot_is_free(on):
    assert _SCHEMA_SLOT_ROLES[3] == "free"
    assert _schema_directive(3, GROUPS) == ""


def test_roles_repeat_beyond_the_plan(on):
    """At BIRD_NCAND=7 the roles wrap; slot 4 must behave like slot 0's role."""
    assert _schema_directive(4, GROUPS) == ""
    assert _schema_directive(5, GROUPS) == _JOIN_PREFERRING


def test_terms_are_capped(on):
    many = [
        {"entity": f"e{i}", "columns": [{"qualified": "a"}, {"qualified": "b"}]}
        for i in range(20)
    ]
    assert _schema_directive(2, many).count('"e') == 6


def test_directives_do_not_force_a_wrong_answer(on):
    """Both must leave an escape hatch, or they turn breadth into damage."""
    for d in (_JOIN_PREFERRING, _ALTERNATIVE_BINDING):
        low = d.lower()
        assert "if" in low
