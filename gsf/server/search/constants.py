# SPDX-FileCopyrightText: Copyright (c) 2024-2026, NVIDIA CORPORATION & AFFILIATES.
# All rights reserved.
# SPDX-License-Identifier: Apache-2.0

"""Constants shared by the global-search router and service."""

from __future__ import annotations

# The only ``text_match_option`` global search implements: every token has to
# appear as a substring. Lives here because the request model's default and the
# service's validation have to agree on the wording.
TEXT_MATCH_CONTAINS = "contains"

# Shorter terms are rejected before the database is touched: a one-character
# ``%a%`` matches most of the catalog and is never a useful search.
MIN_SEARCH_LENGTH = 2
