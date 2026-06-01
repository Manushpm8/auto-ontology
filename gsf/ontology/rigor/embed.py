"""Embed Rigor ontology elements into pgvector for semantic search.

Converts BusinessTerms, Attributes, and Metrics from a CoreOntology into
composite text documents, embeds them via the NVIDIA NIM API, and writes
the vectors into a dedicated pgvector collection (``rigor_ontology``).
"""

from __future__ import annotations

import logging
import os
import time
from collections import defaultdict

import pandas as pd
from nemo_retriever.params import EmbedParams
from nemo_retriever.text_embed.runtime import embed_text_main_text_embed
from nemo_retriever.vdb import IngestVdbOperator

from gsf.vdb.config import get_postgres_connection_string
from gsf.vdb.postgres import PostgresVDB
from gsf.ontology.rigor.models import CoreOntology

logger = logging.getLogger(__name__)

RIGOR_VDB_COLLECTION = "rigor_ontology"

_NVIDIA_API_KEY = os.environ.get("NVIDIA_API_KEY", "")
_EMBED_ENDPOINT = os.environ.get(
    "EMBED_ENDPOINT", "https://integrate.api.nvidia.com/v1"
)
_EMBED_MODEL = os.environ.get("EMBED_MODEL", "nvidia/llama-nemotron-embed-1b-v2")


def _get_embed_params() -> EmbedParams:
    return EmbedParams(
        embed_invoke_url=_EMBED_ENDPOINT,
        model_name=_EMBED_MODEL,
        api_key=_NVIDIA_API_KEY,
        embed_modality="text",
    )


def _term_text(term_name: str, description: str, attr_names: list[str]) -> str:
    """Composite text for a BusinessTerm embedding."""
    parts = [f"business_term: {term_name}. {description}"]
    if attr_names:
        parts.append(f"Attributes: {', '.join(attr_names)}")
    return ". ".join(parts)


def _attribute_text(
    name: str,
    datatype: str,
    term_name: str,
    description: str | None,
    usage_hint: str | None,
) -> str:
    """Composite text for an Attribute embedding."""
    parts = [f"attribute: {name} ({datatype}), property of {term_name}"]
    if description:
        parts.append(description)
    if usage_hint:
        parts.append(usage_hint)
    return ". ".join(parts)


def _metric_text(
    name: str,
    expression: str,
    aggregation_type: str,
    source_tables: list[str],
) -> str:
    """Composite text for a Metric embedding."""
    tables = ", ".join(source_tables) if source_tables else "unknown"
    return f"metric: {name}. {expression} ({aggregation_type}) over {tables}"


def _build_records(
    ontology: CoreOntology,
    database_name: str,
) -> list[dict]:
    """Convert ontology elements into NeMo-compatible record dicts."""
    attrs_by_term: dict[str, list[str]] = defaultdict(list)
    for attr in ontology.attributes:
        attrs_by_term[attr.term_name].append(attr.name)

    records: list[dict] = []

    for bt in ontology.business_terms:
        text = _term_text(bt.name, bt.description, attrs_by_term.get(bt.name, []))
        source_tables = sorted({p.source_table for p in bt.provenance})
        meta = {
            "label": "BusinessTerm",
            "name": bt.name,
            "source_tables": source_tables,
            "database_name": database_name,
        }
        records.append(
            {
                "text": text,
                "_embed_modality": "text",
                "path": f"rigor:BusinessTerm:{bt.name}",
                "page_number": -1,
                "metadata": {**meta, "content_metadata": dict(meta)},
            }
        )

    for attr in ontology.attributes:
        text = _attribute_text(
            attr.name,
            attr.datatype,
            attr.term_name,
            attr.description,
            attr.usage_hint,
        )
        meta = {
            "label": "Attribute",
            "name": attr.name,
            "term_name": attr.term_name,
            "source_column": attr.source_column,
            "source_table": attr.provenance.source_table,
            "database_name": database_name,
        }
        records.append(
            {
                "text": text,
                "_embed_modality": "text",
                "path": f"rigor:Attribute:{attr.term_name}.{attr.name}",
                "page_number": -1,
                "metadata": {**meta, "content_metadata": dict(meta)},
            }
        )

    for m in ontology.metrics:
        text = _metric_text(
            m.name,
            m.expression,
            m.aggregation_type.value,
            m.source_tables,
        )
        meta = {
            "label": "Metric",
            "name": m.name,
            "source_tables": m.source_tables,
            "database_name": database_name,
        }
        records.append(
            {
                "text": text,
                "_embed_modality": "text",
                "path": f"rigor:Metric:{m.name}",
                "page_number": -1,
                "metadata": {**meta, "content_metadata": dict(meta)},
            }
        )

    return records


def embed_ontology(ontology: CoreOntology, database_name: str) -> int:
    """Embed all ontology elements into pgvector. Returns row count."""
    records = _build_records(ontology, database_name)
    if not records:
        logger.info("No ontology elements to embed for %r.", database_name)
        return 0

    logger.info("Embedding %d ontology elements for %r …", len(records), database_name)

    df = pd.DataFrame(records)
    params = _get_embed_params()

    start = time.time()
    embedded = embed_text_main_text_embed(
        df,
        model_name=params.model_name,
        embed_invoke_url=params.embed_invoke_url,
        api_key=params.api_key,
        embed_modality=params.embed_modality,
    )

    with_embeddings = [
        row
        for row in embedded.to_dict(orient="records")
        if (row.get("metadata") or {}).get("embedding")
    ]
    if not with_embeddings:
        raise RuntimeError(
            f"Embedding produced 0/{len(embedded)} ontology rows with vectors; "
            f"check NIM API errors ({params.embed_invoke_url})."
        )

    vdb = PostgresVDB(
        connection_string=get_postgres_connection_string(),
        collection_name=RIGOR_VDB_COLLECTION,
        database_name=database_name,
    )
    try:
        IngestVdbOperator(vdb=vdb)(with_embeddings)
    finally:
        vdb.close()

    elapsed = time.time() - start
    logger.info(
        "Embedded %d/%d ontology element(s) into %r in %.2fs.",
        len(with_embeddings),
        len(records),
        RIGOR_VDB_COLLECTION,
        elapsed,
    )
    return len(with_embeddings)
