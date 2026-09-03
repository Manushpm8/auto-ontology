# SPDX-FileCopyrightText: Copyright (c) 2024-2026, NVIDIA CORPORATION & AFFILIATES.
# All rights reserved.
# SPDX-License-Identifier: Apache-2.0

"""Global-search orchestration: validate, query, rank."""

from __future__ import annotations

from typing import Any

from gsf.catalog.constants import Labels
from gsf.dal import search as search_dal
from gsf.semantic.constants import LABEL_COLUMN_ATTRIBUTE, LABEL_SQL_ATTRIBUTE
from gsf.server.search.constants import MIN_SEARCH_LENGTH, TEXT_MATCH_CONTAINS

_PARENT_FROM_LAST_CRUMB = {Labels.COLUMN, LABEL_COLUMN_ATTRIBUTE, LABEL_SQL_ATTRIBUTE}


class SearchValidationError(ValueError):
    """Caller sent an unsupported match option or object type."""


def resolve_object_types(objects: list[str] | None) -> set[str]:
    """Return the object types to search; empty/None means all of them."""
    allowed = set(search_dal.SEARCH_OBJECT_TYPES)
    if not objects:
        return allowed
    unknown = sorted({item for item in objects if item not in allowed})
    if unknown:
        raise SearchValidationError(
            f"Unsupported search object type(s): {', '.join(unknown)}"
        )
    return set(objects)


def rank_key(search_term: str) -> Any:
    """Sort key: name contains, then synonym whole-word, then shorter names."""
    needle = search_term.lower()

    def _key(item: dict[str, Any]) -> tuple[int, int, str]:
        name = (item.get("name") or "").lower()
        if needle in name:
            bucket = 0
        elif item.get("synonyms"):
            bucket = 1
        else:
            bucket = 2
        return (bucket, len(name), name)

    return _key


def _fit_list_limit(
    items: list[dict[str, Any]], *, search_term: str
) -> list[dict[str, Any]]:
    """Cap the ranked list at ``LIST_LIMIT`` without dropping synonym-only Terms.

    Fulltext already filled the 200 slots with name/description hits, so a
    Term that matched only via ``synonyms`` would otherwise be sliced away
    on the All tab even though the count tab still includes it.
    """
    key = rank_key(search_term)
    items.sort(key=key)
    limit = search_dal.LIST_LIMIT
    if len(items) <= limit:
        return items
    kept = items[:limit]
    kept_ids = {item["id"] for item in kept}
    extras = [
        item
        for item in items[limit:]
        if item.get("synonyms") and item["id"] not in kept_ids
    ]
    if not extras:
        return kept
    replaceable = [index for index, item in enumerate(kept) if not item.get("synonyms")]
    for extra in extras:
        if not replaceable:
            break
        kept[replaceable.pop()] = extra
    kept.sort(key=key)
    return kept


def _normalize_crumb(crumb: dict[str, Any]) -> dict[str, Any] | None:
    if not crumb.get("name"):
        return None
    out: dict[str, Any] = {
        "name": crumb.get("name"),
        "type": crumb.get("type"),
    }
    crumb_id = crumb.get("id")
    if crumb_id:
        out["id"] = crumb_id
    return out


def _matching_synonyms(row: dict[str, Any], synonym_tokens: list[str]) -> list[str]:
    out: list[str] = []
    for raw in row.get("synonyms") or []:
        if not isinstance(raw, str) or raw in out:
            continue
        if search_dal.synonym_matches_tokens(raw, synonym_tokens):
            out.append(raw)
    return out


def _normalize_item(
    row: dict[str, Any], *, synonym_tokens: list[str]
) -> dict[str, Any]:
    crumbs: list[dict[str, Any]] = []
    for crumb in row.get("breadcrumbs") or []:
        if not isinstance(crumb, dict):
            continue
        normalized = _normalize_crumb(crumb)
        if normalized is not None:
            crumbs.append(normalized)
    parent_id = None
    if crumbs and row.get("label") in _PARENT_FROM_LAST_CRUMB:
        parent_id = crumbs[-1].get("id")
    return {
        "id": row.get("id"),
        "name": row.get("name"),
        "type": row.get("label"),
        "table_type": row.get("table_type"),
        "description": row.get("description"),
        "certified": row.get("certified"),
        "parent_id": parent_id,
        "breadcrumbs": crumbs,
        "synonyms": _matching_synonyms(row, synonym_tokens),
    }


def _tokens_or_empty(search_term: str) -> list[str] | None:
    stripped = search_term.strip()
    if len(stripped) < MIN_SEARCH_LENGTH:
        return None
    return search_dal.search_tokens(stripped) or None


def _prepare_search(
    *,
    search_term: str,
    text_match_option: str,
    objects: list[str] | None,
) -> tuple[list[str], set[str], str, list[str]] | None:
    """Validate and expand a query. ``None`` means nothing searchable."""
    if text_match_option != TEXT_MATCH_CONTAINS:
        raise SearchValidationError(
            f"Unsupported text_match_option {text_match_option!r}; "
            f"only {TEXT_MATCH_CONTAINS!r} is implemented"
        )
    tokens = _tokens_or_empty(search_term)
    if tokens is None:
        return None
    types = resolve_object_types(objects)
    stripped = search_term.strip()
    return tokens, types, stripped, search_dal.synonym_word_tokens(stripped)


def global_search(
    *,
    search_term: str,
    text_match_option: str,
    objects: list[str] | None,
    include_description: bool,
) -> dict[str, Any]:
    """Run the list path: fulltext + enrichment, capped and ranked."""
    prepared = _prepare_search(
        search_term=search_term,
        text_match_option=text_match_option,
        objects=objects,
    )
    if prepared is None:
        return {"data": [], "count": 0}

    tokens, types, stripped, synonym_tokens = prepared
    rows = search_dal.fetch_global_search(
        tokens,
        types,
        include_description=include_description,
        synonym_tokens=synonym_tokens,
        limit=search_dal.LIST_LIMIT,
    )
    items = [_normalize_item(row, synonym_tokens=synonym_tokens) for row in rows]
    items = _fit_list_limit(items, search_term=stripped)
    return {"data": items, "count": len(items)}


def global_search_count(
    *,
    search_term: str,
    text_match_option: str,
    objects: list[str] | None,
    include_description: bool,
) -> dict[str, Any]:
    """Run the count path: same match as list, grouped by type, no cap."""
    prepared = _prepare_search(
        search_term=search_term,
        text_match_option=text_match_option,
        objects=objects,
    )
    if prepared is None:
        return {"data": {}}

    tokens, types, _stripped, synonym_tokens = prepared
    counts = search_dal.count_global_search(
        tokens,
        types,
        include_description=include_description,
        synonym_tokens=synonym_tokens,
    )
    return {"data": counts}
