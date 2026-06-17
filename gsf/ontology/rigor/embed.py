"""Embed ontology elements into pgvector for semantic search.

Converts Terms, ColumnAttributes, and SqlAttributes from a CoreOntology into
composite text documents, embeds them via the NVIDIA NIM API, and writes
the vectors into the ``semantic_layer`` pgvector collection.
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

from gsf.vdb import SEMANTIC_VDB_COLLECTION, get_semantic_vdb
from gsf.vdb.postgres import PostgresVDB
from gsf.ontology.rigor.models import CoreOntology

logger = logging.getLogger(__name__)

_NVIDIA_API_KEY = os.environ.get("NVIDIA_API_KEY", "")
_EMBED_API_KEY = os.environ.get("EMBED_API_KEY", _NVIDIA_API_KEY)
_EMBED_ENDPOINT = os.environ.get(
    "EMBED_ENDPOINT", "https://integrate.api.nvidia.com/v1"
)
_EMBED_MODEL = os.environ.get("EMBED_MODEL", "nvidia/llama-nemotron-embed-1b-v2")


def _get_embed_params() -> EmbedParams:
    return EmbedParams(
        embed_invoke_url=_EMBED_ENDPOINT,
        model_name=_EMBED_MODEL,
        api_key=_EMBED_API_KEY,
        embed_modality="text",
    )


def _term_text(term_name: str, description: str, attr_names: list[str]) -> str:
    """Composite text for a Term embedding."""
    parts = [f"Term: {term_name}. {description}"]
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
    """Composite text for a ColumnAttribute embedding."""
    parts = [f"ColumnAttribute: {name} ({datatype}), property of {term_name}"]
    if description:
        parts.append(description)
    if usage_hint:
        parts.append(usage_hint)
    return ". ".join(parts)


def _sql_attribute_text(
    name: str,
    expression: str,
    aggregation_type: str,
    source_tables: list[str],
) -> str:
    """Composite text for a SqlAttribute embedding."""
    tables = ", ".join(source_tables) if source_tables else "unknown"
    return f"sql_attribute: {name}. {expression} ({aggregation_type}) over {tables}"


def _qualify_table(table: str, schema: str) -> str:
    """Return ``SCHEMA.TABLE`` if *schema* is non-empty, else *table* as-is."""
    return f"{schema}.{table}" if schema else table


def _build_records(
    ontology: CoreOntology,
    database_name: str,
    schema_name: str = "",
) -> list[dict]:
    """Convert ontology elements into NeMo-compatible record dicts.

    There is no top-level ``schema_name`` in the metadata.  Instead the
    schema is baked into each ``source_tables`` / ``source_table`` value
    (e.g. ``"SALES.CUSTOMERS"``) so downstream consumers always see the
    fully-qualified table name that was valid at embed time.
    """
    attrs_by_term: dict[str, list[str]] = defaultdict(list)
    for attr in ontology.attributes:
        attrs_by_term[attr.term_name].append(attr.name)

    records: list[dict] = []

    for bt in ontology.business_terms:
        text = _term_text(bt.name, bt.description, attrs_by_term.get(bt.name, []))
        source_tables = sorted(
            {_qualify_table(p.source_table, schema_name) for p in bt.provenance}
        )
        node_id = bt.id or f"semantic:term:{bt.name}"
        meta = {
            "id": node_id,
            "label": "Term",
            "name": bt.name,
            "source_tables": source_tables,
            "database_name": database_name,
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
            attr.datatype,
            attr.term_name,
            attr.description,
            attr.usage_hint,
        )
        node_id = attr.id or f"semantic:attr:{attr.term_name}:{attr.source_column}"
        qualified_table = _qualify_table(attr.provenance.source_table, schema_name)
        meta = {
            "id": node_id,
            "label": "ColumnAttribute",
            "name": attr.name,
            "term_name": attr.term_name,
            "source_column": attr.source_column,
            "source_table": qualified_table,
            "database_name": database_name,
            "is_primary_key": attr.is_primary_key,
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
        text = _sql_attribute_text(
            m.name,
            m.expression,
            m.aggregation_type.value,
            m.source_tables,
        )
        node_id = m.id or f"semantic:metric:{m.name}"
        qualified_tables = sorted(
            _qualify_table(t, schema_name) for t in m.source_tables
        )
        meta = {
            "id": node_id,
            "label": "SqlAttribute",
            "name": m.name,
            "source_tables": qualified_tables,
            "database_name": database_name,
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
    schema_name: str = "",
    vdb: PostgresVDB | None = None,
) -> int:
    """Embed all ontology elements into pgvector. Returns row count.

    *database_name* is the Postgres database (e.g. ``bird``).
    *schema_name* is used only to qualify ``source_tables`` entries
    (e.g. ``"SALES.CUSTOMERS"``); it is **not** stored as a separate
    metadata field.
    If *vdb* is provided, it is reused; otherwise a temporary one is created
    and closed after the write.
    """
    records = _build_records(ontology, database_name, schema_name)
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

    owns_vdb = vdb is None
    if owns_vdb:
        vdb = get_semantic_vdb(database_name=database_name)
    try:
        IngestVdbOperator(vdb=vdb)(with_embeddings)
    finally:
        if owns_vdb:
            vdb.close()

    elapsed = time.time() - start
    logger.info(
        "Embedded %d/%d ontology element(s) into %r in %.2fs.",
        len(with_embeddings),
        len(records),
        SEMANTIC_VDB_COLLECTION,
        elapsed,
    )
    return len(with_embeddings)
