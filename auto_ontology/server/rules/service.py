# SPDX-FileCopyrightText: Copyright (c) 2026, NVIDIA CORPORATION & AFFILIATES.
# All rights reserved.
# SPDX-License-Identifier: Apache-2.0

"""Applying a rule: replaying its search, and labelling what the search finds.

A rule is a saved global search plus the tags to apply to everything it matches,
and this is the half that does the applying. :func:`find_targets` replays the
search through the *same* service the ``/search/global-search`` route calls, so
a rule labels exactly what the dialog that created it had on screen -- including
the list cap, since a rule that labelled more than the person could see would be
a rule they did not agree to. :func:`label_targets` writes the labels it found.

Two functions because only the second one belongs inside the transaction that
saves the rule, and the split is what keeps the search out of it. See each for
why.

The tags either path applies carry the rule's id rather than a user id (see
``auto_ontology.dal.tags.attach_tags_by_rule``), which is what lets a tag's page say a
rule did this and name it, and what makes deleting the rule take those labels
back.

:func:`reapply_rules` is the other half of a rule's life. A rule is a standing
instruction rather than a one-off labelling, so it is replayed on every ingest
-- a column added last night matches the same search the dialog ran last
month, and nothing else would ever put the tag on it.

**The two paths do not run the same search, and the difference is the point.**
Creating a rule labels what the person saw, so it goes through
``global_search`` and inherits its cap: a rule that labelled more than the
dialog showed is a rule they did not agree to. Replaying one labels what the
catalog now holds, so it goes through ``search_service.match_selects``, which
is uncapped -- a rule matching five thousand columns must not stop at the size
of a page it is no longer showing anybody.
"""

from __future__ import annotations

import logging
from typing import Any

from sqlalchemy import Select

from auto_ontology.dal import rules as rules_dal
from auto_ontology.dal import tags as tags_dal
from auto_ontology.dal.tags import (
    TARGET_COLUMN,
    TARGET_COLUMN_ATTRIBUTE,
    TARGET_SQL_ATTRIBUTE,
    TARGET_TABLE,
    TARGET_TERM,
)
from auto_ontology.semantic.constants import (
    LABEL_COLUMN_ATTRIBUTE,
    LABEL_SQL_ATTRIBUTE,
    LABEL_TERM,
)
from auto_ontology.catalog.constants import Labels
from auto_ontology.server.search import service as search_service

logger = logging.getLogger(__name__)

#: Which search hits can carry a tag, and as which kind of target.
#:
#: Global search answers over ten kinds and only five of them are taggable:
#: Databases and Schemas are containers a tag is deliberately not applied to
#: (see ``tag_target`` in ``auto_ontology/dal/schema.py``), and the two analysis kinds have
#: no tag column at all. Hits of those kinds are dropped rather than refused --
#: a rule saved from the *All* tab legitimately matches them, and it still means
#: "label what you can".
#:
#: ``View`` is absent on purpose: a view is a ``catalog_table`` row whose
#: ``table_type`` says so, so a view hit arrives as ``Table`` and is labelled as
#: one. See ``SEARCH_TYPE_VIEW`` in ``auto_ontology/dal/search.py``.
TAGGABLE_SEARCH_TYPES: dict[str, str] = {
    LABEL_TERM: TARGET_TERM,
    Labels.TABLE: TARGET_TABLE,
    Labels.COLUMN: TARGET_COLUMN,
    LABEL_COLUMN_ATTRIBUTE: TARGET_COLUMN_ATTRIBUTE,
    LABEL_SQL_ATTRIBUTE: TARGET_SQL_ATTRIBUTE,
}


def _taggable_targets(hits: list[dict[str, Any]]) -> list[tuple[str, str]]:
    """The ``(kind, item_id)`` pairs among *hits* that a tag can be applied to.

    Hits of an untaggable kind are dropped, as are hits with no id -- the search
    normalises a missing one to ``None`` rather than omitting the row, and a
    label needs something to point at.
    """
    targets = []
    for hit in hits:
        kind = TAGGABLE_SEARCH_TYPES.get(hit.get("type") or "")
        item_id = hit.get("id")
        if kind is not None and item_id is not None:
            targets.append((kind, item_id))
    return targets


def find_targets(
    *,
    search_term: str,
    text_match_option: str,
    filters: dict[str, Any],
) -> list[tuple[str, str]]:
    """Replay the rule's search and return what of it can carry a tag.

    Read-only, and called *before* the transaction that saves the rule rather
    than inside it. It needs nothing that transaction produces -- a rule is not
    a searchable object, so the search reads the same catalog either way -- and
    it is the slow half by an order of magnitude: measured against half a
    million columns it takes 70-400 ms, where the insert and the labels together
    take about 20 ms. A substring match has no index to use (the btree on
    ``name`` cannot serve ``ilike '%x%'``), so that cost grows with the catalog.
    Run inside the transaction, all of it was spent holding a pooled *write*
    connection and the locks on the freshly inserted rule row.

    Ordering the search first is not a weaker guarantee. The two writes still
    commit together, which is the whole of what atomicity buys here, and a
    ``SELECT`` takes no locks on what it finds: an object could already
    disappear between being matched and being labelled, and the transaction
    could only ever roll the labels back, not prevent it.

    *filters* is the stored JSON, read with the same defaults
    ``GlobalSearchFilters`` declares: it is written with ``exclude_none``, so a
    filter the dialog never sent is absent from the row rather than null, and
    reading it with ``.get`` is what keeps those two the same rule.

    A search that matches nothing is not an error, and returns no targets. A
    rule is a standing instruction -- "label what this finds" -- and finding
    nothing today says only that the catalog does not have it yet.
    """
    found = search_service.global_search(
        search_term=search_term,
        text_match_option=text_match_option,
        objects=filters.get("objects"),
        include_description=filters.get("description", False),
        include_synonyms=filters.get("synonyms", True),
    )
    hits = found["data"]
    targets = _taggable_targets(hits)
    logger.info(
        "Rule search %r matched %d objects, %d taggable",
        search_term,
        len(hits),
        len(targets),
    )
    return targets


