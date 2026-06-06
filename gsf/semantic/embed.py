"""Embed Terms and ColumnAttributes from Neo4j into semantic_layer VDB."""

from __future__ import annotations

import logging
import time

import pandas as pd
from nemo_retriever.params import EmbedParams
from nemo_retriever.text_embed.runtime import embed_text_main_text_embed
from nemo_retriever.vdb import IngestVdbOperator

from gsf.semantic import neo4j_dal

logger = logging.getLogger(__name__)


def embed_semantic_layer(
    database_name: str,
    *,
    embed_params: EmbedParams,
) -> int:
    """Read all semantic nodes from Neo4j and write embeddings to semantic VDB."""
    from gsf.vdb import get_semantic_vdb

    terms, attrs = neo4j_dal.fetch_all_terms_and_attributes()
    rows: list[dict] = []

    for t in terms:
        text = f"Term: {t['name']}. {t.get('description') or ''}".strip()
        path = f"semantic:term:{t['name']}"
        fields = {
            "label": "Term",
            "name": t["name"],
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
        text = (
            f"ColumnAttribute: {a['name']} of Term {a.get('term_name', '')}. "
            f"{a.get('description') or ''}"
        ).strip()
        path = f"semantic:attr:{a.get('term_name')}:{a.get('source_column')}"
        fields = {
            "label": "ColumnAttribute",
            "name": a["name"],
            "term_name": a.get("term_name"),
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

    if not rows:
        logger.warning("No semantic entities to embed in Neo4j graph")
        return 0

    df = pd.DataFrame(rows)
    before = time.time()
    embedded = embed_text_main_text_embed(
        df,
        model_name=embed_params.model_name,
        embed_invoke_url=embed_params.embed_invoke_url,
        api_key=embed_params.api_key,
        embed_modality=embed_params.embed_modality,
    )

    with_embeddings = [
        row
        for row in embedded.to_dict(orient="records")
        if (row.get("metadata") or {}).get("embedding")
    ]
    if not with_embeddings:
        raise RuntimeError(
            f"Embedding produced 0/{len(embedded)} semantic rows with embeddings"
        )

    vdb = get_semantic_vdb(database_name=database_name)
    IngestVdbOperator(vdb=vdb)(with_embeddings)
    logger.info(
        "Embedded %d semantic records in %.2fs",
        len(with_embeddings),
        time.time() - before,
    )
    return len(with_embeddings)
