"""Embed semantic layer elements into pgvector for search.

Converts Terms, Attributes, and Metrics from a CoreOntology into composite
text documents and writes vectors to the ``semantic_layer`` collection.
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

from gsf.ontology.rigor.models import CoreOntology, attribute_neo4j_label
from gsf.vdb import SEMANTIC_VDB_COLLECTION, get_semantic_vdb

logger = logging.getLogger(__name__)

RIGOR_VDB_COLLECTION = SEMANTIC_VDB_COLLECTION

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
    parts = [f"term: {term_name}. {description}"]
    if attr_names:
        parts.append(f"Attributes: {', '.join(attr_names)}")
    return ". ".join(parts)


def _attribute_text(
    name: str,
    attribute_type: str,
    datatype: str,
    term_name: str,
    description: str | None,
    definition: str | None,
    usage_hint: str | None,
) -> str:
    parts = [
        f"{attribute_neo4j_label(attribute_type)}: {name} ({datatype}), "
        f"property of {term_name}"
    ]
    if description:
        parts.append(description)
    if definition:
        parts.append(f"definition: {definition}")
    if usage_hint:
        parts.append(usage_hint)
    return ". ".join(parts)


def _metric_text(
    name: str,
    expression: str,
    aggregation_type: str,
    source_tables: list[str],
) -> str:
    tables = ", ".join(source_tables) if source_tables else "unknown"
    return f"metric: {name}. {expression} ({aggregation_type}) over {tables}"


def _role_text(source: str, target: str, role_name: str) -> str:
    return f"role: {source} {role_name} {target}"


def _build_records(
    ontology: CoreOntology,
    database_name: str,
    schema_name: str,
) -> list[dict]:
    attrs_by_term: dict[str, list[str]] = defaultdict(list)
    for attr in ontology.attributes:
        attrs_by_term[attr.term_name].append(attr.name)

    records: list[dict] = []

    for bt in ontology.business_terms:
        text = _term_text(bt.name, bt.description, attrs_by_term.get(bt.name, []))
        source_tables = sorted({p.source_table for p in bt.provenance})
        node_id = bt.id or f"semantic:Term:{bt.name}"
        meta = {
            "id": node_id,
            "label": "Term",
            "legacy_label": "BusinessTerm",
            "name": bt.name,
            "source_tables": source_tables,
            "database_name": database_name,
            "schema_name": schema_name,
        }
        records.append(
            {
                "text": text,
                "_embed_modality": "text",
                "path": node_id,
                "page_number": -1,
                "metadata": {**meta, "content_metadata": dict(meta)},
            }
        )

    for attr in ontology.attributes:
        text = _attribute_text(
            attr.name,
            attr.attribute_type,
            attr.datatype,
            attr.term_name,
            attr.description,
            attr.definition or attr.formula,
            attr.usage_hint,
        )
        node_id = (
            attr.id
            or f"semantic:{attribute_neo4j_label(attr.attribute_type)}:{attr.term_name}.{attr.name}"
        )
        neo4j_label = attribute_neo4j_label(attr.attribute_type)
        meta = {
            "id": node_id,
            "label": neo4j_label,
            "legacy_label": "Attribute",
            "attribute_type": attr.attribute_type,
            "name": attr.name,
            "term_name": attr.term_name,
            "source_column": attr.source_column,
            "source_table": attr.provenance.source_table,
            "database_name": database_name,
            "schema_name": schema_name,
        }
        records.append(
            {
                "text": text,
                "_embed_modality": "text",
                "path": node_id,
                "page_number": -1,
                "metadata": {**meta, "content_metadata": dict(meta)},
            }
        )

    for op in ontology.object_properties:
        if op.relation_kind != "role":
            continue
        text = _role_text(op.source_term, op.target_term, op.name)
        node_id = f"semantic:Role:{op.source_term}.{op.name}.{op.target_term}"
        meta = {
            "id": node_id,
            "label": "Role",
            "name": op.name,
            "source_term": op.source_term,
            "target_term": op.target_term,
            "database_name": database_name,
            "schema_name": schema_name,
        }
        records.append(
            {
                "text": text,
                "_embed_modality": "text",
                "path": node_id,
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
        node_id = m.id or f"semantic:Metric:{m.name}"
        meta = {
            "id": node_id,
            "label": "Metric",
            "name": m.name,
            "source_tables": m.source_tables,
            "database_name": database_name,
            "schema_name": schema_name,
        }
        records.append(
            {
                "text": text,
                "_embed_modality": "text",
                "path": node_id,
                "page_number": -1,
                "metadata": {**meta, "content_metadata": dict(meta)},
            }
        )

    return records


def embed_ontology(
    ontology: CoreOntology,
    database_name: str,
    schema_name: str | None = None,
) -> int:
    """Embed semantic layer elements into pgvector. Returns row count."""
    if schema_name is None:
        schema_name = database_name
    records = _build_records(ontology, database_name, schema_name)
    if not records:
        logger.info("No ontology elements to embed for %r.", database_name)
        return 0

    logger.info("Embedding %d semantic elements for %r …", len(records), database_name)

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
            f"Embedding produced 0/{len(embedded)} semantic rows with vectors; "
            f"check NIM API errors ({params.embed_invoke_url})."
        )

    vdb = get_semantic_vdb(database_name=database_name)
    try:
        IngestVdbOperator(vdb=vdb)(with_embeddings)
    finally:
        vdb.close()

    elapsed = time.time() - start
    logger.info(
        "Embedded %d/%d element(s) into %r in %.2fs.",
        len(with_embeddings),
        len(records),
        SEMANTIC_VDB_COLLECTION,
        elapsed,
    )
    return len(with_embeddings)
