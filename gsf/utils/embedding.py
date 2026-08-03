# SPDX-FileCopyrightText: Copyright (c) 2024-2026, NVIDIA CORPORATION & AFFILIATES.
# All rights reserved.
# SPDX-License-Identifier: Apache-2.0

"""NIM text-embedding config shared by ingest pipelines and the retriever."""

from __future__ import annotations

import logging
from typing import TYPE_CHECKING

from nemo_retriever.common.params.models import EmbedParams

from gsf.utils.model_config import resolve

if TYPE_CHECKING:
    from nemo_retriever.common.vdb.adt_vdb import VDB

logger = logging.getLogger(__name__)

# Remote NIM embedding endpoint — no local GPU required.
# MUST match the model used at ingest time; a mismatch produces garbage results
# or a dimension error from pgvector. Each field falls back to DEFAULT_MODELS_<field>.
_EMBED_ENDPOINT = resolve("EMBED", "ENDPOINT")
_EMBED_MODEL = resolve("EMBED", "MODEL")
_EMBED_API_KEY = resolve("EMBED", "API_KEY")


def get_embed_kwargs() -> dict[str, str]:
    """Keyword args for ``Retriever`` embed configuration."""
    return {
        "model_name": _EMBED_MODEL,
        "embed_invoke_url": _EMBED_ENDPOINT,
        "api_key": _EMBED_API_KEY,
    }


def get_embed_params() -> EmbedParams:
    return EmbedParams(
        embed_invoke_url=_EMBED_ENDPOINT,
        model_name=_EMBED_MODEL,
        api_key=_EMBED_API_KEY,
        embed_modality="text",
    )


def embed_query_texts(texts: list[str]) -> list[list[float]]:
    """Embed query strings in one HTTP round-trip.

    Deduplicates identical strings before calling the endpoint, then expands
    the result so the returned list is aligned with *texts*. Uses
    ``input_type="query"`` so asymmetric embedders match the Retriever path.

    Raises ``RuntimeError`` when any input lacks an embedding.
    """
    import time

    import pandas as pd

    from nemo_retriever.models.inference.runtime import embed_text_main_text_embed
    from nemo_retriever.operators.vdb import query_vectors_from_embedded_dataframe

    if not texts:
        return []

    unique: list[str] = []
    index_of: dict[str, int] = {}
    for text in texts:
        if text not in index_of:
            index_of[text] = len(unique)
            unique.append(text)

    rows = [
        {
            "text": text,
            "_embed_modality": "text",
            "path": f"query:{i}",
            "page_number": -1,
            "metadata": {},
        }
        for i, text in enumerate(unique)
    ]

    before = time.monotonic()
    embedded = embed_text_main_text_embed(
        pd.DataFrame(rows),
        model_name=_EMBED_MODEL,
        embed_invoke_url=_EMBED_ENDPOINT,
        api_key=_EMBED_API_KEY,
        embed_modality="text",
        input_type="query",
    )
    unique_vectors = query_vectors_from_embedded_dataframe(embedded)
    elapsed = time.monotonic() - before

    if len(unique_vectors) != len(unique) or any(not vec for vec in unique_vectors):
        raise RuntimeError(
            f"Query embedding produced {sum(1 for v in unique_vectors if v)}/"
            f"{len(unique)} vectors; check {_EMBED_ENDPOINT}."
        )

    logger.info(
        "Embedded %d unique query text(s) (%d requested) via %s in %.2fs",
        len(unique),
        len(texts),
        _EMBED_MODEL,
        elapsed,
    )
    return [unique_vectors[index_of[text]] for text in texts]


def embed_docs_into_vdb(
    docs: list[dict],
    embed_params: "EmbedParams",
    vdb: "VDB",
    database_name: str | None = None,
) -> int:
    """Embed *docs* and upsert them into *vdb*.

    Each doc must have at least ``id``, ``name``, ``label``, and ``text`` keys
    (the shape returned by ``fetch_sql_attribute_docs`` and
    ``fetch_suggested_sql_attribute_docs``).

    Returns the number of rows successfully embedded and ingested.
    Raises ``RuntimeError`` when the embedding call produces zero embedded rows
    so the caller can decide how to handle the failure.
    """
    import time

    import pandas as pd

    from nemo_retriever.models.inference.runtime import embed_text_main_text_embed
    from nemo_retriever.operators.vdb import IngestVdbOperator

    if not docs:
        return 0

    rows = []
    for item in docs:
        node_id = item.get("id")
        path = f"neo4j:{node_id}" if node_id is not None else "neo4j:unknown"
        tabular_fields = {
            "id": node_id,
            "label": item.get("label", ""),
            "name": item.get("name", ""),
            "source_path": path,
            "database_name": database_name,
        }
        rows.append(
            {
                "text": (item.get("text") or "").strip(),
                "_embed_modality": "text",
                "path": path,
                "page_number": -1,
                "metadata": {
                    **tabular_fields,
                    "content_metadata": dict(tabular_fields),
                },
            }
        )

    before = time.time()
    embedded = embed_text_main_text_embed(
        pd.DataFrame(rows),
        model_name=embed_params.model_name,
        embed_invoke_url=embed_params.embed_invoke_url,
        api_key=embed_params.api_key,
        embed_modality=embed_params.embed_modality,
    )

    with_embeddings = [
        r
        for r in embedded.to_dict(orient="records")
        if (r.get("metadata") or {}).get("embedding")
    ]
    if not with_embeddings:
        raise RuntimeError(
            f"Embedding step produced 0/{len(embedded)} rows with embeddings; "
            f"check upstream embed errors (often a transient "
            f"{embed_params.embed_invoke_url} 5xx)."
        )

    IngestVdbOperator(vdb=vdb)(with_embeddings)
    logger.info(
        "Embedded %d/%d row(s) via %s in %.2fs.",
        len(with_embeddings),
        len(embedded),
        type(vdb).__name__,
        time.time() - before,
    )
    return len(with_embeddings)
