# SPDX-FileCopyrightText: Copyright (c) 2024-2026, NVIDIA CORPORATION & AFFILIATES.
# All rights reserved.
# SPDX-License-Identifier: Apache-2.0

import json

import pytest
from pytest import MonkeyPatch

from gsf.connectors import vast_embedding as ve


def test_from_env_reads_json(monkeypatch: MonkeyPatch) -> None:
    monkeypatch.setenv(
        "VAST_EMBEDDING_PARAMS",
        json.dumps(
            {
                "endpoint": "https://host/v1/",
                "model": "m",
                "api_key": "k",
                "dimensions": 4,
            }
        ),
    )
    for var in (
        "VAST_EMBED_ENDPOINT",
        "VAST_EMBED_MODEL",
        "VAST_EMBED_API_KEY",
        "VAST_EMBED_DIMENSIONS",
    ):
        monkeypatch.delenv(var, raising=False)

    params = ve.VastEmbeddingParams.from_env()

    assert params.endpoint == "https://host/v1"  # trailing slash stripped
    assert params.model == "m"
    assert params.dimensions == 4
    assert params.input_type == ""  # omitted by default


def test_from_env_individual_vars_override(monkeypatch: MonkeyPatch) -> None:
    monkeypatch.delenv("VAST_EMBEDDING_PARAMS", raising=False)
    monkeypatch.setenv("VAST_EMBED_ENDPOINT", "https://h/v1")
    monkeypatch.setenv("VAST_EMBED_MODEL", "mdl")
    monkeypatch.setenv("VAST_EMBED_API_KEY", "key")
    monkeypatch.setenv("VAST_EMBED_DIMENSIONS", "8")

    params = ve.VastEmbeddingParams.from_env()

    assert (params.endpoint, params.model, params.dimensions) == (
        "https://h/v1",
        "mdl",
        8,
    )


def test_from_env_missing_raises(monkeypatch: MonkeyPatch) -> None:
    for var in (
        "VAST_EMBEDDING_PARAMS",
        "VAST_EMBED_ENDPOINT",
        "VAST_EMBED_MODEL",
        "VAST_EMBED_API_KEY",
        "VAST_EMBED_DIMENSIONS",
    ):
        monkeypatch.delenv(var, raising=False)
    with pytest.raises(EnvironmentError):
        ve.VastEmbeddingParams.from_env()


def test_vector_type_dimensions() -> None:
    assert (
        ve.vector_type_dimensions("fixed_size_list<item: float not null>[1024]") == 1024
    )
    assert ve.vector_type_dimensions("fixed_size_list<item: float>[128]") == 128
    assert ve.vector_type_dimensions("int64") is None
    assert ve.vector_type_dimensions(None) is None


def test_vector_sql_literal() -> None:
    literal = ve.vector_sql_literal([1.0, 2.5, -3.0], dimensions=3)
    assert literal == "[1,2.5,-3]::VECTOR(FLOAT, 3)"

    with pytest.raises(ValueError):
        ve.vector_sql_literal([1.0, 2.0], dimensions=3)  # length mismatch
    with pytest.raises(ValueError):
        ve.vector_sql_literal([float("nan")])  # non-finite


def test_cosine_distance_expr() -> None:
    expr = ve.cosine_distance_expr("title_vec", [0.1, 0.2])
    assert expr.startswith('array_cosine_distance("title_vec", [')
    assert expr.endswith("]::VECTOR(FLOAT, 2))")


class _FakeResponse:
    def __init__(self, payload: dict) -> None:
        self._payload = payload

    def raise_for_status(self) -> None:
        return None

    def json(self) -> dict:
        return self._payload


def test_embed_query(monkeypatch: MonkeyPatch) -> None:
    params = ve.VastEmbeddingParams(
        endpoint="https://host/v1", model="m", api_key="k", dimensions=3
    )
    captured: dict = {}

    def fake_post(
        url: str, *, json: dict, headers: dict, timeout: float
    ) -> _FakeResponse:
        captured["url"] = url
        captured["json"] = json
        captured["headers"] = headers
        return _FakeResponse({"data": [{"embedding": [0.1, 0.2, 0.3]}]})

    monkeypatch.setattr(ve.httpx, "post", fake_post)

    vector = ve.embed_query("find similar", params)

    assert vector == [0.1, 0.2, 0.3]
    assert captured["url"] == "https://host/v1/embeddings"
    # input_type omitted by default (plain OpenAI-style request).
    assert captured["json"] == {"model": "m", "input": ["find similar"]}
    assert captured["headers"]["Authorization"] == "Bearer k"


def test_embed_query_includes_input_type_when_set(monkeypatch: MonkeyPatch) -> None:
    params = ve.VastEmbeddingParams(
        endpoint="https://host/v1",
        model="m",
        api_key="k",
        dimensions=3,
        input_type="query",
    )
    captured: dict = {}

    def fake_post(url, *, json, headers, timeout):
        captured["json"] = json
        return _FakeResponse({"data": [{"embedding": [0.1, 0.2, 0.3]}]})

    monkeypatch.setattr(ve.httpx, "post", fake_post)
    ve.embed_query("q", params)
    assert captured["json"]["input_type"] == "query"


def test_embed_query_dimension_mismatch(monkeypatch: MonkeyPatch) -> None:
    params = ve.VastEmbeddingParams(
        endpoint="https://host/v1", model="m", api_key="k", dimensions=5
    )
    monkeypatch.setattr(
        ve.httpx,
        "post",
        lambda *a, **k: _FakeResponse({"data": [{"embedding": [0.1, 0.2]}]}),
    )
    with pytest.raises(RuntimeError, match="dimension"):
        ve.embed_query("q", params)


def test_embed_query_empty_text() -> None:
    params = ve.VastEmbeddingParams(
        endpoint="https://host/v1", model="m", api_key="k", dimensions=3
    )
    with pytest.raises(ValueError):
        ve.embed_query("   ", params)
