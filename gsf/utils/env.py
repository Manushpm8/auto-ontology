# SPDX-FileCopyrightText: Copyright (c) 2026, NVIDIA CORPORATION & AFFILIATES.
# All rights reserved.
# SPDX-License-Identifier: Apache-2.0

"""Typed environment-variable reads shared across the retrieval/probing agents.

Each helper falls back to *default* both when the variable is unset and when
it holds a value that fails to parse, so a mistyped override degrades to the
built-in behavior instead of crashing the process at import time.
"""

from __future__ import annotations

import os

__all__ = ["read_env_int", "read_env_bool", "read_env_float"]


def read_env_int(name: str, default: int) -> int:
    try:
        return int(os.environ.get(name, str(default)))
    except (TypeError, ValueError):
        return default


def read_env_bool(name: str, default: str = "1") -> bool:
    return os.environ.get(name, default).strip().lower() not in {
        "0",
        "false",
        "no",
        "off",
    }


def read_env_float(name: str, default: float) -> float:
    try:
        return float(os.environ.get(name, str(default)))
    except (TypeError, ValueError):
        return default
