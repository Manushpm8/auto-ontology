# SPDX-FileCopyrightText: Copyright (c) 2024-2026, NVIDIA CORPORATION & AFFILIATES.
# All rights reserved.
# SPDX-License-Identifier: Apache-2.0

"""Global search across the catalog and semantic tables.

One query term, nine tables, one ranked list. Three decisions shape the rest:

* **It is a substring match, and only that.** ``contains`` is the sole
  ``text_match_option`` the API offers, so typing ``mount`` must find
  ``total_amount``. That is why the indexes behind this are ``pg_trgm`` GIN
  rather than ``tsvector``: full-text search reaches the front of a token and no
  further, so it would silently stop finding the word it sits inside of.
* **Name and description are matched separately, not together.** A hit needs
  *every* token in its name, or *every* token in its description -- never some
  of each. Splitting a search across two fields is how "revenue report" starts
  matching a column named ``revenue`` on a table described as a report.
* **Synonyms match whole words instead.** ``unit`` finds the Term whose synonym
  is ``Business Unit``; ``uni`` does not. Substring-matching an alias list
  produces noise the user cannot see the cause of, because the alias that
  matched is not the text on screen.

The label vocabulary is deliberate, not a leftover from the graph. ``label`` is
the API's ``type`` field and the key the frontend tabs results by, so ``Term``
and ``ColumnAttribute`` are part of the response contract -- renaming them here
renames them in the UI.
"""

from __future__ import annotations

import re
from typing import Any

from sqlalchemy import (
    ARRAY,
    Boolean,
    Text,
    and_,
    case,
    cast,
    func,
    literal,
    null,
    or_,
    select,
    union,
    union_all,
)

from gsf.catalog.constants import Labels, TableTypes
from gsf.dal import schema as s
from gsf.dal.session import store
from gsf.semantic.constants import (
    LABEL_COLUMN_ATTRIBUTE,
    LABEL_PQL_ANALYSIS,
    LABEL_SQL_ATTRIBUTE,
    LABEL_TERM,
    SEMANTIC_SOURCE,
)

#: The most hits one list call returns. The count path is deliberately uncapped:
#: the tab badges have to report the real total, not the size of the page.
LIST_LIMIT = 200

#: Not a label of its own -- a view is a ``catalog_table`` row whose
#: ``table_type`` says so. It is a filter key and a count bucket, which is what
#: lets the UI tab tables and views apart while both come from one table.
SEARCH_TYPE_VIEW = "View"

#: Every label that maps to a table of its own, in the order the UI tabs them.
_SEARCH_LABELS = (
    Labels.DB,
    Labels.SCHEMA,
    Labels.TABLE,
    Labels.COLUMN,
    Labels.CUSTOM_ANALYSIS,
    LABEL_TERM,
    LABEL_COLUMN_ATTRIBUTE,
    LABEL_SQL_ATTRIBUTE,
    LABEL_PQL_ANALYSIS,
)

SEARCH_OBJECT_TYPES = (*_SEARCH_LABELS, SEARCH_TYPE_VIEW)

#: Which table each label reads from. ``View`` is absent for the reason given on
#: :data:`SEARCH_TYPE_VIEW`; it shares ``catalog_table`` with ``Table``.
SEARCH_TABLES = {
    Labels.DB: s.catalog_database,
    Labels.SCHEMA: s.catalog_schema,
    Labels.TABLE: s.catalog_table,
    Labels.COLUMN: s.catalog_column,
    Labels.CUSTOM_ANALYSIS: s.custom_analysis,
    LABEL_TERM: s.term,
    LABEL_COLUMN_ATTRIBUTE: s.column_attribute,
    LABEL_SQL_ATTRIBUTE: s.sql_attribute,
    LABEL_PQL_ANALYSIS: s.pql_analysis,
}

_VIEW_TYPES = (TableTypes.VIEW, TableTypes.MATERIALIZED_VIEW)

# Punctuation that separates tokens rather than belonging to one. Identifiers
# are the thing being searched, so the set is "everything an identifier does not
# contain" -- note `_` is absent, because splitting `total_amount` in two would
# make it match every table with a total and every table with an amount.
#
# `-` is here so `created-at` finds `created_at`, which only works if the two
# halves are matched independently.
_SEPARATORS = (
    "-",
    "\\",
    "/",
    "#",
    "%",
    "*",
    ",",
    '"',
    "$",
    "&",
    "?",
    "!",
    "@",
    "^",
    "<",
    ">",
    "|",
    "+",
    ":",
    ";",
    "~",
    "(",
    ")",
    "{",
    "}",
    "[",
    "]",
)

