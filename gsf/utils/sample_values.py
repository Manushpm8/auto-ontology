# SPDX-FileCopyrightText: Copyright (c) 2024-2026, NVIDIA CORPORATION & AFFILIATES.
# All rights reserved.
# SPDX-License-Identifier: Apache-2.0

"""Helpers for Column.sample_values stored on catalog nodes."""

from __future__ import annotations

import json
from typing import Any


def parse_sample_values(raw: Any) -> list[Any] | None:
    """Normalize Column.sample_values (JSON string or list) to a Python list.

    Profiling persists ``col.sample_values`` as a JSON string (see
    ``store_column_sample_values``); catalog PATCH may store a list. JSON
    scalars (numbers, booleans, strings) are left as their decoded types.
    """
    if raw is None:
        return None
    if isinstance(raw, list):
        return raw
    if isinstance(raw, str):
        try:
            parsed = json.loads(raw)
        except (TypeError, ValueError):
            return None
        if not isinstance(parsed, list):
            return None
        return parsed
    return None
