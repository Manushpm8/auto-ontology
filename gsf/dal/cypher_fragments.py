# SPDX-FileCopyrightText: Copyright (c) 2026, NVIDIA CORPORATION & AFFILIATES.
# All rights reserved.
# SPDX-License-Identifier: Apache-2.0

"""Reusable Cypher expression fragments shared across DAL queries."""

from __future__ import annotations

from gsf.semantic.constants import (
    LABEL_COLUMN_ATTRIBUTE,
    LABEL_TERM,
    REL_HAS_ATTRIBUTE,
    REL_REPRESENTS,
    REL_SEMANTIC_FK,
)


def _attr_description_head(col_var: str, rel: str) -> str:
    """First non-blank ColumnAttribute description via *rel* from *col_var*."""
    return (
        f"head([({col_var})-[:{rel}]->(att:{LABEL_COLUMN_ATTRIBUTE}) "
        'WHERE att.description IS NOT NULL AND trim(att.description) <> "" '
        "| att.description])"
    )


def column_description_expr(col_var: str) -> str:
    """Cypher expression yielding a column's description.

    Prefers the ``Column`` node's own description, then a connected
    ColumnAttribute via ``HAS_ATTRIBUTE``, then via ``SEMANTIC_FK``. Blank
    descriptions are treated as missing so an earlier empty value does not
    mask a later real description.

    *col_var* is the Cypher variable already bound to the ``Column`` node.
    """
    return (
        "coalesce("
        f"CASE WHEN {col_var}.description IS NOT NULL "
        f'AND trim({col_var}.description) <> "" '
        f"THEN {col_var}.description ELSE null END, "
        f"{_attr_description_head(col_var, REL_HAS_ATTRIBUTE)}, "
        f"{_attr_description_head(col_var, REL_SEMANTIC_FK)})"
    )


def table_description_expr(table_var: str) -> str:
    """Cypher expression yielding a table's description.

    Prefers the ``Table`` node's own description; falls back to the Term
    linked via ``REPRESENTS`` (``(table)-[:REPRESENTS]->(:Term)``). Blank
    descriptions are treated as missing so an empty Table description does
    not mask a real Term description.

    *table_var* is the Cypher variable already bound to the ``Table`` node.
    """
    return (
        "coalesce("
        f"CASE WHEN {table_var}.description IS NOT NULL "
        f'AND trim({table_var}.description) <> "" '
        f"THEN {table_var}.description ELSE null END, "
        f"head([({table_var})-[:{REL_REPRESENTS}]->(term:{LABEL_TERM}) "
        'WHERE term.description IS NOT NULL AND trim(term.description) <> "" '
        "| term.description]))"
    )
