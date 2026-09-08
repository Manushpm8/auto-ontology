# SPDX-FileCopyrightText: Copyright (c) 2026, NVIDIA CORPORATION & AFFILIATES.
# All rights reserved.
# SPDX-License-Identifier: Apache-2.0

"""Unit tests for applying a rule.

Both halves of :func:`apply_rule` are mocked out -- the search it replays and
the labelling it delegates -- because what is worth pinning here is the bit in
between: which hits become labels, and as which kind.

Global search answers over ten kinds and only five accept a tag, so a rule saved
from the *All* tab routinely matches objects it cannot label. Dropping those
quietly is the intended behaviour, and the easiest thing to get wrong in a way
nothing else catches: passing an untaggable kind through would raise from the
DAL, and passing a view through as a view would raise the same way while looking
right.

The filters go the other way and are just as easy to get wrong. They are stored
with ``exclude_none``, so a filter the dialog never sent is *absent* rather than
null, and reading it back has to produce the same search the dialog ran -- which
is what the defaults here are for.
"""

from __future__ import annotations

from unittest.mock import patch

from gsf.server.rules.service import apply_rule

TAG_IDS = ["tag-1"]


def _apply(hits: list[dict], filters: dict | None = None) -> tuple:
    """Apply a rule over *hits*, returning ``(applied, attach_kwargs, search)``."""
    with (
        patch("gsf.server.rules.service.search_service.global_search") as search,
        patch("gsf.server.rules.service.tags_dal.attach_tags_by_rule") as attach,
    ):
        search.return_value = {"data": hits, "count": len(hits)}
        attach.return_value = len(hits)
        applied = apply_rule(
            rule_id="rule-1",
            search_term="revenue",
            text_match_option="contains",
            filters={} if filters is None else filters,
            tag_ids=TAG_IDS,
        )
    return applied, attach.call_args.kwargs, search.call_args.kwargs


def test_every_taggable_kind_becomes_a_target() -> None:
    _applied, attach, _search = _apply(
        [
            {"id": "t1", "type": "Term"},
            {"id": "tb1", "type": "Table"},
            {"id": "c1", "type": "Column"},
            {"id": "ca1", "type": "ColumnAttribute"},
            {"id": "sa1", "type": "SqlAttribute"},
        ]
    )

    assert attach["targets"] == [
        ("term", "t1"),
        ("table", "tb1"),
        ("column", "c1"),
        ("column_attribute", "ca1"),
        ("sql_attribute", "sa1"),
    ]
    assert attach["rule_id"] == "rule-1"
    assert attach["tag_ids"] == TAG_IDS


def test_a_view_is_labelled_as_the_table_it_is() -> None:
    """A view hit arrives as ``Table``; ``View`` is a tab, not a kind."""
    _applied, attach, _search = _apply(
        [{"id": "v1", "type": "Table", "table_type": "view"}]
    )

    assert attach["targets"] == [("table", "v1")]


def test_kinds_that_cannot_carry_a_tag_are_dropped() -> None:
    """A rule saved from the All tab matches these, and still labels the rest."""
    _applied, attach, _search = _apply(
        [
            {"id": "db1", "type": "Database"},
            {"id": "s1", "type": "Schema"},
            {"id": "ca1", "type": "CustomAnalysis"},
            {"id": "pa1", "type": "PqlAnalysis"},
            {"id": "t1", "type": "Term"},
        ]
    )

    assert attach["targets"] == [("term", "t1")]


def test_a_hit_with_no_id_is_dropped_rather_than_labelled() -> None:
    """The search normalises a missing id to null; a label needs a target."""
    _applied, attach, _search = _apply([{"id": None, "type": "Term"}])

    assert attach["targets"] == []


def test_matching_nothing_is_not_an_error() -> None:
    """A rule is a standing instruction: the catalog may not have it yet."""
    applied, attach, _search = _apply([])

    assert applied == 0
    assert attach["targets"] == []


def test_absent_filters_replay_the_search_defaults() -> None:
    """What ``exclude_none`` left out has to read back as the search's own default."""
    _applied, _attach, search = _apply([], filters={})

    assert search["objects"] is None
    assert search["include_description"] is False
    assert search["include_synonyms"] is True


def test_stored_filters_are_handed_to_the_search_as_saved() -> None:
    _applied, _attach, search = _apply(
        [],
        filters={"objects": ["Table"], "description": True, "synonyms": False},
    )

    assert search["objects"] == ["Table"]
    assert search["include_description"] is True
    assert search["include_synonyms"] is False
    assert search["search_term"] == "revenue"
    assert search["text_match_option"] == "contains"