#: Escape character for the patterns built in :func:`_like_pattern`.
#:
#: ``LIKE`` has wildcards of its own that a search term must not smuggle in, and
#: ``_`` is the one that matters: it is legal in an identifier *and* a
#: single-character wildcard to Postgres, so an unescaped search for ``order_id``
#: would also match ``orderXid``.
_LIKE_ESCAPE = "\\"

_WORD_RE = re.compile(r"[a-z0-9]+")

# Typed NULLs, so every arm of the UNION agrees on a column's type. Postgres
# rejects a bare NULL in a union branch it cannot infer a type for, and these
# are immutable expressions, so one instance each is enough.
_NULL_TEXT = cast(null(), Text)
_NULL_BOOL = cast(null(), Boolean)
_NULL_TEXT_ARRAY = cast(null(), ARRAY(Text))


def is_view_table_type(table_type: str | None) -> bool:
    """True when a table's ``table_type`` should surface as a view."""
    return (table_type or "").lower() in {item.lower() for item in _VIEW_TYPES}


def search_object_type(label: str | None, table_type: str | None = None) -> str | None:
    """Map a label (plus optional ``table_type``) to a search type key.

    Matches the frontend's ``searchObjectTypeFromHit``: every key is a label
    except ``View``, which is a ``Table`` carrying a view ``table_type``.
    """
    if not label:
        return None
    if label == Labels.TABLE and is_view_table_type(table_type):
        return SEARCH_TYPE_VIEW
    return label


def filter_special_characters(input_string: str) -> str:
    """Replace token separators with spaces."""
    out = input_string
    for char in _SEPARATORS:
        if char in out:
            out = out.replace(char, " ")
    return out


def search_tokens(search_term: str) -> list[str]:
    """The tokens a hit must contain, all of them, lowercased.

    ``created-at`` becomes ``["created", "at"]``. Returns ``[]`` when nothing
    searchable survives, which callers read as "match nothing" rather than
    "match everything" -- an empty token list ANDs to true.
    """
    cleaned = filter_special_characters(search_term).lower()
    return cleaned.split()


def synonym_word_tokens(search_term: str) -> list[str]:
    """Alphanumeric tokens for whole-word ``Term.synonyms`` matching.

    ``unit`` matches the synonym ``Business Unit``; ``uni`` does not. Unlike the
    name and description match, synonyms are never matched on a substring.
    """
    cleaned = filter_special_characters(search_term).lower()
    return _WORD_RE.findall(cleaned)


def synonym_matches_tokens(synonym: str, tokens: list[str]) -> bool:
    """True when every token appears as a whole word in *synonym*.

    The Python twin of the regex :func:`_synonym_exists` sends to Postgres. Both
    exist because the database decides which Terms come back and the service
    decides which of their synonyms to show, and a disagreement between the two
    surfaces as a hit with no visible reason for being there.
    """
    if not tokens:
        return False
    text = synonym.lower()
    return all(
        re.search(rf"(^|[^a-z0-9]){re.escape(tok)}([^a-z0-9]|$)", text) is not None
        for tok in tokens
    )


def _like_pattern(token: str) -> str:
    """``%token%``, with LIKE's wildcards escaped out of *token*."""
    escaped = token
    for char in (_LIKE_ESCAPE, "%", "_"):
        escaped = escaped.replace(char, _LIKE_ESCAPE + char)
    return f"%{escaped}%"


def _contains_all(column, tokens: list[str]):
    """*column* contains every token, case-insensitively."""
    return and_(
        *(column.ilike(_like_pattern(token), escape=_LIKE_ESCAPE) for token in tokens)
    )


def _text_match(table, tokens: list[str], include_description: bool):
    """The whole term in the name, or -- when asked -- the whole term in the
    description.

    ``or_`` of two complete matches, never one match over both columns, for the
    reason in the module docstring.
    """
    match = _contains_all(table.c.name, tokens)
    if not include_description:
        return match
    return or_(match, _contains_all(table.c.description, tokens))


def _certified_text():
    """A Term's three-state certification, as the UI badges it."""
    both = and_(s.term.c.name_certified, s.term.c.description_certified)
    either = or_(s.term.c.name_certified, s.term.c.description_certified)
    return case(
        (both, cast(literal("certified"), Text)),
        (either, cast(literal("partial"), Text)),
        else_=cast(literal("pending"), Text),
    )


