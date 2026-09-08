# SPDX-FileCopyrightText: Copyright (c) 2026, NVIDIA CORPORATION & AFFILIATES.
# All rights reserved.
# SPDX-License-Identifier: Apache-2.0

"""Applying a rule: replaying its search, and labelling what the search finds.

A rule is a saved global search plus the tags to apply to everything it matches,
and this is the half that does the applying. :func:`apply_rule` replays the
search through the *same* service the ``/search/global-search`` route calls, so
a rule labels exactly what the dialog that created it had on screen -- including
the list cap, since a rule that labelled more than the person could see would be
a rule they did not agree to.

Called once, when the rule is created. The tags it applies carry the rule's id
rather than a user id (see ``gsf.dal.tags.attach_tags_by_rule``), which is what
lets a tag's page say a rule did this and name it, and what makes deleting the
rule take those labels back.

Re-applying as the catalog grows is not wired up yet: nothing calls this on
ingest. It is written to be safe to call again -- labels it already applied are
skipped rather than rewritten -- so that hook is a call site, not a rewrite.
"""

from __future__ import annotations

import logging
from typing import Any

from gsf.dal import tags as tags_dal
from gsf.dal.tags import (
    TARGET_COLUMN,
    TARGET_COLUMN_ATTRIBUTE,
    TARGET_SQL_ATTRIBUTE,
    TARGET_TABLE,
    TARGET_TERM,
)
from gsf.semantic.constants import (
    LABEL_COLUMN_ATTRIBUTE,
    LABEL_SQL_ATTRIBUTE,
    LABEL_TERM,
)
from gsf.catalog.constants import Labels
from gsf.server.search import service as search_service

logger = logging.getLogger(__name__)

#: Which search hits can carry a tag, and as which kind of target.
#:
#: Global search answers over ten kinds and only five of them are taggable:
#: Databases and Schemas are containers a tag is deliberately not applied to
#: (see ``tag_target`` in ``gsf/dal/schema.py``), and the two analysis kinds have
#: no tag column at all. Hits of those kinds are dropped rather than refused --
#: a rule saved from the *All* tab legitimately matches them, and it still means
#: "label what you can".
#:
#: ``View`` is absent on purpose: a view is a ``catalog_table`` row whose
#: ``table_type`` says so, so a view hit arrives as ``Table`` and is labelled as
#: one. See ``SEARCH_TYPE_VIEW`` in ``gsf/dal/search.py``.
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


def apply_rule(
    *,
    rule_id: str,
    search_term: str,
    text_match_option: str,
    filters: dict[str, Any],
    tag_ids: list[str],
) -> int:
    """Label everything the rule's search matches, and say how many labels stuck.

    *filters* is the stored JSON, read with the same defaults
    ``GlobalSearchFilters`` declares: it is written with ``exclude_none``, so a
    filter the dialog never sent is absent from the row rather than null, and
    reading it with ``.get`` is what keeps those two the same rule.

    The count is of labels *applied*, not objects matched: an object already
    carrying the tag is not counted, because nothing was done to it.

    A search that matches nothing is not an error. A rule is a standing
    instruction -- "label what this finds" -- and finding nothing today says
    only that the catalog does not have it yet.
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
    applied = tags_dal.attach_tags_by_rule(
        rule_id=rule_id, tag_ids=tag_ids, targets=targets
    )
    logger.info(
        "Rule %s matched %d objects, %d taggable, applied %d labels",
        rule_id,
        len(hits),
        len(targets),
        applied,
    )
    return applied
