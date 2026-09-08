# SPDX-FileCopyrightText: Copyright (c) 2026, NVIDIA CORPORATION & AFFILIATES.
# All rights reserved.
# SPDX-License-Identifier: Apache-2.0

"""Rules on Postgres: the saved search, and the tags it applies.

Concentrated on the parts of a rule that are not a plain row.

The first is that a rule spans two tables and is written in one transaction. A
rule with no tags applies nothing, so the test that matters is not that the
happy path inserts both — it is that a tag id the foreign key rejects leaves no
rule behind at all.

The second is what a read is allowed to believe. ``rule__tag.sent`` is whatever
the caller posted, including fields from a model this schema knows nothing
about, and it is kept so a read answers with the tag that was picked. It is not
the source of truth: the id is the foreign key's, the name is the tag table's,
and the tests pin both by posting a stale name and renaming a tag underneath a
saved rule.

The third is order and the page taken from it. The tags come back in the order
they were picked, which the composite primary key does not give; the rules come
back ordered by name case-insensitively, which ``ORDER BY name`` does not; and
two pages of one list have to neither repeat a rule nor skip one, which is what
makes the order worth pinning. The search is checked on both halves of what a
card shows -- the rule's name and its tags' -- and against the row-per-tag a
join would return where the ``EXISTS`` returns one.

The fourth is how little a rename is allowed to be, and that a name names one
rule. Uniqueness is folded for case and surrounding space, as a tag's is, so a
rename is checked against a name in another case and a rule's *own* name is
checked not to collide with itself. Everything else is held still -- the search,
the filters, the tags, the author -- and the tests pin the two columns an edit
does write:
``modified``, which ``onupdate`` advances, and ``modified_by``, which is written
on every rename including one that names nobody, so a rename cannot be credited
to whoever made the last one. What a rename does to the *labels* the rule
applied is covered in ``test_tags.py``, where the tagged objects are.

The fifth is what a delete takes with it, which is more than the row: the tag
links cascade, and so do the labels the rule applied -- that one is covered in
``test_tags.py``, where the tagged objects are. What it must *not* take is the
tags themselves, or another rule applying the same tag, so both have a test
here.

Needs a migrated database and skips without one.
"""

from __future__ import annotations

import os
import uuid

import pytest

pytest.importorskip("sqlalchemy")

from sqlalchemy.exc import IntegrityError  # noqa: E402

from gsf.dal import schema as s  # noqa: E402
from gsf.dal.rules import (  # noqa: E402
    count_rules,
    create_rule,
    delete_rule,
    get_rule,
    list_rules,
    update_rule,
)
from gsf.dal.session import store  # noqa: E402
from gsf.dal.tags import create_tag  # noqa: E402

FILTERS = {"description": False, "synonyms": True, "objects": None}


@pytest.fixture(scope="module", autouse=True)
def require_schema():
    if not os.environ.get("POSTGRES_USER"):
        pytest.skip("POSTGRES_* not set")
    try:
        store().query_read("SELECT 1 FROM rule__tag LIMIT 1")
    except Exception as exc:  # noqa: BLE001
        pytest.skip(f"gsf schema unavailable (alembic upgrade head): {exc}")


@pytest.fixture
def prefix():
    """A name prefix unique to this test, and its cleanup.

    The rules go first: ``rule__tag`` cascades from both sides, so deleting the
    tags would take the links with them and leave the rules tagless rather than
    gone.
    """
    value = f"r-{uuid.uuid4().hex[:8]}"
    yield value
    store().query_write(s.rule.delete().where(s.rule.c.name.like(f"%{value}%")))
    store().query_write(s.tag.delete().where(s.tag.c.name.like(f"%{value}%")))


def _saved(prefix: str, *, name: str | None = None, tags: list[dict]) -> str:
    """A rule with *tags*, saved. Returns its id."""
    return create_rule(
        name=name or f"{prefix}-rule",
        search_term="customer",
        text_match_option="contains",
        filters=FILTERS,
        tags=tags,
        created_by="user-1",
    )


def _read(rule_id: str) -> dict:
    return next(rule for rule in list_rules() if rule["id"] == rule_id)


def test_a_saved_rule_reads_back_with_what_it_was_given(prefix) -> None:
    tag = create_tag(name=f"{prefix}-pii")

    rule_id = _saved(prefix, tags=[{"id": tag["id"], "name": tag["name"]}])

    rule = _read(rule_id)
    assert rule["name"] == f"{prefix}-rule"
    assert rule["search_term"] == "customer"
    assert rule["text_match_option"] == "contains"
    assert rule["filters"] == FILTERS
    assert rule["created_by"] == "user-1"
    assert rule["tags"] == [{"id": tag["id"], "name": tag["name"]}]