def _is_view():
    """``catalog_table`` rows the UI shows under the View tab."""
    return func.lower(func.coalesce(s.catalog_table.c.table_type, "")).in_(
        [item.lower() for item in _VIEW_TYPES]
    )


def _hit_select(
    table,
    label: str,
    *,
    table_type=None,
    certified=None,
    certified_flag=None,
    synonyms=None,
):
    """One branch of the union: id, name, description and five common columns.

    Every entity supplies the first three; the rest differ by label and default
    to a typed NULL, so the branches stay union-compatible without each one
    restating the columns it has nothing to say about.
    """
    return select(
        table.c.id.label("id"),
        table.c.name.label("name"),
        table.c.description.label("description"),
        cast(literal(label), Text).label("label"),
        (_NULL_TEXT if table_type is None else table_type).label("table_type"),
        (_NULL_TEXT if certified is None else certified).label("certified"),
        (_NULL_BOOL if certified_flag is None else certified_flag).label(
            "certified_flag"
        ),
        (_NULL_TEXT_ARRAY if synonyms is None else synonyms).label("synonyms"),
    )


def _term_is_visible():
    """A semantic Term with at least one table representing it.

    Terms from other sources, and terms nothing points at, are real rows that
    the catalog UI has no page for -- returning them yields a result that opens
    onto nothing.
    """
    represented = (
        select(literal(1))
        .select_from(s.table__term)
        .where(s.table__term.c.term_id == s.term.c.id)
        .correlate(s.term)
        .exists()
    )
    return and_(s.term.c.source == SEMANTIC_SOURCE, represented)


def _term_select():
    """The Term branch, before its WHERE clause."""
    return _hit_select(
        s.term,
        LABEL_TERM,
        certified=_certified_text(),
        synonyms=s.term.c.synonyms,
    )


def _synonym_exists(synonym_tokens: list[str]):
    """Whole-word match of every token against one of the Term's synonyms.

    All tokens must land in the *same* synonym, which is what makes searching
    "business unit" find the alias ``Business Unit`` rather than any Term with
    one alias mentioning business and another mentioning units.

    Tokens are ``[a-z0-9]+`` by construction, so they are safe to concatenate
    into a pattern -- there is no regex metacharacter left to escape.
    """
    # ``render_derived`` and not ``alias``: the alias alone emits
    # ``AS syn``, which names the table but leaves its one column called ``syn``
    # too, so ``syn.synonym`` is then an undefined column at runtime. This emits
    # ``AS syn(synonym)``, naming both.
    synonym = (
        func.unnest(s.term.c.synonyms).table_valued("synonym").render_derived("syn")
    )
    return (
        select(literal(1))
        .select_from(synonym)
        .where(
            and_(
                *(
                    func.lower(synonym.c.synonym).op("~")(
                        f"(^|[^a-z0-9]){token}([^a-z0-9]|$)"
                    )
                    for token in synonym_tokens
                )
            )
        )
        .correlate(s.term)
        .exists()
    )


def _synonym_term_select(synonym_tokens: list[str], object_types: set[str]):
    """Visible Terms reached only by their synonyms, or ``None``.

    Kept out of the main union so it is not competing for the list cap: a Term
    whose *name* looks nothing like the query is exactly the hit a user typing
    an alias is looking for, and it must not be sliced away by 200 name matches.
    """
    if LABEL_TERM not in object_types or not synonym_tokens:
        return None
    return _term_select().where(_term_is_visible(), _synonym_exists(synonym_tokens))


def _table_where(tokens: list[str], object_types: set[str], include_description: bool):
    """The ``catalog_table`` filter, which serves two tabs from one table."""
    wants_table = Labels.TABLE in object_types
    wants_view = SEARCH_TYPE_VIEW in object_types
    match = _text_match(s.catalog_table, tokens, include_description)
    if wants_table and wants_view:
        return match
    return and_(match, _is_view() if wants_view else ~_is_view())


