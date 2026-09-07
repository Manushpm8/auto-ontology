# SPDX-FileCopyrightText: Copyright (c) 2026, NVIDIA CORPORATION & AFFILIATES.
# All rights reserved.
# SPDX-License-Identifier: Apache-2.0

"""API route handlers for rule-based tags.

A rule is a saved global search plus the tags to apply to everything it
matches -- now, and again as the catalog grows.

**There is no store behind this router yet.** The ``rule`` table and its DAL
are the next piece of work; this exists ahead of them so the create dialog has
a real endpoint to post to and the OpenAPI spec carries the shape both sides
have agreed on. Route by route, that means:

* :func:`create_rule` validates the whole body -- including resolving the tag
  ids against the tag table, which does exist -- and answers with the rule it
  would have stored, ``id``, ``created_by`` and the timestamps filled in as the
  stored one would carry them.
* :func:`list_rules` answers an empty list.
* The three routes that address an *already stored* rule answer 404, because
  none is. :func:`_not_stored` is the single place that says so.

Nothing here fabricates a store to soften that. An in-memory dict would make
the list and the reads agree with each other for as long as one worker lived
and disagree with the next request that landed on another, which is harder to
reason about than a list that is honestly empty.

The validation is not throwaway, though, and that is the point of writing it
now: it is the half of the contract that does not depend on where a rule is
kept, so the version that persists rules keeps every check below and adds the
write underneath it.
"""

from __future__ import annotations

from datetime import UTC, datetime
from typing import Any
from uuid import uuid4

from fastapi import APIRouter, HTTPException, Request
from pydantic import BaseModel, ConfigDict, Field

from gsf.dal import tags as tags_dal
from gsf.server.identity import resolve_internal_user
from gsf.server.responses import IdResponse, RuleListResponse, RuleResponse

# A rule's search *is* a global search, so both come from the search router
# rather than being restated here: the filters a rule saves and the filters a
# search accepts cannot drift apart if they are the same model.
from gsf.server.search.constants import MIN_SEARCH_LENGTH, TEXT_MATCH_CONTAINS
from gsf.server.search.router import GlobalSearchFilters

router = APIRouter()


class RuleTagRef(BaseModel):
    """One tag a rule applies, as a caller names it.

    Whole objects rather than bare ids, so a client can post the tags it is
    already holding without reducing them first. Only ``id`` is required, and
    only ``id`` is authoritative: :func:`_resolved_tags` reads the name from the
    tag table rather than believing the one that arrived, because a client's
    copy is a snapshot and a rule that recorded a stale name would show a tag
    under a name it no longer has.

    ``extra="allow"`` so the fields a caller happens to carry -- a created
    timestamp, a label -- are accepted and ignored instead of turning a
    perfectly good tag into a 422. Ignored rather than stored: they describe the
    tag, which has its own table and its own endpoints, and a second copy of
    them inside a rule would be one that nothing keeps up to date.
    """

    model_config = ConfigDict(extra="allow")

    id: str
    name: str | None = None


class RuleCreate(BaseModel):
    """A rule as the create dialog sends it.

    ``search_term``, ``text_match_option`` and ``filters`` are the
    ``/search/global-search`` request being saved, spelled the way that route
    takes it.
    """

    name: str
    search_term: str
    text_match_option: str = TEXT_MATCH_CONTAINS
    filters: GlobalSearchFilters = Field(default_factory=GlobalSearchFilters)
    tags: list[RuleTagRef]


class RuleUpdate(BaseModel):
    """The parts of a rule an edit may change.

    The name and the tags, and not the search: what a rule matched is what it
    was created from, and re-pointing it at another search would silently
    change which objects it labels. That is a new rule.

    Both optional, so an edit sends only what it changes. Sending neither
    changes nothing, which the storing version will refuse; today every edit is
    a 404 for want of a rule to apply it to.
    """

    name: str | None = None
    tags: list[RuleTagRef] | None = None