def test_the_timestamps_come_from_the_database(prefix) -> None:
    """Both from one clock, and a fresh rule has not been modified."""
    tag = create_tag(name=f"{prefix}-pii")

    rule = _read(_saved(prefix, tags=[{"id": tag["id"]}]))

    assert rule["created"].tzinfo is not None
    assert rule["modified"] == rule["created"]


def test_everything_a_caller_sent_with_a_tag_survives(prefix) -> None:
    """The point of ``sent``: a client's own fields come back untouched."""
    tag = create_tag(name=f"{prefix}-pii")

    rule_id = _saved(
        prefix,
        tags=[
            {
                "id": tag["id"],
                "name": tag["name"],
                "label": "tag",
                "type": "tag",
                "created_date": {"year": 2024},
            }
        ],
    )

    assert _read(rule_id)["tags"] == [
        {
            "id": tag["id"],
            "name": tag["name"],
            "label": "tag",
            "type": "tag",
            "created_date": {"year": 2024},
        }
    ]


def test_the_name_comes_from_the_tag_table_not_from_what_was_sent(prefix) -> None:
    """A posted name is a snapshot, and the vocabulary outranks it."""
    tag = create_tag(name=f"{prefix}-pii")

    rule_id = _saved(prefix, tags=[{"id": tag["id"], "name": "whatever-was-cached"}])

    assert _read(rule_id)["tags"][0]["name"] == f"{prefix}-pii"


def test_a_renamed_tag_reads_back_under_its_new_name(prefix) -> None:
    tag = create_tag(name=f"{prefix}-pii")
    rule_id = _saved(prefix, tags=[{"id": tag["id"], "name": tag["name"]}])

    store().query_write(
        s.tag.update().where(s.tag.c.id == tag["id"]).values(name=f"{prefix}-gdpr")
    )

    assert _read(rule_id)["tags"][0]["name"] == f"{prefix}-gdpr"


def test_the_tags_keep_the_order_they_were_picked_in(prefix) -> None:
    """``position``'s whole job -- the primary key orders by id instead."""
    picked = [create_tag(name=f"{prefix}-{n}") for n in ("gamma", "alpha", "beta")]

    rule_id = _saved(prefix, tags=[{"id": tag["id"]} for tag in picked])

    assert [tag["id"] for tag in _read(rule_id)["tags"]] == [t["id"] for t in picked]


def test_a_deleted_tag_leaves_the_rules_that_applied_it(prefix) -> None:
    """The cascade, which is why the tags are a link table and not a blob."""
    kept = create_tag(name=f"{prefix}-kept")
    dropped = create_tag(name=f"{prefix}-dropped")
    rule_id = _saved(prefix, tags=[{"id": kept["id"]}, {"id": dropped["id"]}])

    store().query_write(s.tag.delete().where(s.tag.c.id == dropped["id"]))

    assert [tag["id"] for tag in _read(rule_id)["tags"]] == [kept["id"]]


def test_a_tag_that_is_not_a_tag_saves_no_rule(prefix) -> None:
    """The transaction: a rule row that outlived its tags would apply nothing."""
    with pytest.raises(IntegrityError):
        _saved(prefix, tags=[{"id": "not-a-tag"}])

    assert [rule for rule in list_rules() if prefix in rule["name"]] == []


def test_the_listing_is_ordered_case_insensitively(prefix) -> None:
    """``ORDER BY name`` would put every capital ahead of every lowercase."""
    tag = create_tag(name=f"{prefix}-pii")
    for name in (f"{prefix}-beta", f"{prefix}-Alpha", f"{prefix}-gamma"):
        _saved(prefix, name=name, tags=[{"id": tag["id"]}])

    names = [rule["name"] for rule in list_rules() if prefix in rule["name"]]
    assert names == [f"{prefix}-Alpha", f"{prefix}-beta", f"{prefix}-gamma"]


def test_a_page_is_a_window_on_one_order(prefix) -> None:
    """Two pages of one list: neither repeats a rule nor skips one."""
    tag = create_tag(name=f"{prefix}-pii")
    for name in (f"{prefix}-c", f"{prefix}-a", f"{prefix}-b"):
        _saved(prefix, name=name, tags=[{"id": tag["id"]}])

    first = list_rules(search=prefix, skip=0, limit=2)
    second = list_rules(search=prefix, skip=2, limit=2)

    assert [rule["name"] for rule in first] == [f"{prefix}-a", f"{prefix}-b"]
    assert [rule["name"] for rule in second] == [f"{prefix}-c"]