def _hit_selects(
    tokens: list[str], object_types: set[str], *, include_description: bool
) -> list[Any]:
    """One select per requested entity, ready to be unioned."""
    selects: list[Any] = []

    for label in _SEARCH_LABELS:
        table = SEARCH_TABLES[label]

        if label == Labels.TABLE:
            # Handled below with View, which shares this table.
            continue

        if label not in object_types:
            continue

        if label == LABEL_TERM:
            selects.append(
                _term_select().where(
                    _term_is_visible(),
                    _text_match(s.term, tokens, include_description),
                )
            )
            continue

        if label in (LABEL_COLUMN_ATTRIBUTE, LABEL_SQL_ATTRIBUTE):
            selects.append(
                _hit_select(table, label, certified_flag=table.c.certified).where(
                    _text_match(table, tokens, include_description)
                )
            )
            continue

        selects.append(
            _hit_select(table, label).where(
                _text_match(table, tokens, include_description)
            )
        )

    if Labels.TABLE in object_types or SEARCH_TYPE_VIEW in object_types:
        selects.append(
            _hit_select(
                s.catalog_table,
                Labels.TABLE,
                table_type=s.catalog_table.c.table_type,
            ).where(_table_where(tokens, object_types, include_description))
        )

    return selects


def _crumb(row: dict[str, Any], prefix: str, label: str) -> dict[str, Any] | None:
    """One breadcrumb hop, or ``None`` when the join produced no parent."""
    name = row.get(f"{prefix}_name")
    if not name:
        return None
    return {"id": row.get(f"{prefix}_id"), "name": name, "type": label}


def _catalog_breadcrumbs(ids_by_label: dict[str, list[str]]) -> dict[str, list[dict]]:
    """Ancestor paths for Column, Table/View and Schema hits.

    One query per level rather than per hit: a search returning 200 columns from
    the same table would otherwise walk the same three parents 200 times.
    """
    out: dict[str, list[dict]] = {}

    schema_to_db = s.catalog_schema.join(
        s.catalog_database, s.catalog_database.c.id == s.catalog_schema.c.database_id
    )
    db_crumb = (
        s.catalog_database.c.id.label("db_id"),
        s.catalog_database.c.name.label("db_name"),
    )
    schema_crumb = (
        s.catalog_schema.c.id.label("schema_id"),
        s.catalog_schema.c.name.label("schema_name"),
    )
    table_crumb = (
        s.catalog_table.c.id.label("table_id"),
        s.catalog_table.c.name.label("table_name"),
    )

    column_ids = ids_by_label.get(Labels.COLUMN, [])
    if column_ids:
        for row in store().query_read(
            select(s.catalog_column.c.id, *db_crumb, *schema_crumb, *table_crumb)
            .select_from(
                s.catalog_column.join(
                    s.catalog_table,
                    s.catalog_table.c.id == s.catalog_column.c.table_id,
                ).join(
                    schema_to_db,
                    s.catalog_schema.c.id == s.catalog_table.c.schema_id,
                )
            )
            .where(s.catalog_column.c.id.in_(column_ids))
        ):
            out[row["id"]] = [
                crumb
                for crumb in (
                    _crumb(row, "db", Labels.DB),
                    _crumb(row, "schema", Labels.SCHEMA),
                    _crumb(row, "table", Labels.TABLE),
                )
                if crumb is not None
            ]

    table_ids = ids_by_label.get(Labels.TABLE, [])
    if table_ids:
        for row in store().query_read(
            select(s.catalog_table.c.id, *db_crumb, *schema_crumb)
            .select_from(
                s.catalog_table.join(
                    schema_to_db, s.catalog_schema.c.id == s.catalog_table.c.schema_id
                )
            )
            .where(s.catalog_table.c.id.in_(table_ids))
        ):
            out[row["id"]] = [
                crumb
                for crumb in (
                    _crumb(row, "db", Labels.DB),
                    _crumb(row, "schema", Labels.SCHEMA),
                )
                if crumb is not None
            ]

    schema_ids = ids_by_label.get(Labels.SCHEMA, [])
    if schema_ids:
        for row in store().query_read(
            select(s.catalog_schema.c.id, *db_crumb)
            .select_from(schema_to_db)
            .where(s.catalog_schema.c.id.in_(schema_ids))
        ):
            crumb = _crumb(row, "db", Labels.DB)
            out[row["id"]] = [crumb] if crumb is not None else []

    return out


