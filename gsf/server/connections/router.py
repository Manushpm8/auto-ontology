# SPDX-FileCopyrightText: Copyright (c) 2024-2026, NVIDIA CORPORATION & AFFILIATES.
# All rights reserved.
# SPDX-License-Identifier: Apache-2.0

"""API routes for catalog connections (``db`` nodes)."""

from __future__ import annotations

from fastapi import APIRouter

from gsf.server.connections import dal

router = APIRouter()


@router.get("/connections")
def list_connections() -> dict:
    """All catalog databases — illumex-style list sourced from ``db`` nodes."""
    rows = dal.list_connections()
    return {"data": rows, "count": len(rows)}