def test_the_total_counts_the_whole_match_not_the_page(prefix) -> None:
    tag = create_tag(name=f"{prefix}-pii")
    for name in (f"{prefix}-a", f"{prefix}-b", f"{prefix}-c"):
        _saved(prefix, name=name, tags=[{"id": tag["id"]}])

    assert len(list_rules(search=prefix, limit=2)) == 2
    assert count_rules(search=prefix) == 3


def test_a_search_matches_the_rule_name(prefix) -> None:
    tag = create_tag(name=f"{prefix}-pii")
    _saved(prefix, name=f"{prefix}-customers", tags=[{"id": tag["id"]}])
    _saved(prefix, name=f"{prefix}-orders", tags=[{"id": tag["id"]}])

    found = list_rules(search=f"{prefix}-CUSTOM")

    assert [rule["name"] for rule in found] == [f"{prefix}-customers"]
    assert count_rules(search=f"{prefix}-CUSTOM") == 1


def test_a_search_matches_the_name_of_a_tag_the_rule_applies(prefix) -> None:
    """The other half of what a card shows, so it is searchable too."""
    confidential = create_tag(name=f"{prefix}-confidential")
    public = create_tag(name=f"{prefix}-public")
    _saved(prefix, name=f"{prefix}-one", tags=[{"id": confidential["id"]}])
    _saved(prefix, name=f"{prefix}-two", tags=[{"id": public["id"]}])

    found = list_rules(search=f"{prefix}-confidential")

    assert [rule["name"] for rule in found] == [f"{prefix}-one"]


def test_a_rule_matching_on_several_tags_is_one_row(prefix) -> None:
    """The tag half is an ``EXISTS``; a join would return the rule per tag."""
    first = create_tag(name=f"{prefix}-pii-a")
    second = create_tag(name=f"{prefix}-pii-b")
    _saved(prefix, tags=[{"id": first["id"]}, {"id": second["id"]}])

    assert len(list_rules(search=f"{prefix}-pii")) == 1
    assert count_rules(search=f"{prefix}-pii") == 1


def test_two_rules_do_not_borrow_each_other_s_tags(prefix) -> None:
    """One read serves every rule, so the rows have to land on the right one."""
    first = create_tag(name=f"{prefix}-first")
    second = create_tag(name=f"{prefix}-second")

    one = _saved(prefix, name=f"{prefix}-one", tags=[{"id": first["id"]}])
    two = _saved(prefix, name=f"{prefix}-two", tags=[{"id": second["id"]}])

    assert [tag["id"] for tag in _read(one)["tags"]] == [first["id"]]
    assert [tag["id"] for tag in _read(two)["tags"]] == [second["id"]]


# --------------------------------------------------------------------------
# get_rule and update_rule
# --------------------------------------------------------------------------


def test_one_rule_reads_the_same_as_its_row_in_the_list(prefix) -> None:
    """Two reads of one rule, so a caller renders it the same either way."""
    tag = create_tag(name=f"{prefix}-pii")
    rule_id = _saved(prefix, tags=[{"id": tag["id"]}])

    assert get_rule(rule_id) == _read(rule_id)


def test_reading_an_id_that_is_not_a_rule_is_nothing(prefix) -> None:
    assert get_rule(str(uuid.uuid4())) is None


def test_a_rename_changes_the_name_and_nothing_else(prefix) -> None:
    """A name labels the rule in the settings list; the search *is* the rule."""
    tag = create_tag(name=f"{prefix}-pii")
    rule_id = _saved(prefix, tags=[{"id": tag["id"]}])
    before = _read(rule_id)

    renamed = update_rule(rule_id=rule_id, name=f"{prefix}-renamed")

    assert renamed is not None
    assert renamed["name"] == f"{prefix}-renamed"
    assert renamed["search_term"] == before["search_term"]
    assert renamed["text_match_option"] == before["text_match_option"]
    assert renamed["filters"] == before["filters"]
    assert renamed["created"] == before["created"]
    assert renamed["created_by"] == before["created_by"]
    assert renamed["tags"] == before["tags"]


def test_a_rename_records_who_made_it_and_that_it_happened(prefix) -> None:
    """``modified`` past ``created`` is the fact that this rule was renamed."""
    tag = create_tag(name=f"{prefix}-pii")
    rule_id = _saved(prefix, tags=[{"id": tag["id"]}])

    renamed = update_rule(
        rule_id=rule_id, name=f"{prefix}-renamed", modified_by="user-2"
    )

    assert renamed is not None
    assert renamed["modified"] > renamed["created"]
    assert renamed["modified_by"] == "user-2"


def test_a_rename_naming_nobody_does_not_credit_the_last_editor(prefix) -> None:
    """Written on every rename: the previous editor did not make this one."""
    tag = create_tag(name=f"{prefix}-pii")
    rule_id = _saved(prefix, tags=[{"id": tag["id"]}])
    update_rule(rule_id=rule_id, name=f"{prefix}-once", modified_by="user-2")

    renamed = update_rule(rule_id=rule_id, name=f"{prefix}-twice")

    assert renamed is not None
    assert renamed["modified_by"] is None