def _validated_name(raw: str) -> str:
    """*raw* trimmed, or a 400 when there is nothing left of it.

    Trimmed before it is checked or stored, as ``gsf.server.tags.router`` does
    with a tag name, so the name a reader sees is the one that was validated.

    No length ceiling, unlike a tag name: the dialog shows no limit and caps
    nothing, so a rejection here would be one a person could not have
    predicted. The column a stored rule lands in is where that bound belongs.
    """
    name = raw.strip()
    if name == "":
        raise HTTPException(status_code=400, detail="Rule name is required")
    return name


def _validated_search_term(raw: str) -> str:
    """*raw* trimmed, held to the floor global search itself applies.

    The same :data:`MIN_SEARCH_LENGTH` rather than a rule-specific one: a rule
    that stored a term the search refuses would be a rule that can never be
    replayed.
    """
    search_term = raw.strip()
    if len(search_term) < MIN_SEARCH_LENGTH:
        raise HTTPException(
            status_code=400,
            detail=f"Rule search term must be at least {MIN_SEARCH_LENGTH} characters",
        )
    return search_term


def _validated_match_option(option: str) -> str:
    """*option*, or a 400 naming the one match global search implements.

    ``contains`` is the only one, and the request model already defaults to it
    -- this catches a caller that sent something else on purpose.
    """
    if option != TEXT_MATCH_CONTAINS:
        raise HTTPException(
            status_code=400,
            detail=(
                f"Unsupported text match option {option!r}; "
                f"expected {TEXT_MATCH_CONTAINS!r}"
            ),
        )
    return option


def _resolved_tags(tags: list[RuleTagRef]) -> list[dict[str, Any]]:
    """The chips for *tags*, or a 404 naming the first id that is not a tag.

    Read rather than taken on trust, which is why a caller sending whole tag
    objects gets the same answer as one sending bare ids. The tags are the half
    of a rule that already has a table, so this is checkable today, and a rule
    applying a tag that does not exist would label nothing while claiming
    otherwise.

    Order follows the request, so the chips come back in the order they were
    picked. Repeats collapse: two clicks on one tag are one intention, the way
    ``attach_tag`` treats labelling something twice.

    One read for the whole set rather than one per id. The tag vocabulary is
    curated in settings and small by design, so reading it whole costs less
    than a round trip per tag.

    An empty list is a 400 -- a rule with no tags applies nothing, which the
    dialog also refuses to submit.
    """
    if not tags:
        raise HTTPException(
            status_code=400, detail="A rule must apply at least one tag"
        )

    known = {tag["id"]: tag for tag in tags_dal.list_tags()}
    chips: list[dict[str, Any]] = []
    seen: set[str] = set()
    for ref in tags:
        tag_id = ref.id
        if tag_id in seen:
            continue
        seen.add(tag_id)
        tag = known.get(tag_id)
        if tag is None:
            # 404 for the reason the tag routes give one: the dialog picked from
            # a list it had already read, so an id that is gone means that list
            # is stale.
            raise HTTPException(status_code=404, detail=f"Tag {tag_id!r} not found")
        chips.append({"id": tag["id"], "name": tag["name"]})
    return chips


def _author(request: Request) -> str:
    """The id of the user saving the rule, from the trusted gateway header.

    A rule is attributed to whoever saved it, so an absent identity is a 401
    rather than a rule owned by nobody. It is never absent on the deployed
    path -- ``frontend/app/api/rules/route.ts`` forwards it for every
    authenticated caller -- so reaching this means somebody called FastAPI
    directly, which is not publicly reachable.

    Resolved with ``required=False`` and refused here so the message names a
    rule; ``resolve_internal_user``'s own 401 talks about conversations.
    """
    user_id = resolve_internal_user(request, required=False)
    if user_id is None:
        raise HTTPException(
            status_code=401, detail="A rule must be attributed to a user"
        )
    return user_id


