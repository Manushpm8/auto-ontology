# SPDX-FileCopyrightText: Copyright (c) 2026, NVIDIA CORPORATION & AFFILIATES.
# All rights reserved.
# SPDX-License-Identifier: Apache-2.0

"""Persistence operations for automatic PII classification."""

from __future__ import annotations

from auto_ontology.dal import schema as s
from auto_ontology.dal.session import store


def mark_columns_pii_processed(column_ids: list[str]) -> int:
    """Mark successfully classified catalog columns as processed."""

    if not column_ids:
        return 0

    rows = store().query_write(
        s.catalog_column.update()
        .where(
            s.catalog_column.c.id.in_(column_ids),
            s.catalog_column.c.pii_processed.is_(False),
        )
        .values(pii_processed=True)
        .returning(s.catalog_column.c.id)
    )
    return len(rows)