def label_targets(
    *, rule_id: str, tag_ids: list[str], targets: list[tuple[str, str]]
) -> int:
    """Apply the rule's tags to *targets*, and say how many labels stuck.

    The transactional half: called inside the block that inserts the rule, so
    the rule and its labels are one unit of work and a rule that could not
    label the catalog is not left saved and inert.

    The count is of labels *applied*, not objects matched: an object already
    carrying the tag is not counted, because nothing was done to it.
    """
    applied = tags_dal.attach_tags_by_rule(
        rule_id=rule_id, tag_ids=tag_ids, targets=targets
    )
    logger.info(
        "Rule %s applied %d labels over %d targets", rule_id, applied, len(targets)
    )
    return applied


def _taggable_selects(selects: dict[str, Select]) -> dict[str, Select]:
    """*selects* keyed by tag target kind, with the untaggable kinds dropped.

    The same reduction :func:`_taggable_targets` performs on hits, one step
    earlier: these are statements, so a kind is dropped by not asking for it
    rather than by discarding its rows. Databases, Schemas and the two
    analysis kinds have no ``tag_target`` column, and a rule saved from the
    *All* tab matches them legitimately -- it still means "label what you can".
    """
    return {
        TAGGABLE_SEARCH_TYPES[label]: statement
        for label, statement in selects.items()
        if label in TAGGABLE_SEARCH_TYPES
    }


def reapply_rule(rule: dict[str, Any]) -> tuple[int, int]:
    """Bring one rule's labels in line with what its search matches now.

    *rule* is a row of :func:`auto_ontology.dal.rules.list_rules`: the stored search and
    the tags, read from the database rather than passed in, so what is
    replayed is what was saved.

    Returns ``(applied, removed)`` — labels written and labels taken back. Both
    are usually zero, because most rules match the same objects they did
    yesterday, and that is the number worth logging: a nightly line saying a
    rule applied nothing is the rule working, not the rule idle.

    The filters are read with the same defaults ``GlobalSearchFilters``
    declares, as :func:`find_targets` reads them: they were written with
    ``exclude_none``, so a filter the dialog never sent is absent from the row
    rather than null, and ``.get`` is what keeps the two the same rule.
    """
    filters = rule["filters"] or {}
    selects = search_service.match_selects(
        search_term=rule["search_term"],
        text_match_option=rule["text_match_option"],
        objects=filters.get("objects"),
        include_description=filters.get("description", False),
        include_synonyms=filters.get("synonyms", True),
    )
    return tags_dal.sync_tags_by_rule(
        rule_id=rule["id"],
        tag_ids=[tag["id"] for tag in rule["tags"]],
        targets=_taggable_selects(selects),
    )


def reapply_rules() -> tuple[int, int]:
    """Replay every stored rule, and report the labels the pass changed.

    The ingest hook. Called after a pass has finished writing the catalog and
    the semantic layer, because a rule labels both and replaying it against a
    half-written catalog would take back labels on objects that are about to
    be there again.

    Rules are independent of one another, so the order they run in does not
    matter and no rule can see another's work: a rule's search matches names,
    descriptions and aliases, never tags. That is worth stating because the
    obvious next feature -- filtering by tag -- would end that, and this loop
    would then need an order it does not have today.

    A rule that fails is logged and the rest still run, as a connection that
    fails to ingest does not stop the others. One malformed rule must not cost
    a night's labelling for every other rule in the deployment.
    """
    rules = rules_dal.list_rules()
    if not rules:
        logger.info("No rules to re-apply")
        return 0, 0

    applied = removed = 0
    failed = 0
    for rule in rules:
        try:
            rule_applied, rule_removed = reapply_rule(rule)
        except Exception:
            failed += 1
            logger.exception("Rule %s could not be re-applied", rule["name"])
            continue
        applied += rule_applied
        removed += rule_removed
        if rule_applied or rule_removed:
            logger.info(
                "Rule %s: %d label(s) applied, %d taken back",
                rule["name"],
                rule_applied,
                rule_removed,
            )

    # Per-rule failures are swallowed above, so the tally is what says whether
    # the pass did its job -- without it this line reads the same whether every
    # rule ran or every one of them threw.
    if failed:
        logger.warning(
            "Re-applied %d of %d rule(s), %d failed — "
            "%d label(s) applied, %d taken back",
            len(rules) - failed,
            len(rules),
            failed,
            applied,
            removed,
        )
    else:
        logger.info(
            "Re-applied %d rule(s) — %d label(s) applied, %d taken back",
            len(rules),
            applied,
            removed,
        )
    return applied, removed
