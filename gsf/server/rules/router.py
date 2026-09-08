# SPDX-FileCopyrightText: Copyright (c) 2026, NVIDIA CORPORATION & AFFILIATES.
# All rights reserved.
# SPDX-License-Identifier: Apache-2.0

"""API route handlers for rule-based tags.

A rule is a saved global search plus the tags to apply to everything it
matches -- now, and again as the catalog grows.

Rules are stored: ``rule`` and ``rule__tag`` in ``gsf/dal/schema.py``, written
and read through ``gsf.dal.rules``:

* :func:`create_rule` validates the body, checks the tag ids against the tag
  table, saves the rule, applies it -- see ``service.apply_rule`` -- and answers
  with its id.
* :func:`list_rules` returns a page of rules with their tags, filtered by an
  optional query, and :func:`get_rule` returns one of them by id.
* :func:`update_rule` renames a rule, and renames only -- its search and its
  tags are what it *is*, so changing either is a new rule rather than an edit.
* :func:`delete_rule` removes one rule **and the labels it applied**, which is
  the whole of what un-applying a rule means.

The tag ids are checked here rather than left to the foreign key underneath
``rule__tag``: the dialog picked them from a list it had already read, so an id
that is gone is worth a 404 naming it rather than a constraint violation.
"""

from __future__ import annotations

from typing import Any

from fastapi import APIRouter, HTTPException, Query, Request
from pydantic import BaseModel, ConfigDict, Field

from gsf.dal import rules as rules_dal
from gsf.dal import tags as tags_dal
from gsf.dal.session import write_transaction
from gsf.server.identity import resolve_internal_user
from gsf.server.pagination import LIMIT_QUERY, SKIP_QUERY
from gsf.server.responses import IdResponse, RulePageResponse, RuleResponse
from gsf.server.rules import service as rule_service

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
    only ``id`` is checked: :func:`_resolved_tags` looks it up in the tag table,
    because a rule applying a tag that does not exist would label nothing while
    claiming otherwise.

    ``extra="allow"`` so the fields a caller happens to carry -- a created
    timestamp, a label, whatever its own tag model holds -- survive the round
    trip instead of turning a perfectly good tag into a 422. They come back on
    the created rule exactly as they arrived: the answer is the tag the caller
    posted, so a client redraws its chips from the response without having to
    merge it against the objects it already had.

    ``name`` is the one field this fills in when it is missing, from the tag
    table, so every tag in the answer has something to render.
    """

    model_config = ConfigDict(extra="allow")

    id: str
    name: str | None = None


class RuleCreate(BaseModel):
    """A rule as the create dialog sends it.

    ``search_term``, ``text_match_option`` and ``filters`` are the
    ``/search/global-search`` request being saved, spelled the way that route
    takes it.

    ``filters.objects`` is the search's tab rather than a switch anybody set:
    the dialog opens over a result list, and saving from *Tables* means the rule
    labels tables. Omitted for the *All* tab, and stored omitted -- see
    :func:`create_rule` -- because "no kind was picked" and "the kinds picked
    were none" are not the same rule.
    """

    name: str
    search_term: str
    text_match_option: str = TEXT_MATCH_CONTAINS
    filters: GlobalSearchFilters = Field(default_factory=GlobalSearchFilters)
    tags: list[RuleTagRef]


class RuleUpdate(BaseModel):
    """The part of a rule an edit may change: its name.

    Not the search, and not the tags either. Both of those are what a rule *is*
    -- change one and the rule labels a different set of objects, while the
    labels it already wrote stay where they are, explaining nothing and
    re-applied by nobody. That is a new rule and a deleted one, which is two
    operations this API already has.

    A name, by contrast, is only how the settings list refers to the rule: the
    labels it wrote name it by id, so renaming reaches all of them without a
    single ``tag_target`` row being written.
    """

    name: str


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
    """*tags* as they arrived, or a 404 naming the first id that is not a tag.

    Every field a caller sent is echoed, not just ``id`` and ``name``: the tags
    are the part of the rule the client picked, and answering with a narrower
    copy of them would make the response something it has to merge rather than
    something it can render. Only ``name`` is supplied here, and only when the
    caller left it out, since it is the one field there is nothing to draw
    without.

    The ids are read against the tag table rather than taken on trust. The tags
    are the half of a rule that already has a table, so this is checkable today,
    and a rule applying a tag that does not exist would label nothing while
    claiming otherwise.

    Order follows the request, so the tags come back in the order they were
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
    resolved: list[dict[str, Any]] = []
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
        sent = ref.model_dump()
        if sent.get("name") is None:
            sent["name"] = tag["name"]
        resolved.append(sent)
    return resolved


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


