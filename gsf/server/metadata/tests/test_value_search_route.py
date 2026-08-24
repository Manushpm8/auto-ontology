# SPDX-FileCopyrightText: Copyright (c) 2026, NVIDIA CORPORATION & AFFILIATES.
# All rights reserved.
# SPDX-License-Identifier: Apache-2.0

from __future__ import annotations

import pytest
from fastapi import HTTPException
from pydantic import ValidationError

from gsf.server.metadata import router, service


def test_request_accepts_omitted_database_and_trims_inputs() -> None:
    request = router.ValueSearchRequest(
        value="  alex shaked  ",
        description="  a person  ",
    )

    assert request.value == "alex shaked"
    assert request.description == "a person"
    assert request.database_name is None


@pytest.mark.parametrize("field", ["value", "description"])
def test_request_rejects_blank_required_input(field: str) -> None:
    payload = {"value": "Alex", "description": "a person", field: "   "}

    with pytest.raises(ValidationError):
        router.ValueSearchRequest(**payload)


def test_route_returns_data_envelope_and_forwards_optional_database(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    captured: dict = {}

    def fake_lookup(**kwargs: str | None) -> dict:
        captured.update(kwargs)
        return {
            "field": "main.people.person",
            "value": "Alex Shaked Hamelech",
        }

    monkeypatch.setattr(router.dal, "find_column_value", fake_lookup)

    response = router.find_column_value(
        router.ValueSearchRequest(
            value="alex shaked",
            description="a manager",
        )
    )

    assert captured["database_name"] is None
    assert response == {
        "data": {
            "field": "main.people.person",
            "value": "Alex Shaked Hamelech",
        }
    }


@pytest.mark.parametrize(
    ("error", "status_code"),
    [
        (router.dal.ValueSearchError("bad database"), 422),
        (router.dal.ValueSearchUnavailableError("offline"), 503),
    ],
)
def test_route_maps_service_errors(
    monkeypatch: pytest.MonkeyPatch,
    error: RuntimeError,
    status_code: int,
) -> None:
    def fail(**kwargs: str | None) -> dict:
        raise error

    monkeypatch.setattr(router.dal, "find_column_value", fail)

    with pytest.raises(HTTPException) as exc_info:
        router.find_column_value(
            router.ValueSearchRequest(value="Alex", description="a person")
        )

    assert exc_info.value.status_code == status_code


def test_service_passes_optional_database_to_resolver(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    connector = object()
    retriever = object()
    captured: dict = {}
    monkeypatch.setattr(service, "get_connectors", lambda: [connector])
    monkeypatch.setattr(
        service,
        "get_semantic_objects_retriever",
        lambda: retriever,
    )

    def fake_resolver(**kwargs: object) -> dict:
        captured.update(kwargs)
        return {"field": None, "value": None}

    monkeypatch.setattr(service.value_search, "find_column_value", fake_resolver)

    result = service.find_column_value(
        value="Alex",
        description="a person",
        database_name=None,
    )

    assert result == {"field": None, "value": None}
    assert captured["connectors"] == [connector]
    assert captured["retriever"] is retriever
    assert captured["database_name"] is None


def test_service_reports_missing_connectors_as_unavailable(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(service, "get_connectors", lambda: [])

    with pytest.raises(service.ValueSearchUnavailableError):
        service.find_column_value(value="Alex", description="a person")
