# SPDX-FileCopyrightText: Copyright (c) 2024-2026, NVIDIA CORPORATION & AFFILIATES.
# All rights reserved.
# SPDX-License-Identifier: Apache-2.0

"""Re-embed ontology elements from Neo4j without re-running the LLM pipeline.

Reads existing BusinessTerms, ColumnAttributes, and SqlAttributes from Neo4j,
reconstructs a CoreOntology, and writes fresh embeddings to the rigor_ontology
pgvector collection.

Usage::

    uv run python -m dev_tools.reembed_ontology --database-name dor_prod
    uv run python -m dev_tools.reembed_ontology --database-name dor_prod --schema-name public
"""

from __future__ import annotations

import argparse
import logging

from nemo_retriever.tabular_data.neo4j import get_neo4j_conn

from gsf.ontology.rigor.embed import embed_ontology, make_ontology_vdb
from gsf.ontology.rigor.loaders import fetch_schemas_for_database
from gsf.ontology.rigor.models import (
    AggregationType,
    Attribute,
    BusinessTerm,
    CoreOntology,
    Metric,
    Provenance,
)

logger = logging.getLogger(__name__)

RIGOR_SOURCE = "rigor"


def load_ontology_from_neo4j(
    database_name: str,
    schema_name: str | None = None,
) -> CoreOntology:
    """Read the existing Rigor ontology from Neo4j into a CoreOntology.

    Filters by *database_name* (and optionally *schema_name*) so only
    terms belonging to that database are returned.
    """
    conn = get_neo4j_conn()
    ontology = CoreOntology()

    schema_filter = " {name: $schema_name}" if schema_name else ""
    params: dict = {"source": RIGOR_SOURCE, "database_name": database_name}
    if schema_name:
        params["schema_name"] = schema_name

    # 1. BusinessTerms — only those whose source table lives under this DB
    bt_rows = conn.query_read(
        f"""
        MATCH (db:Database {{name: $database_name}})
              -[:CONTAINS]->(s:Schema{schema_filter})
              -[:CONTAINS]->(t:Table)
        WITH collect(t.name) AS db_tables
        MATCH (bt:BusinessTerm {{source: $source}})
        WHERE any(st IN bt.source_tables WHERE st IN db_tables)
        RETURN bt.id AS id, bt.name AS name,
               bt.description AS description,
               bt.source_tables AS source_tables
        """,
        params,
    )
    for r in bt_rows:
        source_tables = r.get("source_tables") or []
        ontology.business_terms.append(
            BusinessTerm(
                id=r["id"],
                name=r["name"],
                description=r.get("description") or "",
                provenance=[
                    Provenance(source_table=t, derivation="deterministic")
                    for t in source_tables
                ],
            )
        )
    logger.info("Loaded %d BusinessTerms from Neo4j", len(ontology.business_terms))

    # 2. ColumnAttributes
    bt_ids = {bt.id for bt in ontology.business_terms}
    attr_rows = conn.query_read(
        """
        MATCH (a:ColumnAttribute {source: $source})-[:IS_PROPERTY_OF]->(bt:BusinessTerm)
        WHERE bt.id IN $bt_ids
        RETURN a.id AS id, a.name AS name, a.datatype AS datatype,
               bt.name AS term_name, a.source_column AS source_column,
               a.description AS description, a.formula AS formula,
               a.usage_hint AS usage_hint,
               a.is_primary_key AS is_primary_key,
               bt.source_tables AS source_tables
        """,
        {"source": RIGOR_SOURCE, "bt_ids": list(bt_ids)},
    )
    for r in attr_rows:
        source_tables = r.get("source_tables") or []
        source_table = source_tables[0] if source_tables else ""
        ontology.attributes.append(
            Attribute(
                id=r["id"],
                name=r["name"],
                datatype=r.get("datatype") or "unknown",
                term_name=r["term_name"],
                source_column=r.get("source_column") or r["name"],
                provenance=Provenance(
                    source_table=source_table,
                    source_column=r.get("source_column") or r["name"],
                    derivation="deterministic",
                ),
                description=r.get("description"),
                formula=r.get("formula"),
                usage_hint=r.get("usage_hint"),
                is_primary_key=r.get("is_primary_key") or False,
            )
        )
    logger.info("Loaded %d ColumnAttributes from Neo4j", len(ontology.attributes))

    # 3. SqlAttributes (Metrics) — filter by same db_tables
    metric_rows = conn.query_read(
        f"""
        MATCH (db:Database {{name: $database_name}})
              -[:CONTAINS]->(s:Schema{schema_filter})
              -[:CONTAINS]->(t:Table)
        WITH collect(t.name) AS db_tables
        MATCH (m:SqlAttribute {{source: $source}})
        WHERE any(st IN m.source_tables WHERE st IN db_tables)
        RETURN m.id AS id, m.name AS name,
               m.expression AS expression,
               m.aggregation_type AS aggregation_type,
               m.source_tables AS source_tables
        """,
        params,
    )
    for r in metric_rows:
        agg_str = r.get("aggregation_type") or "OTHER"
        try:
            agg_type = AggregationType(agg_str)
        except ValueError:
            agg_type = AggregationType.OTHER
        ontology.metrics.append(
            Metric(
                id=r["id"],
                name=r["name"],
                expression=r.get("expression") or "",
                aggregation_type=agg_type,
                source_tables=r.get("source_tables") or [],
            )
        )
    logger.info("Loaded %d SqlAttributes from Neo4j", len(ontology.metrics))

    return ontology


def main() -> None:
    parser = argparse.ArgumentParser(description="Re-embed ontology from Neo4j")
    parser.add_argument(
        "--database-name",
        required=True,
        help="Database name (e.g. dor_prod, bird)",
    )
    parser.add_argument(
        "--schema-name",
        default=None,
        help="Schema name. If omitted, auto-discovers all schemas.",
    )
    args = parser.parse_args()

    if args.schema_name:
        schemas = [args.schema_name]
    else:
        schemas = fetch_schemas_for_database(args.database_name)
        if not schemas:
            schemas = [args.database_name]

    logger.info(
        "Re-embedding %d schema(s) for %r: %s",
        len(schemas),
        args.database_name,
        schemas,
    )

    vdb = make_ontology_vdb()
    try:
        for schema in schemas:
            ontology = load_ontology_from_neo4j(
                args.database_name,
                schema_name=schema,
            )
            total = (
                len(ontology.business_terms)
                + len(ontology.attributes)
                + len(ontology.metrics)
            )
            if total == 0:
                logger.info("No elements for schema %r — skipping.", schema)
                continue

            logger.info(
                "Embedding %d elements for %s.%s …",
                total,
                args.database_name,
                schema,
            )
            count = embed_ontology(
                ontology,
                database_name=args.database_name,
                schema_name=schema,
                vdb=vdb,
            )
            logger.info("Embedded %d elements for schema %r", count, schema)
    finally:
        vdb.close()


if __name__ == "__main__":
    from dotenv import load_dotenv

    load_dotenv()

    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s %(levelname)s %(name)s: %(message)s",
    )
    main()
