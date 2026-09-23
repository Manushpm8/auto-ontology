# SPDX-FileCopyrightText: Copyright (c) 2026, NVIDIA CORPORATION & AFFILIATES.
# All rights reserved.
# SPDX-License-Identifier: Apache-2.0

"""Unit tests for applying a rule.

The search :func:`find_targets` replays is mocked out, and so is the DAL
:func:`label_targets` delegates to, because what is worth pinning here is the
bit in between: which hits become labels, and as which kind.

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

from auto_ontology.server.rules.service import (
    find_targets,
    label_targets,
    reapply_rules,
)

TAG_IDS = ["tag-1"]


def _apply(hits: list[dict], filters: dict | None = None) -> tuple:
    """Both halves over *hits*, returning ``(applied, attach_kwargs, search)``.

    Run one after the other the way the route runs them -- the search outside a
    transaction, the labelling inside one -- so what the first half found is
    what the second half is asked to label.
    """
    with (
        patch(
            "auto_ontology.server.rules.service.search_service.global_search"
        ) as search,
        patch(
            "auto_ontology.server.rules.service.tags_dal.attach_tags_by_rule"
        ) as attach,
    ):
        search.return_value = {"data": hits, "count": len(hits)}
        attach.return_value = len(hits)
        targets = find_targets(
            search_term="revenue",
            text_match_option="contains",
            filters={} if filters is None else filters,
        )
        applied = label_targets(rule_id="rule-1", tag_ids=TAG_IDS, targets=targets)
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


# --------------------------------------------------------------------------
# Re-applying on ingest
# --------------------------------------------------------------------------
#
# The other half of a rule's life, and a different search: creating a rule
# labels what the person saw and inherits the list's cap, while replaying one
# labels what the catalog now holds and must not. What is worth pinning here
# is the same thing as above -- which kinds survive the trip -- plus the two
# things a nightly unattended pass turns into an outage if they are wrong: one
# bad rule must not stop the rest, and an empty deployment must not raise.


def _reapply(rules: list[dict]) -> tuple[tuple[int, int], list, list]:
    """Run the ingest pass over *rules*, returning what the two halves saw."""
    with (
        patch("auto_ontology.server.rules.service.rules_dal.list_rules") as list_rules,
        patch(
            "auto_ontology.server.rules.service.search_service.match_selects"
        ) as match,
        patch("auto_ontology.server.rules.service.tags_dal.sync_tags_by_rule") as sync,
    ):
        list_rules.return_value = rules
        match.side_effect = lambda **kwargs: SELECTS
        sync.return_value = (1, 0)
        totals = reapply_rules()
    return totals, match.call_args_list, sync.call_args_list


def _rule(**overrides) -> dict:
    """A rule as ``list_rules`` reads one back."""
    return {
        "id": "rule-1",
        "name": "Revenue",
        "search_term": "revenue",
        "text_match_option": "contains",
        "filters": {},
        "tags": [{"id": "tag-1", "name": "Finance"}],
        **overrides,
    }


#: What the uncapped search hands back: a statement per object type. The
#: statements are never run here -- the DAL is mocked -- so a marker per label
#: is enough to follow which ones reached the write.
SELECTS = {
    "Term": "select-term",
    "Table": "select-table",
    "Column": "select-column",
    "ColumnAttribute": "select-column-attribute",
    "SqlAttribute": "select-sql-attribute",
    "Database": "select-database",
    "Schema": "select-schema",
    "CustomAnalysis": "select-custom-analysis",
}


def test_every_taggable_kind_is_synced_and_the_rest_dropped() -> None:
    """The same reduction the create path does on hits, one step earlier: a
    kind with no ``tag_target`` column is dropped by not asking for it."""
    _totals, _match, sync = _reapply([_rule()])

    assert sync[0].kwargs["targets"] == {
        "term": "select-term",
        "table": "select-table",
        "column": "select-column",
        "column_attribute": "select-column-attribute",
        "sql_attribute": "select-sql-attribute",
    }
    assert sync[0].kwargs["rule_id"] == "rule-1"
    assert sync[0].kwargs["tag_ids"] == ["tag-1"]


def test_the_stored_search_is_what_gets_replayed() -> None:
    """Read from the row rather than reconstructed, so a rule labels what it
    was saved as -- and with the same defaults ``exclude_none`` left out."""
    _totals, match, _sync = _reapply([_rule()])

    assert match[0].kwargs == {
        "search_term": "revenue",
        "text_match_option": "contains",
        "objects": None,
        "include_description": False,
        "include_synonyms": True,
    }


def test_stored_filters_reach_the_uncapped_search_too() -> None:
    filters = {"objects": ["Column"], "description": True, "synonyms": False}
    _totals, match, _sync = _reapply([_rule(filters=filters)])

    assert match[0].kwargs["objects"] == ["Column"]
    assert match[0].kwargs["include_description"] is True
    assert match[0].kwargs["include_synonyms"] is False


def test_every_rule_runs_and_the_labels_are_totalled() -> None:
    totals, match, sync = _reapply(
        [_rule(id="rule-1"), _rule(id="rule-2"), _rule(id="rule-3")]
    )

    assert len(match) == len(sync) == 3
    assert totals == (3, 0)


def test_one_failing_rule_does_not_stop_the_others() -> None:
    """Unattended and nightly: a single malformed rule must not cost every
    other rule in the deployment a night's labelling."""
    with (
        patch("auto_ontology.server.rules.service.rules_dal.list_rules") as list_rules,
        patch(
            "auto_ontology.server.rules.service.search_service.match_selects"
        ) as match,
        patch("auto_ontology.server.rules.service.tags_dal.sync_tags_by_rule") as sync,
    ):
        list_rules.return_value = [_rule(id="bad"), _rule(id="good")]
        match.side_effect = [RuntimeError("no such column"), SELECTS]
        sync.return_value = (2, 1)

        assert reapply_rules() == (2, 1)

    # The failing rule reaches no write at all, which is the half that
    # matters: a search that could not run says nothing about what the rule
    # should be labelling, and syncing on it would take back every label the
    # rule holds.
    assert [call.kwargs["rule_id"] for call in sync.call_args_list] == ["good"]


def test_a_deployment_with_no_rules_does_nothing() -> None:
    """The ordinary case for most deployments, on every ingest."""
    totals, match, sync = _reapply([])

    assert totals == (0, 0)
    assert match == sync == []
