# SPDX-FileCopyrightText: Copyright (c) 2024-2026, NVIDIA CORPORATION & AFFILIATES.
# All rights reserved.
# SPDX-License-Identifier: Apache-2.0

"""Query-side embedding for VAST in-table vector search.

This is intentionally **separate** from the GSF ingestion/retrieval embeddings
(:mod:`gsf.utils.embedding`). VAST vector columns (Arrow type
``fixed_size_list<item: float ...>[N]``) were populated by a specific embedding
model, so a query embedded for server-side cosine search must use *that* model,
not GSF's schema-retrieval model. Configure it independently via the
``VAST_EMBEDDING_PARAMS`` env var.

Server-side search then follows the ``vectors_wiki.py`` shape::

    SELECT ..., array_cosine_distance("<vec_col>", <literal>) AS distance
    FROM "<bucket>/<schema>"."<table>"
    ORDER BY distance ASC
    LIMIT <k>

where ``<literal>`` is the query vector rendered by :func:`vector_sql_literal`.
"""

from __future__ import annotations

import json
import logging
import math
import os
import re
from dataclasses import dataclass

import httpx

logger = logging.getLogger(__name__)

#: Primary config env var — a JSON object. Individual ``VAST_EMBED_*`` vars are
#: honoured as a fallback so the config can also be set field-by-field (handy in
#: Helm ``--set`` / k8s env).
_ENV_JSON = "VAST_EMBEDDING_PARAMS"
_ENV_ENDPOINT = "VAST_EMBED_ENDPOINT"
_ENV_MODEL = "VAST_EMBED_MODEL"
_ENV_API_KEY = "VAST_EMBED_API_KEY"
_ENV_DIMENSIONS = "VAST_EMBED_DIMENSIONS"
_ENV_INPUT_TYPE = "VAST_EMBED_INPUT_TYPE"

# Matches ``fixed_size_list<item: float not null>[1024]`` (and variants) and
# captures the dimensionality. Used to detect vector columns during candidate
# selection.
_VECTOR_TYPE_RE = re.compile(
    r"fixed_size_list\s*<\s*item\s*:\s*float[^>]*>\s*\[\s*(\d+)\s*\]",
    re.IGNORECASE,
)

_KEY_ERROR = (
    f"{_ENV_JSON} is not set. Provide a JSON object with the VAST embedding "
    "model that produced the stored vectors, e.g.\n\n"
    f'    export {_ENV_JSON}=\'{{"endpoint": "https://host/v1", '
    '"model": "nvidia/...", "api_key": "nvapi-...", "dimensions": 1024}\'\n\n'
    f"(or set {_ENV_ENDPOINT}/{_ENV_MODEL}/{_ENV_API_KEY}/{_ENV_DIMENSIONS})"
)


@dataclass(frozen=True)
class VastEmbeddingParams:
    """Connection + model config for the VAST query-embedding endpoint.

    ``endpoint`` is an OpenAI-compatible base URL (``.../v1``); the request is
    posted to ``{endpoint}/embeddings``. ``input_type`` is optional and omitted
    by default (plain OpenAI-style ``{model, input}``, which is what vLLM/TEI-style
    servers expect); set it (e.g. ``"query"``) only for NVIDIA embed NIMs that
    require the asymmetric query/passage flag.
    """

    endpoint: str
    model: str
    api_key: str
    dimensions: int
    input_type: str = ""
    timeout: float = 60.0

    @classmethod
    def from_env(cls) -> "VastEmbeddingParams":
        """Build params from ``VAST_EMBEDDING_PARAMS`` (JSON) or ``VAST_EMBED_*``."""
        data: dict = {}
        raw = os.environ.get(_ENV_JSON, "").strip()
        if raw:
            try:
                data = json.loads(raw)
            except json.JSONDecodeError as exc:
                raise EnvironmentError(f"{_ENV_JSON} is not valid JSON: {exc}") from exc
            if not isinstance(data, dict):
                raise EnvironmentError(f"{_ENV_JSON} must be a JSON object")

        # Individual env vars override / fill in the JSON.
        env_overrides = {
            "endpoint": os.environ.get(_ENV_ENDPOINT),
            "model": os.environ.get(_ENV_MODEL),
            "api_key": os.environ.get(_ENV_API_KEY),
            "dimensions": os.environ.get(_ENV_DIMENSIONS),
            "input_type": os.environ.get(_ENV_INPUT_TYPE),
        }
        for key, value in env_overrides.items():
            if value:
                data[key] = value

        missing = [
            k for k in ("endpoint", "model", "api_key", "dimensions") if not data.get(k)
        ]
        if missing:
            raise EnvironmentError(_KEY_ERROR)

        try:
            dimensions = int(data["dimensions"])
        except (TypeError, ValueError) as exc:
            raise EnvironmentError(
                f"{_ENV_JSON}.dimensions must be an integer"
            ) from exc
        if dimensions <= 0:
            raise EnvironmentError(f"{_ENV_JSON}.dimensions must be positive")

        return cls(
            endpoint=str(data["endpoint"]).rstrip("/"),
            model=str(data["model"]),
            api_key=str(data["api_key"]),
            dimensions=dimensions,
            input_type=str(data.get("input_type", "")),
            timeout=float(data.get("timeout", 60.0)),
        )