def test_a_fresh_rule_has_no_editor(prefix) -> None:
    tag = create_tag(name=f"{prefix}-pii")

    assert _read(_saved(prefix, tags=[{"id": tag["id"]}]))["modified_by"] is None


def test_renaming_an_id_that_is_not_a_rule_is_nothing(prefix) -> None:
    assert update_rule(rule_id=str(uuid.uuid4()), name=f"{prefix}-renamed") is None


def test_a_rename_onto_a_name_another_rule_holds_is_refused(prefix) -> None:
    """One name has to name one rule: a delete takes back what a rule labelled."""
    tag = create_tag(name=f"{prefix}-pii")
    _saved(prefix, name=f"{prefix}-taken", tags=[{"id": tag["id"]}])
    other = _saved(prefix, name=f"{prefix}-other", tags=[{"id": tag["id"]}])

    with pytest.raises(ValueError):
        update_rule(rule_id=other, name=f"  {prefix}-TAKEN  ")

    assert _read(other)["name"] == f"{prefix}-other"


def test_a_rule_may_be_renamed_to_the_name_it_has(prefix) -> None:
    """Its own row is not a collision: re-typing a name is an ordinary rename."""
    tag = create_tag(name=f"{prefix}-pii")
    rule_id = _saved(prefix, name=f"{prefix}-rule", tags=[{"id": tag["id"]}])

    assert update_rule(rule_id=rule_id, name=f"{prefix}-rule") is not None


def test_a_rename_may_change_only_the_case_of_a_name(prefix) -> None:
    """How a rule created shouting gets fixed."""
    tag = create_tag(name=f"{prefix}-pii")
    rule_id = _saved(prefix, name=f"{prefix}-RULE", tags=[{"id": tag["id"]}])

    renamed = update_rule(rule_id=rule_id, name=f"{prefix}-rule")

    assert renamed is not None
    assert renamed["name"] == f"{prefix}-rule"


def test_a_second_rule_cannot_be_created_under_a_name_in_use(prefix) -> None:
    tag = create_tag(name=f"{prefix}-pii")
    _saved(prefix, name=f"{prefix}-taken", tags=[{"id": tag["id"]}])

    with pytest.raises(ValueError):
        _saved(prefix, name=f"{prefix}-TAKEN ", tags=[{"id": tag["id"]}])

    assert count_rules(search=prefix) == 1


# --------------------------------------------------------------------------
# delete_rule
# --------------------------------------------------------------------------


def test_deleting_a_rule_removes_it_and_says_it_did(prefix) -> None:
    tag = create_tag(name=f"{prefix}-pii")
    rule_id = _saved(prefix, tags=[{"id": tag["id"]}])

    assert delete_rule(rule_id) is True
    assert [rule["id"] for rule in list_rules(search=prefix)] == []
    assert count_rules(search=prefix) == 0


def test_deleting_an_id_that_is_not_a_rule_is_reported(prefix) -> None:
    """A stale settings list, which the route turns into a 404."""
    assert delete_rule(str(uuid.uuid4())) is False


def test_deleting_a_rule_takes_its_tag_links_with_it(prefix) -> None:
    """``rule__tag`` cascades, so no link outlives the rule that held it."""
    tag = create_tag(name=f"{prefix}-pii")
    rule_id = _saved(prefix, tags=[{"id": tag["id"]}])

    delete_rule(rule_id)

    assert (
        store().query_read(s.rule__tag.select().where(s.rule__tag.c.rule_id == rule_id))
        == []
    )


def test_deleting_a_rule_leaves_the_tags_themselves(prefix) -> None:
    """The vocabulary is not the rule's to take: other rules and objects use it."""
    tag = create_tag(name=f"{prefix}-pii")

    delete_rule(_saved(prefix, tags=[{"id": tag["id"]}]))

    assert store().query_read(s.tag.select().where(s.tag.c.id == tag["id"]))


def test_deleting_one_rule_leaves_another_that_applies_the_same_tag(prefix) -> None:
    tag = create_tag(name=f"{prefix}-pii")
    doomed = _saved(prefix, name=f"{prefix}-one", tags=[{"id": tag["id"]}])
    kept = _saved(prefix, name=f"{prefix}-two", tags=[{"id": tag["id"]}])

    delete_rule(doomed)

    assert [rule["id"] for rule in list_rules(search=prefix)] == [kept]
    assert [tag["id"] for tag in _read(kept)["tags"]] == [tag["id"]]
