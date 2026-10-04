# SPDX-FileCopyrightText: Copyright (c) 2026, NVIDIA CORPORATION & AFFILIATES.
# All rights reserved.
# SPDX-License-Identifier: Apache-2.0

"""The system-managed ``PII`` tag can be neither deleted nor renamed.

PII detection finds the tag by name on every ingestion pass, so losing it or
its name would split PII labels across two tags. The route is the one gate
every caller -- the settings page and a direct API call alike -- goes through.
"""

from __future__ import annotations

from typing import Any

import pytest
from fastapi import HTTPException

from auto_ontology.server.tags import router
from auto_ontology.server.tags.router import TagUpdate, delete_tag, update_tag


def _tag(name: str) -> dict[str, Any]:
    return {"id": "tag-1", "name": name}


@pytest.fixture
def writes(monkeypatch: pytest.MonkeyPatch) -> list[str]:
    calls: list[str] = []
    monkeypatch.setattr(
        router.dal, "delete_tag", lambda tag_id: calls.append("delete") or True
    )
    monkeypatch.setattr(
        router.dal,
        "update_tag",
        lambda **kwargs: calls.append("update") or _tag(kwargs["name"]),
    )
    monkeypatch.setattr(router, "resolve_internal_user", lambda *a, **k: None)
    return calls


@pytest.mark.parametrize("name", ["PII", "pii", " Pii "])
def test_the_pii_tag_cannot_be_deleted(
    monkeypatch: pytest.MonkeyPatch, writes: list[str], name: str
) -> None:
    monkeypatch.setattr(router.dal, "get_tag", lambda tag_id: _tag(name))

    with pytest.raises(HTTPException) as refused:
        delete_tag("tag-1")

    assert refused.value.status_code == 403
    assert writes == []


def test_the_pii_tag_cannot_be_renamed(
    monkeypatch: pytest.MonkeyPatch, writes: list[str]
) -> None:
    monkeypatch.setattr(router.dal, "get_tag", lambda tag_id: _tag("PII"))

    with pytest.raises(HTTPException) as refused:
        update_tag(None, "tag-1", TagUpdate(name="Personal"))  # type: ignore[arg-type]

    assert refused.value.status_code == 403
    assert writes == []


def test_an_ordinary_tag_is_still_deleted(
    monkeypatch: pytest.MonkeyPatch, writes: list[str]
) -> None:
    monkeypatch.setattr(router.dal, "get_tag", lambda tag_id: _tag("GDPR"))

    assert delete_tag("tag-1") == {"data": {"id": "tag-1"}}
    assert writes == ["delete"]


def test_an_unknown_tag_is_still_a_404(
    monkeypatch: pytest.MonkeyPatch, writes: list[str]
) -> None:
    monkeypatch.setattr(router.dal, "get_tag", lambda tag_id: None)

    with pytest.raises(HTTPException) as refused:
        delete_tag("tag-1")

    assert refused.value.status_code == 404
    assert writes == []