def _no_such_rule(rule_id: str) -> HTTPException:
    """The 404 for a rule id nothing names.

    One function so the three routes that take an id are the same about it: a
    settings page reads a list and then acts on a row from it, so "gone" is the
    answer every one of them owes when that list has gone stale, and a client
    handling it should not have to read three wordings to recognise it.
    """
    return HTTPException(status_code=404, detail=f"Rule {rule_id!r} not found")


def _name_conflict(exc: ValueError) -> HTTPException:
    """The 409 for a name another rule holds, worded by the DAL.

    Only one ``ValueError`` reaches either caller -- ``rules_dal`` raises it for
    the name and nothing else -- so this does not have to tell them apart, and
    the message names the rule the way the tag routes' 409 names the tag.
    """
    return HTTPException(status_code=409, detail=str(exc))


@router.post("/rules", status_code=201, response_model=IdResponse)
def create_rule(request: Request, body: RuleCreate) -> dict:
    """Save a rule, and answer with its id.

    The id alone: the dialog that posted the rule closes on success and the
    settings list re-reads, so a whole rule in the answer would be a copy
    nothing renders. Everything else about a stored rule -- the timestamps, the
    author, the tag names -- comes from :func:`list_rules`, read from the row
    rather than from what was posted.

    The filters are stored as the caller sent them, ``exclude_none`` and not a
    full dump: a filter that was not sent is absent from the stored JSON rather
    than present as ``null``. It reads back the same either way -- the response
    model fills the default -- but the row then says which filters a rule
    actually carries, instead of recording every filter the search has ever
    offered against every rule ever saved.

    Saving the rule also *applies* it: the search is replayed and the tags are
    attached to everything it matches, recorded as this rule's doing so a tag's
    page can name the rule that labelled each object. One transaction with the
    insert, so a rule that could not label the catalog is not left saved and
    inert -- the create either produces a rule with its labels or nothing at
    all. See ``rules.service.apply_rule``.

    400 for a body that could never be a rule: a blank name, a search term
    shorter than global search accepts, a match option it does not implement,
    or no tags at all. 404 for a tag id that is not a tag. 409 for a name
    another rule already holds, as renaming answers. 401 when the request
    carries no identity to attribute the rule to.
    """
    created_by = _author(request)
    name = _validated_name(body.name)
    search_term = _validated_search_term(body.search_term)
    text_match_option = _validated_match_option(body.text_match_option)
    filters = body.filters.model_dump(exclude_none=True)
    tags = _resolved_tags(body.tags)

    with write_transaction():
        try:
            rule_id = rules_dal.create_rule(
                name=name,
                search_term=search_term,
                text_match_option=text_match_option,
                filters=filters,
                tags=tags,
                created_by=created_by,
            )
        except ValueError as exc:
            raise _name_conflict(exc) from exc
        rule_service.apply_rule(
            rule_id=rule_id,
            search_term=search_term,
            text_match_option=text_match_option,
            filters=filters,
            tag_ids=[tag["id"] for tag in tags],
        )
    return {"data": {"id": rule_id}}