def vector_type_dimensions(data_type: str | None) -> int | None:
    """Return the dimensionality of a VAST vector column type, or ``None``.

    ``"fixed_size_list<item: float not null>[1024]"`` -> ``1024``; any
    non-vector type -> ``None``. Use this to detect a vector candidate column.
    """
    if not data_type:
        return None
    match = _VECTOR_TYPE_RE.search(data_type)
    return int(match.group(1)) if match else None


def embed_query(text: str, params: VastEmbeddingParams | None = None) -> list[float]:
    """Embed a single query string with the VAST embedding model.

    Returns a vector of length ``params.dimensions``. Raises ``EnvironmentError``
    when the config is missing, ``ValueError`` for empty input, and
    ``RuntimeError`` for a bad endpoint response or dimension mismatch.
    """
    text = (text or "").strip()
    if not text:
        raise ValueError("cannot embed empty text")
    params = params or VastEmbeddingParams.from_env()

    payload: dict = {"model": params.model, "input": [text]}
    if params.input_type:
        payload["input_type"] = params.input_type
    headers = {
        "Authorization": f"Bearer {params.api_key}",
        "Content-Type": "application/json",
    }
    response = httpx.post(
        f"{params.endpoint}/embeddings",
        json=payload,
        headers=headers,
        timeout=params.timeout,
    )
    response.raise_for_status()
    body = response.json()
    try:
        vector = [float(value) for value in body["data"][0]["embedding"]]
    except (KeyError, IndexError, TypeError) as exc:
        raise RuntimeError(f"unexpected embeddings response shape: {exc}") from exc
    if len(vector) != params.dimensions:
        raise RuntimeError(
            f"embedding dimension {len(vector)} != configured {params.dimensions}"
        )
    return vector


def vector_sql_literal(vector: list[float], dimensions: int | None = None) -> str:
    """Render a float vector as a VAST SQL literal: ``[v1,v2,...]::VECTOR(FLOAT, N)``.

    The stored embedding columns are the engine's native ``VECTOR(FLOAT, N)``
    type (Arrow ``fixed_size_list<item: float ...>[N]``), so the query literal is
    cast to ``VECTOR(FLOAT, N)`` — ``array_cosine_distance`` only binds when the
    argument type matches the column. When *dimensions* is given it is validated
    against the vector length.
    """
    if dimensions is not None and len(vector) != dimensions:
        raise ValueError(f"vector dimension {len(vector)} != {dimensions}")
    if not vector:
        raise ValueError("cannot render an empty vector")
    values: list[str] = []
    for raw_value in vector:
        value = float(raw_value)
        if not math.isfinite(value):
            raise ValueError("vector contains a non-finite value")
        values.append(format(value, ".9g"))
    return f"[{','.join(values)}]::VECTOR(FLOAT, {len(vector)})"


def cosine_distance_expr(column: str, vector: list[float]) -> str:
    """Return ``array_cosine_distance("<column>", <literal>)`` for ORDER BY.

    *column* is quoted as a VAST identifier; smaller distance = closer match, so
    callers order ASC and LIMIT to ``k``.
    """
    quoted = '"' + column.replace('"', '""') + '"'
    return f"array_cosine_distance({quoted}, {vector_sql_literal(vector)})"
