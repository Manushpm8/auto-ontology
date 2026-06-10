"""Inline per-Term embedder used during `visit_enter`."""

from __future__ import annotations

import logging
import os
from dataclasses import dataclass, field
from typing import Any

import pandas as pd
from nemo_retriever.graph import Graph
from nemo_retriever.params import EmbedParams
from nemo_retriever.text_embed.operators import _BatchEmbedActor
from nemo_retriever.vdb import IngestVdbOperator

from gsf.vdb import get_semantic_vdb
from gsf.vdb.postgres import PostgresVDB

logger = logging.getLogger(__name__)


@dataclass
class SemanticEmbedder:
    """Embeds one Term + its ColumnAttributes into the semantic VDB per call."""

    database_name: str
    embed_params: EmbedParams
    vdb: PostgresVDB
    embed_graph: Graph = field(init=False)
    ingest_op: IngestVdbOperator = field(init=False)

    def __post_init__(self) -> None:
        self.embed_graph = Graph() >> _BatchEmbedActor(params=self.embed_params)
        self.ingest_op = IngestVdbOperator(vdb=self.vdb)

    def embed_term(
        self,
        term: dict[str, Any],
        attrs: list[dict[str, Any]],
    ) -> int:
        """Embed and ingest one Term and its attribute rows in a single batch.

        ``term`` entries: ``{"name", "description"}``.
        ``attrs`` entries: ``{"name", "term_name", "source_column", "description"}``.
        Returns the number of rows actually written to the VDB.
        """
        rows = _build_rows(self.database_name, term, attrs)
        if not rows:
            return 0

        results = self.embed_graph.execute(pd.DataFrame(rows))
        embedded_df = results[0] if results else None
        if embedded_df is None or embedded_df.empty:
            logger.warning(
                "Inline embed produced no rows for Term %s", term.get("name")
            )
            return 0

        with_embeddings = [
            row
            for row in embedded_df.to_dict(orient="records")
            if (row.get("metadata") or {}).get("embedding")
        ]
        if not with_embeddings:
            logger.warning(
                "Inline embed produced 0/%d rows with embeddings for Term %s",
                len(rows),
                term.get("name"),
            )
            return 0

        self.ingest_op(with_embeddings)
        return len(with_embeddings)


def build_semantic_embedder(
    database_name: str,
    *,
    reset: bool,
) -> SemanticEmbedder | None:
    """Construct an embedder bound to the semantic VDB, or None when disabled."""
    api_key = os.environ.get("NVIDIA_API_KEY", "")
    if not api_key:
        logger.warning("NVIDIA_API_KEY not set — semantic VDB embedding disabled")
        return None

    embed_params = EmbedParams(
        embed_invoke_url=os.environ.get(
            "EMBED_ENDPOINT", "https://integrate.api.nvidia.com/v1"
        ),
        model_name=os.environ.get("EMBED_MODEL", "nvidia/llama-nemotron-embed-1b-v2"),
        api_key=api_key,
        embed_modality="text",
    )
    vdb = get_semantic_vdb(database_name=database_name, reset=reset)
    return SemanticEmbedder(
        database_name=database_name,
        embed_params=embed_params,
        vdb=vdb,
    )


def _build_rows(
    database_name: str,
    term: dict[str, Any],
    attrs: list[dict[str, Any]],
) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []

    term_name = term.get("name")
    if term_name:
        text = f"Term: {term_name}. {term.get('description') or ''}".strip()
        path = f"semantic:term:{term_name}"
        fields = {
            "label": "Term",
            "name": term_name,
            "database_name": database_name,
            "source_path": path,
        }
        rows.append(
            {
                "text": text,
                "_embed_modality": "text",
                "path": path,
                "page_number": -1,
                "metadata": {**fields, "content_metadata": dict(fields)},
            }
        )

    for a in attrs:
        attr_name = a.get("name")
        if not attr_name:
            continue
        owner = a.get("term_name") or term_name or ""
        text = (
            f"ColumnAttribute: {attr_name} of Term {owner}. "
            f"{a.get('description') or ''}"
        ).strip()
        path = f"semantic:attr:{owner}:{a.get('source_column')}"
        fields = {
            "label": "ColumnAttribute",
            "name": attr_name,
            "term_name": owner,
            "source_column": a.get("source_column"),
            "database_name": database_name,
            "source_path": path,
        }
        rows.append(
            {
                "text": text,
                "_embed_modality": "text",
                "path": path,
                "page_number": -1,
                "metadata": {**fields, "content_metadata": dict(fields)},
            }
        )

    return rows