@router.get("/rules", response_model=RulePageResponse)
def list_rules(
    q: str | None = Query(
        default=None,
        description=(
            "Case-insensitive substring filter on the rule name or on the name "
            "of a tag it applies."
        ),
    ),
    skip: int = SKIP_QUERY,
    limit: int | None = LIMIT_QUERY,
) -> dict:
    """Return rules, with the tags each applies.

    *q*, when given, keeps the rules whose name contains it, and those applying
    a tag whose name does -- the two things the settings list shows, so a search
    that hides a row can be explained by what is on screen.

    Rules come back ordered by name, case-insensitively, and *skip*/*limit*
    select one page of that order; ``total`` counts every match so a caller
    knows when to stop asking. Omitting *limit* returns every matching rule.

    Empty for a deployment where nobody has saved one, which the Rules settings
    page shows as its ordinary empty state.
    """
    rules = rules_dal.list_rules(search=q, skip=skip, limit=limit)
    # The count is a second read, so it is worth skipping for the request that
    # asked for everything: a whole unpaged list already is its own total.
    total = rules_dal.count_rules(search=q) if skip or limit is not None else len(rules)
    return {"data": rules, "count": len(rules), "total": total}


@router.get("/rules/{rule_id}", response_model=RuleResponse)
def get_rule(rule_id: str) -> dict:
    """Return one rule, with the tags it applies.

    The same shape one entry of :func:`list_rules` has, read through the same
    columns, so a caller renders a rule the same way whichever it came from.

    404 for an id that is not stored.
    """
    rule = rules_dal.get_rule(rule_id)
    if rule is None:
        raise _no_such_rule(rule_id)
    return {"data": rule}


@router.patch("/rules/{rule_id}", response_model=RuleResponse)
def update_rule(request: Request, rule_id: str, body: RuleUpdate) -> dict:
    """Rename a rule.

    The name is all an edit changes -- see :class:`RuleUpdate` for why the
    search and the tags are not editable. What the rule has already labelled is
    untouched: those rows name the rule by id, so the new name appears against
    every object it labelled under the old one.

    Answers with the whole rule rather than an echo of the name, the way
    renaming a tag does: the edit also advances ``modified`` and records a
    ``modified_by``, so the row the caller just edited is redrawn from this one
    response instead of from a re-read.

    400 for a blank name. 409 for a name another rule holds -- rule names are
    unique, so that the list's own way of referring to a rule identifies one.
    A rename to the rule's own name is not a conflict, and neither is one that
    only changes case, which is how a rule created shouting gets fixed. 404 for
    an id that is not stored -- the settings page edits a row from a list it has
    already read, so a missing rule means that list is stale.

    No 401, unlike the create: an edit is recorded against whoever made it when
    the request says, and a request that names nobody still renames the rule
    somebody else created. ``modified_by`` is nullable for exactly that.
    """
    try:
        rule = rules_dal.update_rule(
            rule_id=rule_id,
            name=_validated_name(body.name),
            modified_by=resolve_internal_user(request, required=False),
        )
    except ValueError as exc:
        raise _name_conflict(exc) from exc
    if rule is None:
        raise _no_such_rule(rule_id)
    return {"data": rule}


@router.delete("/rules/{rule_id}", response_model=IdResponse)
def delete_rule(rule_id: str) -> dict:
    """Delete one rule by id.

    **This also takes back every tag the rule applied.** The labels it wrote
    cascade with it -- see ``rules_dal.delete_rule`` -- while labels people
    applied by hand stay, including on an object the rule had also matched,
    because those rows record no rule to cascade from. A rule goes on labelling
    the catalog as the catalog grows, so leaving its labels behind would leave
    tags nothing on screen could explain and nothing could re-apply.

    404 rather than a silent 204 for an id that is not there: the settings page
    deletes from a list it has already read, so a missing rule means that list
    is stale and the row would be left on screen with nothing to explain it.

    Answers with the id, as deleting a tag does, so a caller has the row it just
    removed without holding on to what it sent.
    """
    if not rules_dal.delete_rule(rule_id):
        raise _no_such_rule(rule_id)
    return {"data": {"id": rule_id}}