def _not_stored(rule_id: str) -> HTTPException:
    """The 404 every route that addresses a stored rule answers with.

    One function so the reason is stated once: nothing is stored, so no id can
    name a rule. A 404 rather than a 501 because it is what the storing version
    answers for an id it does not hold, and it is the case a caller has to
    handle either way -- a page reading a rule that is not there behaves the
    same whether it was deleted or never persisted.
    """
    return HTTPException(status_code=404, detail=f"Rule {rule_id!r} not found")


def _created_rule(
    *,
    name: str,
    search_term: str,
    text_match_option: str,
    filters: GlobalSearchFilters,
    tags: list[dict[str, Any]],
    created_by: str,
) -> dict[str, Any]:
    """The rule a create would have stored, shaped as the response carries it.

    The one place this router stands in for the store, and the only thing that
    moves when the store arrives: ``id`` and the timestamps come from here
    instead of from a ``RETURNING`` clause.

    Both timestamps are the same instant, which is what ``created`` and
    ``modified`` mean on a freshly created tag -- they differ only once
    something has edited the row.

    The clock is the application's. ``gsf.dal.tags`` takes its timestamps from
    Postgres, and a stored rule will too, so two rules created against
    different workers cannot be ordered by these the way two tags can. Worth
    knowing for as long as this stands in.
    """
    now = datetime.now(tz=UTC)
    return {
        "id": str(uuid4()),
        "name": name,
        "search_term": search_term,
        "text_match_option": text_match_option,
        "filters": filters.model_dump(),
        "tags": tags,
        "created_by": created_by,
        "created": now,
        "modified": now,
    }


@router.post("/rules", status_code=201, response_model=RuleResponse)
def create_rule(request: Request, body: RuleCreate) -> dict:
    """Save a rule, and answer with it.

    Validates the body in full and stores nothing -- see the module docstring
    for why that is the shape of this router today. The answer is the rule as a
    stored one would read back, so the dialog that posted it needs no second
    request and no change once rules persist.

    400 for a body that could never be a rule: a blank name, a search term
    shorter than global search accepts, a match option it does not implement,
    or no tags at all. 404 for a tag id that is not a tag. 401 when the request
    carries no identity to attribute the rule to.
    """
    created_by = _author(request)
    return {
        "data": _created_rule(
            name=_validated_name(body.name),
            search_term=_validated_search_term(body.search_term),
            text_match_option=_validated_match_option(body.text_match_option),
            filters=body.filters,
            tags=_resolved_tags(body.tags),
            created_by=created_by,
        )
    }


@router.get("/rules", response_model=RuleListResponse)
def list_rules() -> dict:
    """Return every rule.

    Empty while there is nowhere to keep one. That is the same answer a
    deployment where nobody has created a rule yet would give, so the Rules
    settings page renders its ordinary empty state and needs no special case
    for the store being absent.
    """
    return {"data": [], "count": 0}


@router.get("/rules/{rule_id}", response_model=RuleResponse)
def get_rule(rule_id: str) -> dict:
    """Return one rule.

    404 for every id today -- see :func:`_not_stored`.
    """
    raise _not_stored(rule_id)


@router.patch("/rules/{rule_id}", response_model=RuleResponse)
def update_rule(rule_id: str, body: RuleUpdate) -> dict:
    """Edit a rule's name or the tags it applies.

    Answers with the whole rule rather than an echo of the fields that changed,
    the way renaming a tag does: the edit also advances ``modified``, so the
    row a caller just edited is redrawn from this one response.

    404 for every id today -- see :func:`_not_stored`. The body is declared
    rather than ignored because it is the contract the storing version will
    validate; nothing reads it here, since there is no rule to apply it to.
    """
    raise _not_stored(rule_id)


@router.delete("/rules/{rule_id}", response_model=IdResponse)
def delete_rule(rule_id: str) -> dict:
    """Delete one rule by id.

    404 for every id today -- see :func:`_not_stored`. Which is also what the
    storing version owes an id it does not hold, rather than a silent 204: the
    settings page deletes from a list it has already read, so a missing rule
    means that list is stale and the row would be left on screen with nothing
    to explain it.
    """
    raise _not_stored(rule_id)
