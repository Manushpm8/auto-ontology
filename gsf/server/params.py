# SPDX-FileCopyrightText: Copyright (c) 2024-2026, NVIDIA CORPORATION & AFFILIATES.
# All rights reserved.
# SPDX-License-Identifier: Apache-2.0

"""Query parameters shared by more than one router.

Paging parameters live in :mod:`gsf.server.pagination`; this module holds the
rest, so a parameter that means the same thing on fifteen endpoints is also
*described* the same way on all fifteen.
"""

from __future__ import annotations

from fastapi import Query

ZONE_IDS_QUERY = Query(
    default=None,
    description=(
        "Zones the caller may read through. Omit for the unfiltered catalog "
        "(admin callers); pass an empty list for a viewer with no zone access, "
        "which returns nothing. Scoping is all-or-nothing per record: anything "
        "that also reaches a table outside these zones is excluded entirely."
    ),
)