def _attribute_breadcrumbs(ids_by_label: dict[str, list[str]]) -> dict[str, list[dict]]:
    """The Term each attribute is a property of.

    An attribute linked to several Terms shows the lowest-id one. Which Term
    wins is arbitrary either way; ordering makes it the *same* arbitrary answer
    on every call, instead of one that moves between requests.
    """
    out: dict[str, list[dict]] = {}
    links = (
        (LABEL_COLUMN_ATTRIBUTE, s.column_attribute__term),
        (LABEL_SQL_ATTRIBUTE, s.sql_attribute__term),
    )
    for label, link in links:
        ids = ids_by_label.get(label, [])
        if not ids:
            continue
        for row in store().query_read(
            select(
                link.c.attribute_id,
                s.term.c.id.label("term_id"),
                s.term.c.name.label("term_name"),
            )
            .select_from(link.join(s.term, s.term.c.id == link.c.term_id))
            .where(link.c.attribute_id.in_(ids))
            .order_by(link.c.attribute_id, s.term.c.id)
        ):
            if row["attribute_id"] in out:
                continue
            crumb = _crumb(row, "term", LABEL_TERM)
            out[row["attribute_id"]] = [crumb] if crumb is not None else []
    return out


def _with_breadcrumbs(rows: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Attach each hit's ancestor path, and flatten the two certified columns.

    The union carries certification in two columns because a Term's is a
    three-state string and an attribute's is a boolean, and one column cannot be
    both. Callers see the single ``certified`` field the API declares.
    """
    ids_by_label: dict[str, list[str]] = {}
    for row in rows:
        ids_by_label.setdefault(row["label"], []).append(row["id"])

    crumbs = _catalog_breadcrumbs(ids_by_label) | _attribute_breadcrumbs(ids_by_label)

    out: list[dict[str, Any]] = []
    for row in rows:
        certified = row["certified"]
        if certified is None:
            certified = row["certified_flag"]
        out.append(
            {
                "id": row["id"],
                "name": row["name"],
                "description": row["description"],
                "label": row["label"],
                "table_type": row["table_type"],
                "certified": certified,
                "breadcrumbs": crumbs.get(row["id"], []),
                "synonyms": row["synonyms"] or [],
            }
        )
    return out


def fetch_global_search(
    tokens: list[str],
    object_types: set[str],
    *,
    include_description: bool,
    synonym_tokens: list[str] | None = None,
    limit: int = LIST_LIMIT,
) -> list[dict[str, Any]]:
    """Enriched hits for *tokens*, plus every Term reached by its synonyms.

    Two statements rather than one: the text match is capped at *limit*, and the
    synonym match is not, so an alias-only Term survives a page full of name
    matches. The service re-ranks the combination and cuts it back to *limit*.
    """
    if not tokens or not object_types:
        return []

    selects = _hit_selects(
        tokens, object_types, include_description=include_description
    )
    # ``union_all``: each branch reads a different table, so no row can appear
    # in two of them, and paying for a DISTINCT over 200 rows with an array
    # column buys nothing. The synonym branch below *can* overlap, which is why
    # the ids are deduplicated afterwards rather than not at all.
    rows: list[dict[str, Any]] = []
    if selects:
        rows = store().query_read(union_all(*selects).limit(limit))

    synonym_select = _synonym_term_select(synonym_tokens or [], object_types)
    if synonym_select is not None:
        rows += store().query_read(synonym_select)

    seen: set[str] = set()
    unique = [row for row in rows if not (row["id"] in seen or seen.add(row["id"]))]
    return _with_breadcrumbs(unique)


def count_global_search(
    tokens: list[str],
    object_types: set[str],
    *,
    include_description: bool,
    synonym_tokens: list[str] | None = None,
) -> dict[str, int]:
    """Hit counts by object type, uncapped.

    ``UNION`` rather than ``UNION ALL``: a Term matching both its name and a
    synonym is one hit, and counting it twice puts a number on the tab that the
    list beneath it cannot produce.
    """
    if not tokens or not object_types:
        return {}

    selects = _hit_selects(
        tokens, object_types, include_description=include_description
    )
    synonym_select = _synonym_term_select(synonym_tokens or [], object_types)
    if synonym_select is not None:
        selects.append(synonym_select)
    if not selects:
        return {}

    hits = union(*selects).subquery()
    rows = store().query_read(
        select(hits.c.label, hits.c.table_type, func.count().label("count")).group_by(
            hits.c.label, hits.c.table_type
        )
    )

    out: dict[str, int] = {}
    for row in rows:
        key = search_object_type(row["label"], row["table_type"])
        if not key:
            continue
        out[key] = out.get(key, 0) + int(row["count"])
    return out
