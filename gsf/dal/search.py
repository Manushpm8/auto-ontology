# SPDX-FileCopyrightText: Copyright (c) 2024-2026, NVIDIA CORPORATION & AFFILIATES.
# All rights reserved.
# SPDX-License-Identifier: Apache-2.0

"""Neo4j fulltext global search across catalog and semantic nodes."""

from __future__ import annotations

import re
from typing import Any

from nemo_retriever.tabular_data.ingestion.model.reserved_words import (
    Edges,
    Labels,
    TableTypes,
)

from gsf.dal.neo4j_tx import graph
from gsf.semantic.constants import (
    LABEL_COLUMN_ATTRIBUTE,
    LABEL_PQL_ANALYSIS,
    LABEL_SQL_ATTRIBUTE,
    LABEL_TERM,
    REL_PROPERTY_OF,
    REL_REPRESENTS,
    SEMANTIC_SOURCE,
)

NAME_INDEX = "gsf_name_index"
DESCRIPTION_INDEX = "gsf_description_index"
LIST_LIMIT = 200

# View is not a Neo4j label (those nodes are ``Table`` + ``table_type``).
# It is a search filter / count key so the UI can tab tables vs views.
SEARCH_TYPE_VIEW = "View"

_SEARCH_INDEX_LABELS = (
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

SEARCH_OBJECT_TYPES = (*_SEARCH_INDEX_LABELS, SEARCH_TYPE_VIEW)

# Lucene treats these as operators; strip them like illumex
# ``filter_special_characters`` so a typed query cannot break the index call.
_LUCENE_STRIP = (
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

_VIEW_TYPES = (TableTypes.VIEW, TableTypes.MATERIALIZED_VIEW)


def is_view_table_type(table_type: str | None) -> bool:
    """True when a Table node's ``table_type`` should surface as a view."""
    return (table_type or "").lower() in {item.lower() for item in _VIEW_TYPES}


def search_object_type(label: str | None, table_type: str | None = None) -> str | None:
    """Map a graph label (+ optional ``table_type``) to a search type key.

    Matches the frontend ``searchObjectTypeFromHit`` helper: every key is a
    Neo4j label except ``View``, which is ``Table`` with a view ``table_type``.
    """
    if not label:
        return None
    if label == Labels.TABLE and is_view_table_type(table_type):
        return SEARCH_TYPE_VIEW
    return label


_LABEL_UNION = "|".join(_SEARCH_INDEX_LABELS)


def filter_special_characters(input_string: str) -> str:
    """Replace Lucene-special characters with spaces."""
    out = input_string
    for ch in _LUCENE_STRIP:
        if ch in out:
            out = out.replace(ch, " ")
    return out


def build_lucene_query(search_term: str) -> str:
    """Build a CONTAINS-style Lucene wildcard query from *search_term*.

    Multi-word input becomes ``*foo* AND *bar*`` (illumex's primary-term
    wrapping, without WordNet synonyms). Returns ``""`` when nothing
    searchable remains after stripping special characters.
    """
    cleaned = " ".join(filter_special_characters(search_term).split())
    if not cleaned:
        return ""
    return " AND ".join(f"*{token}*" for token in cleaned.split())


_WORD_RE = re.compile(r"[a-z0-9]+")


def synonym_word_tokens(search_term: str) -> list[str]:
    """Alphanumeric tokens for whole-word Term.synonyms matching.

    ``unit`` matches synonym ``Business Unit``; ``uni`` does not. Unlike
    name fulltext, synonyms never use Lucene ``*token*`` contains.
    """
    cleaned = filter_special_characters(search_term).lower()
    return _WORD_RE.findall(cleaned)


def synonym_matches_tokens(synonym: str, tokens: list[str]) -> bool:
    """True when every token is a whole word in *synonym* (same as Cypher)."""
    if not tokens:
        return False
    text = synonym.lower()
    return all(
        re.search(rf"(^|[^a-z0-9]){re.escape(tok)}([^a-z0-9]|$)", text) is not None
        for tok in tokens
    )


def _term_is_visible() -> str:
    """Semantic Term with at least one representing table."""
    return f"""
        n.source = $source
        AND EXISTS {{
            MATCH (:{Labels.TABLE})-[:{REL_REPRESENTS}]->(n)
        }}
    """


def _synonym_term_source() -> str:
    """Visible Terms whose stored synonyms contain every search token as a word.

    Neo4j ``=~`` matches the whole string, so the pattern is wrapped in
    ``.*``. Tokens are ``[a-z0-9]+`` only — safe to concatenate into regex.
    """
    return f"""
        MATCH (n:{LABEL_TERM})
        WHERE '{LABEL_TERM}' IN $object_types
          AND size($synonym_tokens) > 0
          AND size(coalesce(n.synonyms, [])) > 0
          AND {_term_is_visible()}
          AND ANY(
              syn IN coalesce(n.synonyms, [])
              WHERE ALL(
                  tok IN $synonym_tokens
                  WHERE toLower(coalesce(syn, '')) =~
                      ('.*(^|[^a-z0-9])' + tok + '([^a-z0-9]|$).*')
              )
          )
        RETURN n
    """


def ensure_search_indexes() -> None:
    """Create fulltext name/description indexes if they are missing.

    Schema changes cannot run inside a data transaction; this uses the
    auto-commit connection. ``IF NOT EXISTS`` makes it cheap to call on
    every boot and before a search.
    """
    conn = graph()
    conn.query_write(
        f"""
        CREATE FULLTEXT INDEX {NAME_INDEX} IF NOT EXISTS
        FOR (n:{_LABEL_UNION})
        ON EACH [n.name]
        """
    )
    conn.query_write(
        f"""
        CREATE FULLTEXT INDEX {DESCRIPTION_INDEX} IF NOT EXISTS
        FOR (n:{_LABEL_UNION})
        ON EACH [n.description]
        """
    )


def _search_params(object_types: set[str]) -> dict[str, Any]:
    """Shared Cypher params: requested types plus index/view/source constants."""
    return {
        "object_types": list(object_types),
        "view_types": list(_VIEW_TYPES),
        "search_labels": list(_SEARCH_INDEX_LABELS),
        "source": SEMANTIC_SOURCE,
    }


def _fulltext_source(include_description: bool) -> str:
    name_call = (
        f"CALL db.index.fulltext.queryNodes('{NAME_INDEX}', $lucene) "
        "YIELD node AS n RETURN n"
    )
    if not include_description:
        return name_call
    desc_call = (
        f"CALL db.index.fulltext.queryNodes('{DESCRIPTION_INDEX}', $lucene) "
        "YIELD node AS n RETURN n"
    )
    return f"{name_call} UNION {desc_call}"


def _count_hit_source(include_description: bool) -> str:
    """Uncapped fulltext hits plus whole-word Term synonym matches."""
    return f"""
        CALL {{
            {_fulltext_source(include_description)}
            UNION
            {_synonym_term_source()}
        }}
    """


def _list_hit_source(include_description: bool) -> str:
    """Top *limit* fulltext hits, unioned with uncapped synonym Terms.

    Synonym-only Terms are not competing for the fulltext LIMIT slot, so a
    query like ``BU`` still surfaces a Term whose name is ``Business Unit``.
    """
    return f"""
        CALL {{
            CALL {{
                {_fulltext_source(include_description)}
            }}
            WITH DISTINCT n
            {_visibility_where()}
            WITH n
            LIMIT $limit
            RETURN n
            UNION
            {_synonym_term_source()}
        }}
    """


def _visibility_where() -> str:
    """Keep hits whose type is in ``$object_types``, plus Term visibility.

    Plain labels are a straight membership check. Term also needs semantic
    ``source`` and a representing table. View is not a label — it is a Table
    whose ``table_type`` is in ``$view_types``.
    """
    is_view = "toLower(coalesce(n.table_type, '')) IN $view_types"
    plain = " OR ".join(
        f"(n:{lab} AND '{lab}' IN $object_types)"
        for lab in _SEARCH_INDEX_LABELS
        if lab not in {Labels.TABLE, LABEL_TERM}
    )
    return f"""
        WHERE (
            {plain}
            OR (
                n:{LABEL_TERM} AND '{LABEL_TERM}' IN $object_types
                AND {_term_is_visible()}
            )
            OR (
                n:{Labels.TABLE} AND '{Labels.TABLE}' IN $object_types
                AND NOT {is_view}
            )
            OR (
                n:{Labels.TABLE} AND '{SEARCH_TYPE_VIEW}' IN $object_types
                AND {is_view}
            )
        )
        """


def _canonical_label() -> str:
    """Pick the searchable Neo4j label; nodes may carry extra labels."""
    return "[lab IN labels(n) WHERE lab IN $search_labels][0]"


def _certified_case() -> str:
    return f"""
        CASE
            WHEN n:{LABEL_TERM} THEN CASE
                WHEN coalesce(n.name_certified, false)
                    AND coalesce(n.description_certified, false)
                    THEN 'certified'
                WHEN coalesce(n.name_certified, false)
                    OR coalesce(n.description_certified, false)
                    THEN 'partial'
                ELSE 'pending'
            END
            WHEN n:{LABEL_COLUMN_ATTRIBUTE} OR n:{LABEL_SQL_ATTRIBUTE}
                THEN n.certified
            ELSE null
        END
        """


def _enrichment_matches() -> str:
    return f"""
        OPTIONAL MATCH (col_table:{Labels.TABLE})-[:{Edges.CONTAINS}]->(n)
            WHERE n:{Labels.COLUMN}
        OPTIONAL MATCH (col_schema:{Labels.SCHEMA})-[:{Edges.CONTAINS}]->(col_table)
            WHERE n:{Labels.COLUMN}
        OPTIONAL MATCH (col_db:{Labels.DB})-[:{Edges.CONTAINS}]->(col_schema)
            WHERE n:{Labels.COLUMN}
        OPTIONAL MATCH (tbl_schema:{Labels.SCHEMA})-[:{Edges.CONTAINS}]->(n)
            WHERE n:{Labels.TABLE}
        OPTIONAL MATCH (tbl_db:{Labels.DB})-[:{Edges.CONTAINS}]->(tbl_schema)
            WHERE n:{Labels.TABLE}
        OPTIONAL MATCH (sch_db:{Labels.DB})-[:{Edges.CONTAINS}]->(n)
            WHERE n:{Labels.SCHEMA}
        OPTIONAL MATCH (n)-[:{REL_PROPERTY_OF}]->(attr_term:{LABEL_TERM})
            WHERE n:{LABEL_COLUMN_ATTRIBUTE} OR n:{LABEL_SQL_ATTRIBUTE}
        // One parent Term is the product model; collect so a stray extra
        // PROPERTY_OF edge cannot duplicate the search row.
        WITH n, col_table, col_schema, col_db, tbl_schema, tbl_db, sch_db,
             head(collect(attr_term)) AS attr_term
        """


def _crumb(alias: str, type_label: str) -> str:
    return (
        f"CASE WHEN {alias} IS NOT NULL "
        f"THEN {{id: {alias}.id, name: {alias}.name, type: '{type_label}'}} END"
    )


def _breadcrumbs_case() -> str:
    return f"""
        CASE
            WHEN n:{Labels.COLUMN} THEN [
                c IN [
                    {_crumb("col_db", Labels.DB)},
                    {_crumb("col_schema", Labels.SCHEMA)},
                    {_crumb("col_table", Labels.TABLE)}
                ] WHERE c IS NOT NULL
            ]
            WHEN n:{Labels.TABLE} THEN [
                c IN [
                    {_crumb("tbl_db", Labels.DB)},
                    {_crumb("tbl_schema", Labels.SCHEMA)}
                ] WHERE c IS NOT NULL
            ]
            WHEN n:{Labels.SCHEMA} THEN [
                c IN [
                    {_crumb("sch_db", Labels.DB)}
                ] WHERE c IS NOT NULL
            ]
            WHEN n:{LABEL_COLUMN_ATTRIBUTE} OR n:{LABEL_SQL_ATTRIBUTE} THEN [
                c IN [
                    {_crumb("attr_term", LABEL_TERM)}
                ] WHERE c IS NOT NULL
            ]
            ELSE []
        END
        """


def fetch_global_search(
    lucene: str,
    object_types: set[str],
    *,
    include_description: bool,
    synonym_tokens: list[str] | None = None,
    limit: int = LIST_LIMIT,
) -> list[dict[str, Any]]:
    """Return enriched global-search hits for *lucene* plus synonym Terms.

    Fulltext is capped at *limit* before the synonym UNION, so alias-only
    Terms are not dropped by the name/description list cap. The service
    re-ranks and slices the combined list back to *limit*.
    """
    params: dict[str, Any] = {
        "lucene": lucene,
        "synonym_tokens": synonym_tokens or [],
        "limit": limit,
        **_search_params(object_types),
    }
    query = f"""
        {_list_hit_source(include_description)}
        WITH DISTINCT n
        {_enrichment_matches()}
        RETURN
            n.id AS id,
            n.name AS name,
            n.description AS description,
            {_canonical_label()} AS label,
            n.table_type AS table_type,
            {_certified_case()} AS certified,
            {_breadcrumbs_case()} AS breadcrumbs,
            coalesce(n.synonyms, []) AS synonyms
        """
    return graph().query_read(query, params)


def count_global_search(
    lucene: str,
    object_types: set[str],
    *,
    include_description: bool,
    synonym_tokens: list[str] | None = None,
) -> dict[str, int]:
    """Return hit counts grouped by object type (no list cap)."""
    params: dict[str, Any] = {
        "lucene": lucene,
        "synonym_tokens": synonym_tokens or [],
        **_search_params(object_types),
    }
    query = f"""
        {_count_hit_source(include_description)}
        WITH DISTINCT n
        {_visibility_where()}
        WITH {_canonical_label()} AS label, n.table_type AS table_type
        RETURN label, table_type, count(*) AS count
        """
    rows = graph().query_read(query, params)
    out: dict[str, int] = {}
    for row in rows:
        key = search_object_type(row.get("label"), row.get("table_type"))
        if not key:
            continue
        out[key] = out.get(key, 0) + int(row["count"])
    return out
