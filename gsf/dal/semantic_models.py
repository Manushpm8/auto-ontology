# SPDX-FileCopyrightText: Copyright (c) 2026, NVIDIA CORPORATION & AFFILIATES.
# All rights reserved.
# SPDX-License-Identifier: Apache-2.0

"""Neo4j persistence for versioned semantic-model interchange."""

from __future__ import annotations

import os
from typing import Any

from neo4j import GraphDatabase
from nemo_retriever.tabular_data.ingestion.model.reserved_words import Edges, Labels
from nemo_retriever.tabular_data.neo4j import get_neo4j_conn

from gsf.semantic.constants import (
    LABEL_COLUMN_ATTRIBUTE,
    LABEL_SEMANTIC_IMPORT_LOCK,
    LABEL_SEMANTIC_MODEL,
    LABEL_SQL_ATTRIBUTE,
    LABEL_TERM,
    REL_HAS_ATTRIBUTE,
    REL_HAS_DATASET,
    REL_PROPERTY_OF,
    REL_REPRESENTS,
    REL_SEMANTIC_FK,
    SEMANTIC_SOURCE,
)


class SemanticModelPersistenceError(Exception):
    """Raised when a transactional model write cannot persist every row."""


def resolve_catalog_sources(rows: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Resolve document table references against the ingested GSF catalog."""
    return get_neo4j_conn().query_read(
        f"""
        UNWIND $rows AS row
        MATCH (db:{Labels.DB})-[:{Edges.CONTAINS}]->(schema:{Labels.SCHEMA})
              -[:{Edges.CONTAINS}]->(table:{Labels.TABLE})
        WHERE table.name = row.table
          AND (row.schema IS NULL OR schema.name = row.schema)
          AND (row.database IS NULL OR db.name = row.database)
        RETURN row.name AS name, db.name AS database,
               schema.name AS schema, table.name AS table, table.id AS table_id
        ORDER BY name, database, schema, table
        """,
        {"rows": rows},
    )


def resolve_catalog_columns(rows: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Resolve document column references against physical GSF columns."""
    return get_neo4j_conn().query_read(
        f"""
        UNWIND $rows AS row
        MATCH (:{Labels.DB} {{name: row.database}})-[:{Edges.CONTAINS}]->
              (:{Labels.SCHEMA} {{name: row.schema}})-[:{Edges.CONTAINS}]->
              (:{Labels.TABLE} {{name: row.table}})-[:{Edges.CONTAINS}]->
              (column:{Labels.COLUMN} {{name: row.column}})
        RETURN row.dataset AS dataset, column.name AS column,
               column.id AS column_id, column.data_type AS datatype
        """,
        {"rows": rows},
    )


def find_term_conflicts(
    rows: list[dict[str, Any]],
    *,
    model_name: str,
    replace: bool,
) -> list[dict[str, Any]]:
    """Find global Terms that cannot be safely reused by this model."""
    return get_neo4j_conn().query_read(
        f"""
        UNWIND $rows AS row
        MATCH (term:{LABEL_TERM} {{name: row.name, source: $source}})
        OPTIONAL MATCH (table:{Labels.TABLE})-[:{REL_REPRESENTS}]->(term)
        WITH row, term, collect(table.id) AS represented_table_ids
        WHERE term.import_model IS NULL
           OR term.import_model <> $model_name
           OR (
            NOT $replace
            AND
            any(
                table_id IN represented_table_ids
                WHERE table_id <> row.table_id
            )
        )
        RETURN row.name AS name, term.import_model AS existing_model,
               represented_table_ids
        ORDER BY name
        """,
        {
            "rows": rows,
            "model_name": model_name,
            "replace": replace,
            "source": SEMANTIC_SOURCE,
        },
    )


def find_table_conflicts(
    rows: list[dict[str, Any]],
    *,
    model_name: str,
) -> list[dict[str, Any]]:
    """Find physical Tables already represented outside this model."""
    return get_neo4j_conn().query_read(
        f"""
        UNWIND $rows AS row
        MATCH (table:{Labels.TABLE} {{id: row.table_id}})
        OPTIONAL MATCH (table)-[:{REL_REPRESENTS}]->(term:{LABEL_TERM})
        WITH row, table, collect(term) AS represented_terms
        WHERE any(
            term IN represented_terms
            WHERE term.name <> row.name
               OR coalesce(term.import_model, '') <> $model_name
        )
        RETURN row.name AS name, table.id AS table_id,
               [term IN represented_terms | term.name] AS existing_terms
        ORDER BY name
        """,
        {"rows": rows, "model_name": model_name},
    )


def find_sql_attribute_conflicts(
    rows: list[dict[str, Any]],
    *,
    model_name: str,
) -> list[dict[str, Any]]:
    """Return global SqlAttribute name conflicts outside this imported model."""
    return get_neo4j_conn().query_read(
        f"""
        UNWIND $rows AS row
        MATCH (attribute:{LABEL_SQL_ATTRIBUTE} {{name: row.name}})
        OPTIONAL MATCH (attribute)-[:{REL_PROPERTY_OF}]->(term:{LABEL_TERM})
        WITH row, attribute, term
        WHERE coalesce(attribute.import_model, '') <> $model_name
           OR term IS NULL
           OR term.name <> row.term
        RETURN DISTINCT row.name AS name, term.name AS term_name
        ORDER BY name
        """,
        {"rows": rows, "model_name": model_name},
    )


def replace_imported_model(model_name: str) -> None:
    """Delete only graph objects owned by an earlier import of *model_name*."""
    get_neo4j_conn().query_write(
        f"""
        MATCH ()-[relationship:{REL_SEMANTIC_FK}]->()
        WHERE relationship.import_model = $model_name
        DELETE relationship
        WITH count(*) AS ignored
        MATCH (attribute)
        WHERE attribute.import_model = $model_name
          AND attribute.import_managed = true
          AND (attribute:{LABEL_COLUMN_ATTRIBUTE}
               OR attribute:{LABEL_SQL_ATTRIBUTE})
        DETACH DELETE attribute
        WITH count(*) AS ignored
        MATCH (sql:{Labels.SQL})
        WHERE sql.import_model = $model_name
          AND sql.import_managed = true
          AND NOT EXISTS {{
              MATCH ()-[:{Edges.HAS_SQL}]->(sql)
          }}
        DETACH DELETE sql
        WITH count(*) AS ignored
        MATCH ()-[represents:{REL_REPRESENTS}]->()
        WHERE represents.import_model = $model_name
        DELETE represents
        WITH count(*) AS ignored
        MATCH (term:{LABEL_TERM})
        WHERE term.import_model = $model_name
          AND term.import_managed = true
          AND NOT EXISTS {{
              MATCH (:{Labels.TABLE})-[:{REL_REPRESENTS}]->(term)
          }}
        DETACH DELETE term
        WITH count(*) AS ignored
        MATCH (model:{LABEL_SEMANTIC_MODEL} {{
            name: $model_name,
            format: 'gsf'
        }})
        DETACH DELETE model
        """,
        {"model_name": model_name},
    )


def write_model(row: dict[str, Any]) -> None:
    """Create or update a semantic-model envelope."""
    get_neo4j_conn().query_write(
        f"""
        MERGE (model:{LABEL_SEMANTIC_MODEL} {{
            name: $model_name,
            format: 'gsf'
        }})
        ON CREATE SET model.id = randomUUID()
        SET model.format_version = $format_version,
            model.description = $description,
            model.ai_context = $ai_context,
            model.original_root = $original_root,
            model.original_model = $original_model
        """,
        row,
    )


def write_datasets(
    model_name: str,
    rows: list[dict[str, Any]],
) -> list[dict[str, Any]]:
    """Attach imported Terms to resolved physical Tables."""
    return get_neo4j_conn().query_write(
        f"""
        UNWIND $rows AS row
        MATCH (db:{Labels.DB} {{name: row.database}})-[:{Edges.CONTAINS}]->
              (schema:{Labels.SCHEMA} {{name: row.schema}})
              -[:{Edges.CONTAINS}]->(table:{Labels.TABLE} {{name: row.table}})
        MATCH (model:{LABEL_SEMANTIC_MODEL} {{
            name: $model_name,
            format: 'gsf'
        }})
        MERGE (term:{LABEL_TERM} {{name: row.name, source: $source}})
        ON CREATE SET term.id = randomUUID(),
                      term.import_managed = true
        SET term.description = row.description,
            term.synonyms = row.synonyms,
            term.import_model = $model_name,
            term.import_ai_context = row.ai_context,
            term.import_original = row.original
        MERGE (table)-[represents:{REL_REPRESENTS}]->(term)
        ON CREATE SET represents.import_model = $model_name
        MERGE (model)-[has_dataset:{REL_HAS_DATASET}]->(term)
        SET has_dataset.table_id = table.id
        RETURN row.name AS name, term.id AS term_id, table.id AS table_id
        """,
        {"model_name": model_name, "rows": rows, "source": SEMANTIC_SOURCE},
    )


def write_column_attributes(
    model_name: str,
    rows: list[dict[str, Any]],
) -> list[dict[str, Any]]:
    """Create imported ColumnAttributes and their graph edges."""
    if not rows:
        return []
    return get_neo4j_conn().query_write(
        f"""
        UNWIND $rows AS row
        MATCH (term:{LABEL_TERM} {{name: row.dataset, source: $source}})
        WHERE term.import_model = $model_name
        MATCH (:{Labels.DB} {{name: row.table.database}})
              -[:{Edges.CONTAINS}]->
              (:{Labels.SCHEMA} {{name: row.table.schema}})
              -[:{Edges.CONTAINS}]->
              (table:{Labels.TABLE} {{name: row.table.table}})
              -[:{REL_REPRESENTS}]->(term)
        MATCH (table)-[:{Edges.CONTAINS}]->
              (column:{Labels.COLUMN} {{name: row.source_column}})
        MERGE (attribute:{LABEL_COLUMN_ATTRIBUTE} {{
            source_column: row.source_column,
            term_name: row.dataset,
            table_id: table.id,
            source: $source
        }})
        ON CREATE SET attribute.id = randomUUID(),
                      attribute.import_managed = true
        SET attribute.name = row.name,
            attribute.datatype = column.data_type,
            attribute.description = row.description,
            attribute.import_model = $model_name,
            attribute.import_ai_context = row.ai_context,
            attribute.import_original = row.original,
            attribute.import_synthetic = row.synthetic
        MERGE (column)-[:{REL_HAS_ATTRIBUTE}]->(attribute)
        MERGE (attribute)-[:{REL_PROPERTY_OF}]->(term)
        RETURN attribute.id AS id, attribute.name AS name,
               attribute.term_name AS term_name,
               attribute.source_column AS source_column,
               attribute.description AS description,
               column.sample_values AS sample_values
        """,
        {
            "model_name": model_name,
            "rows": rows,
            "source": SEMANTIC_SOURCE,
        },
    )


def write_sql_attributes(
    model_name: str,
    rows: list[dict[str, Any]],
) -> list[dict[str, Any]]:
    """Create imported SqlAttributes, Sql nodes, and referenced-table edges."""
    if not rows:
        return []
    return get_neo4j_conn().query_write(
        f"""
        UNWIND $rows AS row
        MATCH (term:{LABEL_TERM} {{name: row.term, source: $source}})
        WHERE term.import_model = $model_name
        MERGE (attribute:{LABEL_SQL_ATTRIBUTE} {{name: row.name}})
        ON CREATE SET attribute.id = randomUUID(),
                      attribute.import_managed = true
        SET attribute.description = row.description,
            attribute.expression = row.expression,
            attribute.source = 'manual',
            attribute.import_model = $model_name,
            attribute.import_kind = row.kind,
            attribute.import_original = row.original
        MERGE (attribute)-[:{REL_PROPERTY_OF}]->(term)
        MERGE (sql:{Labels.SQL} {{sql_full_query: row.sql}})
        ON CREATE SET sql.id = randomUUID(),
                      sql.import_managed = true
        SET sql.import_model = $model_name
        MERGE (attribute)-[:{Edges.HAS_SQL}]->(sql)
        WITH row, attribute, sql
        UNWIND row.table_refs AS ref
        MATCH (:{Labels.DB} {{name: ref.database}})-[:{Edges.CONTAINS}]->
              (:{Labels.SCHEMA} {{name: ref.schema}})-[:{Edges.CONTAINS}]->
              (table:{Labels.TABLE} {{name: ref.table}})
        MERGE (sql)-[:{Edges.SQL}]->(table)
        RETURN DISTINCT row.name AS name, attribute.id AS id,
               row.term AS term_name
        """,
        {
            "model_name": model_name,
            "rows": rows,
            "source": SEMANTIC_SOURCE,
        },
    )


def write_relationships(
    model_name: str,
    rows: list[dict[str, Any]],
) -> list[dict[str, Any]]:
    """Create physical and semantic FK edges for imported relationships."""
    if not rows:
        return []
    return get_neo4j_conn().query_write(
        f"""
        UNWIND $rows AS row
        UNWIND range(0, size(row.from_columns) - 1) AS index
        MATCH (:{Labels.DB} {{name: row.from_table.database}})
              -[:{Edges.CONTAINS}]->
              (:{Labels.SCHEMA} {{name: row.from_table.schema}})
              -[:{Edges.CONTAINS}]->
              (from_table:{Labels.TABLE} {{name: row.from_table.table}})
              -[:{Edges.CONTAINS}]->
              (from_column:{Labels.COLUMN} {{
                  name: row.from_columns[index]
              }})
        OPTIONAL MATCH (from_column)-[old_relationship:{REL_SEMANTIC_FK}]->()
        WHERE old_relationship.import_model = $model_name
          AND old_relationship.import_name = row.name
          AND old_relationship.import_index = index
        DELETE old_relationship
        WITH row, index, from_column
        MATCH (:{Labels.DB} {{name: row.to_table.database}})
              -[:{Edges.CONTAINS}]->
              (:{Labels.SCHEMA} {{name: row.to_table.schema}})
              -[:{Edges.CONTAINS}]->
              (to_table:{Labels.TABLE} {{name: row.to_table.table}})
              -[:{Edges.CONTAINS}]->
              (to_column:{Labels.COLUMN} {{name: row.to_columns[index]}})
              -[:{REL_HAS_ATTRIBUTE}]->
              (to_attribute:{LABEL_COLUMN_ATTRIBUTE})
        WHERE to_attribute.term_name = row.to_dataset
          AND to_attribute.source_column = row.to_columns[index]
          AND to_attribute.import_model = $model_name
        CREATE (from_column)-[semantic_fk:{REL_SEMANTIC_FK}]->(to_attribute)
        SET semantic_fk.import_model = $model_name,
            semantic_fk.import_name = row.name,
            semantic_fk.import_index = index,
            semantic_fk.import_from_dataset = row.from_dataset,
            semantic_fk.import_to_dataset = row.to_dataset,
            semantic_fk.import_original = row.original
        RETURN DISTINCT row.name AS name
        """,
        {"model_name": model_name, "rows": rows},
    )


def apply_import_plan(
    *,
    model: dict[str, Any],
    datasets: list[dict[str, Any]],
    column_attributes: list[dict[str, Any]],
    sql_attributes: list[dict[str, Any]],
    relationships: list[dict[str, Any]],
    replace: bool,
) -> dict[str, list[dict[str, Any]] | list[str]]:
    """Atomically apply a fully validated semantic-model import plan."""
    driver = GraphDatabase.driver(
        os.environ["NEO4J_URI"],
        auth=(os.environ["NEO4J_USERNAME"], os.environ["NEO4J_PASSWORD"]),
        max_connection_lifetime=290,
        liveness_check_timeout=4,
        notifications_min_severity="OFF",
    )
    try:
        with driver.session(database="neo4j") as session:
            return session.execute_write(
                _apply_import_transaction,
                model,
                datasets,
                column_attributes,
                sql_attributes,
                relationships,
                replace,
            )
    finally:
        driver.close()


def _apply_import_transaction(
    transaction: Any,
    model: dict[str, Any],
    datasets: list[dict[str, Any]],
    column_attributes: list[dict[str, Any]],
    sql_attributes: list[dict[str, Any]],
    relationships: list[dict[str, Any]],
    replace: bool,
) -> dict[str, list[dict[str, Any]] | list[str]]:
    model_name = str(model["model_name"])
    database_name = str(datasets[0]["database"])
    transaction.run(
        f"""
        MERGE (global_lock:{LABEL_SEMANTIC_IMPORT_LOCK} {{
            name: 'global'
        }})
        ON CREATE SET global_lock.id = randomUUID()
        SET global_lock.import_lock = coalesce(
            global_lock.import_lock,
            0
        )
        WITH global_lock
        MATCH (database:{Labels.DB} {{name: $database_name}})
        SET database.semantic_model_import_lock = coalesce(
            database.semantic_model_import_lock,
            0
        )
        MERGE (envelope:{LABEL_SEMANTIC_MODEL} {{
            name: $model_name,
            format: 'gsf'
        }})
        ON CREATE SET envelope.id = randomUUID()
        SET envelope.semantic_model_import_lock = coalesce(
            envelope.semantic_model_import_lock,
            0
        )
        """,
        database_name=database_name,
        model_name=model_name,
    ).consume()

    model_scope_conflict = transaction.run(
        f"""
        MATCH (envelope:{LABEL_SEMANTIC_MODEL} {{
            name: $model_name,
            format: 'gsf'
        }})
        OPTIONAL MATCH (envelope)-[:{REL_HAS_DATASET}]->(:{LABEL_TERM})
              <-[:{REL_REPRESENTS}]-(:{Labels.TABLE})
              <-[:{Edges.CONTAINS}]-(:{Labels.SCHEMA})
              <-[:{Edges.CONTAINS}]-(database:{Labels.DB})
        WITH collect(DISTINCT database.name) AS database_names
        WHERE any(
            name IN database_names
            WHERE name <> $database_name
        )
        RETURN database_names
        """,
        database_name=database_name,
        model_name=model_name,
    ).single()
    if model_scope_conflict is not None:
        raise SemanticModelPersistenceError(
            f"GSF model {model_name!r} already belongs to another database"
        )

    term_conflicts = [
        str(record["name"])
        for record in transaction.run(
            f"""
            UNWIND $rows AS row
            MATCH (term:{LABEL_TERM} {{name: row.name, source: $source}})
            OPTIONAL MATCH (table:{Labels.TABLE})-[:{REL_REPRESENTS}]->(term)
            WITH row, term, collect(table.id) AS represented_table_ids
            WHERE term.import_model IS NULL
               OR term.import_model <> $model_name
               OR (
                    NOT $replace
                    AND any(
                        table_id IN represented_table_ids
                        WHERE table_id <> row.table_id
                    )
               )
            RETURN DISTINCT row.name AS name
            """,
            rows=datasets,
            model_name=model_name,
            replace=replace,
            source=SEMANTIC_SOURCE,
        )
    ]
    if term_conflicts:
        raise SemanticModelPersistenceError(
            "GSF Term names are already owned outside this import: "
            + ", ".join(sorted(term_conflicts))
        )

    table_conflicts = [
        str(record["name"])
        for record in transaction.run(
            f"""
            UNWIND $rows AS row
            MATCH (table:{Labels.TABLE} {{id: row.table_id}})
            OPTIONAL MATCH (table)-[:{REL_REPRESENTS}]->(term:{LABEL_TERM})
            WITH row, collect(term) AS represented_terms
            WHERE any(
                term IN represented_terms
                WHERE term.name <> row.name
                   OR coalesce(term.import_model, '') <> $model_name
            )
            RETURN DISTINCT row.name AS name
            """,
            rows=datasets,
            model_name=model_name,
        )
    ]
    if table_conflicts:
        raise SemanticModelPersistenceError(
            "Physical tables are already represented outside this import: "
            + ", ".join(sorted(table_conflicts))
        )

    sql_attribute_conflicts = [
        str(record["name"])
        for record in transaction.run(
            f"""
            UNWIND $rows AS row
            MATCH (attribute:{LABEL_SQL_ATTRIBUTE} {{name: row.name}})
            OPTIONAL MATCH (attribute)-[:{REL_PROPERTY_OF}]->(term:{LABEL_TERM})
            WITH row, attribute, term
            WHERE coalesce(attribute.import_model, '') <> $model_name
               OR term IS NULL
               OR term.name <> row.term
            RETURN DISTINCT row.name AS name
            """,
            rows=sql_attributes,
            model_name=model_name,
        )
    ]
    if sql_attribute_conflicts:
        raise SemanticModelPersistenceError(
            "GSF SqlAttribute names are already owned outside this import: "
            + ", ".join(sorted(sql_attribute_conflicts))
        )

    stale_result = transaction.run(
        f"""
        OPTIONAL MATCH (envelope:{LABEL_SEMANTIC_MODEL} {{
            name: $model_name,
            format: 'gsf'
        }})
        WITH coalesce(
            envelope.pending_embedding_deletes,
            []
        ) AS pending_ids
        OPTIONAL MATCH (node)
        WHERE $replace
          AND node.import_model = $model_name
          AND node.import_managed = true
          AND (
              node:{LABEL_TERM}
              OR node:{LABEL_COLUMN_ATTRIBUTE}
              OR node:{LABEL_SQL_ATTRIBUTE}
          )
        WITH pending_ids, collect(node.id) AS node_ids
        RETURN pending_ids + node_ids AS ids
        """,
        model_name=model_name,
        replace=replace,
    ).single()
    stale_ids = list(stale_result["ids"] or []) if stale_result else []
    if replace:
        transaction.run(
            f"""
            MATCH ()-[relationship:{REL_SEMANTIC_FK}]->()
            WHERE relationship.import_model = $model_name
            DELETE relationship
            WITH count(*) AS ignored
            MATCH (attribute)
            WHERE attribute.import_model = $model_name
              AND attribute.import_managed = true
              AND (attribute:{LABEL_COLUMN_ATTRIBUTE}
                   OR attribute:{LABEL_SQL_ATTRIBUTE})
            DETACH DELETE attribute
            WITH count(*) AS ignored
            MATCH (sql:{Labels.SQL})
            WHERE sql.import_model = $model_name
              AND sql.import_managed = true
              AND NOT EXISTS {{
                  MATCH ()-[:{Edges.HAS_SQL}]->(sql)
              }}
            DETACH DELETE sql
            WITH count(*) AS ignored
            MATCH ()-[represents:{REL_REPRESENTS}]->()
            WHERE represents.import_model = $model_name
            DELETE represents
            WITH count(*) AS ignored
            MATCH (term:{LABEL_TERM})
            WHERE term.import_model = $model_name
              AND term.import_managed = true
              AND NOT EXISTS {{
                  MATCH (:{Labels.TABLE})-[:{REL_REPRESENTS}]->(term)
              }}
            DETACH DELETE term
            WITH count(*) AS ignored
            MATCH (envelope:{LABEL_SEMANTIC_MODEL} {{
                name: $model_name,
                format: 'gsf'
            }})
            DETACH DELETE envelope
            """,
            model_name=model_name,
        ).consume()

    transaction.run(
        f"""
        MERGE (envelope:{LABEL_SEMANTIC_MODEL} {{
            name: $model_name,
            format: 'gsf'
        }})
        ON CREATE SET envelope.id = randomUUID()
        SET envelope.format_version = $format_version,
            envelope.database_name = $database_name,
            envelope.description = $description,
            envelope.ai_context = $ai_context,
            envelope.original_root = $original_root,
            envelope.original_model = $original_model,
            envelope.pending_embedding_deletes = CASE
                WHEN $replace
                THEN $pending_embedding_deletes
                ELSE coalesce(envelope.pending_embedding_deletes, [])
            END
        """,
        pending_embedding_deletes=stale_ids,
        replace=replace,
        **model,
    ).consume()

    dataset_rows = [
        dict(record)
        for record in transaction.run(
            f"""
            UNWIND $rows AS row
            MATCH (db:{Labels.DB} {{name: row.database}})
                  -[:{Edges.CONTAINS}]->
                  (schema:{Labels.SCHEMA} {{name: row.schema}})
                  -[:{Edges.CONTAINS}]->
                  (table:{Labels.TABLE} {{name: row.table}})
            MATCH (envelope:{LABEL_SEMANTIC_MODEL} {{
                name: $model_name,
                format: 'gsf'
            }})
            MERGE (term:{LABEL_TERM} {{name: row.name, source: $source}})
            ON CREATE SET term.id = randomUUID(),
                          term.import_managed = true
            SET term.description = row.description,
                term.synonyms = row.synonyms,
                term.import_model = $model_name,
                term.import_ai_context = row.ai_context,
                term.import_original = row.original
            MERGE (table)-[represents:{REL_REPRESENTS}]->(term)
            ON CREATE SET represents.import_model = $model_name
            MERGE (envelope)-[has_dataset:{REL_HAS_DATASET}]->(term)
            SET has_dataset.table_id = table.id
            RETURN row.name AS name, term.id AS term_id, table.id AS table_id
            """,
            rows=datasets,
            model_name=model_name,
            source=SEMANTIC_SOURCE,
        )
    ]
    _require_row_count("datasets", len(datasets), dataset_rows)

    column_rows = [
        dict(record)
        for record in transaction.run(
            f"""
            UNWIND $rows AS row
            MATCH (term:{LABEL_TERM} {{
                name: row.dataset,
                source: $source
            }})
            WHERE term.import_model = $model_name
            MATCH (:{Labels.DB} {{name: row.table.database}})
                  -[:{Edges.CONTAINS}]->
                  (:{Labels.SCHEMA} {{name: row.table.schema}})
                  -[:{Edges.CONTAINS}]->
                  (table:{Labels.TABLE} {{name: row.table.table}})
                  -[:{REL_REPRESENTS}]->(term)
            MATCH (table)-[:{Edges.CONTAINS}]->
                  (column:{Labels.COLUMN} {{name: row.source_column}})
            MERGE (attribute:{LABEL_COLUMN_ATTRIBUTE} {{
                source_column: row.source_column,
                term_name: row.dataset,
                table_id: table.id,
                source: $source
            }})
            ON CREATE SET attribute.id = randomUUID(),
                          attribute.import_managed = true
            SET attribute.name = row.name,
                attribute.datatype = column.data_type,
                attribute.description = row.description,
                attribute.import_model = $model_name,
                attribute.import_ai_context = row.ai_context,
                attribute.import_original = row.original,
                attribute.import_synthetic = row.synthetic
            MERGE (column)-[:{REL_HAS_ATTRIBUTE}]->(attribute)
            MERGE (attribute)-[:{REL_PROPERTY_OF}]->(term)
            RETURN attribute.id AS id, attribute.name AS name,
                   attribute.term_name AS term_name,
                   attribute.source_column AS source_column,
                   attribute.description AS description,
                   column.sample_values AS sample_values
            """,
            rows=column_attributes,
            model_name=model_name,
            source=SEMANTIC_SOURCE,
        )
    ]
    _require_row_count(
        "column attributes",
        len(column_attributes),
        column_rows,
    )

    sql_rows = [
        dict(record)
        for record in transaction.run(
            f"""
            UNWIND $rows AS row
            MATCH (term:{LABEL_TERM} {{name: row.term, source: $source}})
            WHERE term.import_model = $model_name
            MERGE (attribute:{LABEL_SQL_ATTRIBUTE} {{name: row.name}})
            ON CREATE SET attribute.id = randomUUID(),
                          attribute.import_managed = true
            SET attribute.description = row.description,
                attribute.expression = row.expression,
                attribute.source = 'manual',
                attribute.import_model = $model_name,
                attribute.import_kind = row.kind,
                attribute.import_original = row.original
            MERGE (attribute)-[:{REL_PROPERTY_OF}]->(term)
            WITH row, term, attribute
            OPTIONAL MATCH (attribute)-[old_edge:{Edges.HAS_SQL}]->
                           (old_sql:{Labels.SQL})
            WHERE old_sql.import_model = $model_name
            DELETE old_edge
            WITH row, term, attribute
            MERGE (sql:{Labels.SQL} {{
                import_model: $model_name,
                import_attribute: row.name
            }})
            ON CREATE SET sql.id = randomUUID(),
                          sql.import_managed = true
            SET sql.sql_full_query = row.sql
            MERGE (attribute)-[:{Edges.HAS_SQL}]->(sql)
            WITH row, attribute, sql
            OPTIONAL MATCH (sql)-[old_table_edge:{Edges.SQL}]->
                           (:{Labels.TABLE})
            DELETE old_table_edge
            WITH row, attribute, sql
            UNWIND row.table_refs AS ref
            MATCH (:{Labels.DB} {{name: ref.database}})
                  -[:{Edges.CONTAINS}]->
                  (:{Labels.SCHEMA} {{name: ref.schema}})
                  -[:{Edges.CONTAINS}]->
                  (table:{Labels.TABLE} {{name: ref.table}})
            MERGE (sql)-[:{Edges.SQL}]->(table)
            RETURN DISTINCT row.name AS name, attribute.id AS id,
                   row.term AS term_name
            """,
            rows=sql_attributes,
            model_name=model_name,
            source=SEMANTIC_SOURCE,
        )
    ]
    _require_row_count("SQL attributes", len(sql_attributes), sql_rows)

    relationship_rows = [
        dict(record)
        for record in transaction.run(
            f"""
            UNWIND $rows AS row
            UNWIND range(0, size(row.from_columns) - 1) AS index
            MATCH (:{Labels.DB} {{name: row.from_table.database}})
                  -[:{Edges.CONTAINS}]->
                  (:{Labels.SCHEMA} {{name: row.from_table.schema}})
                  -[:{Edges.CONTAINS}]->
                  (from_table:{Labels.TABLE} {{
                      name: row.from_table.table
                  }})
                  -[:{Edges.CONTAINS}]->
                  (from_column:{Labels.COLUMN} {{
                      name: row.from_columns[index]
                  }})
            OPTIONAL MATCH (from_column)
                  -[old_relationship:{REL_SEMANTIC_FK}]->()
            WHERE old_relationship.import_model = $model_name
              AND old_relationship.import_name = row.name
              AND old_relationship.import_index = index
            DELETE old_relationship
            WITH row, index, from_column
            MATCH (:{Labels.DB} {{name: row.to_table.database}})
                  -[:{Edges.CONTAINS}]->
                  (:{Labels.SCHEMA} {{name: row.to_table.schema}})
                  -[:{Edges.CONTAINS}]->
                  (to_table:{Labels.TABLE} {{name: row.to_table.table}})
                  -[:{Edges.CONTAINS}]->
                  (to_column:{Labels.COLUMN} {{
                      name: row.to_columns[index]
                  }})
                  -[:{REL_HAS_ATTRIBUTE}]->
                  (to_attribute:{LABEL_COLUMN_ATTRIBUTE})
            WHERE to_attribute.term_name = row.to_dataset
              AND to_attribute.source_column = row.to_columns[index]
              AND to_attribute.import_model = $model_name
            CREATE (from_column)
                   -[semantic_fk:{REL_SEMANTIC_FK}]->(to_attribute)
            SET semantic_fk.import_model = $model_name,
                semantic_fk.import_name = row.name,
                semantic_fk.import_index = index,
                semantic_fk.import_from_dataset = row.from_dataset,
                semantic_fk.import_to_dataset = row.to_dataset,
                semantic_fk.import_original = row.original
            RETURN row.name AS name, index
            """,
            rows=relationships,
            model_name=model_name,
        )
    ]
    _require_row_count(
        "relationships",
        sum(len(row["from_columns"]) for row in relationships),
        relationship_rows,
    )

    transaction.run(
        f"""
        MATCH (sql:{Labels.SQL})
        WHERE sql.import_model = $model_name
          AND sql.import_managed = true
          AND NOT EXISTS {{
              MATCH ()-[:{Edges.HAS_SQL}]->(sql)
          }}
        DETACH DELETE sql
        """,
        model_name=model_name,
    ).consume()

    return {
        "datasets": dataset_rows,
        "column_attributes": column_rows,
        "sql_attributes": sql_rows,
        "relationships": relationship_rows,
        "stale_ids": stale_ids,
    }


def clear_pending_embedding_deletes(model_name: str) -> None:
    """Clear persisted VDB cleanup work after every ID was deleted."""
    get_neo4j_conn().query_write(
        f"""
        MATCH (envelope:{LABEL_SEMANTIC_MODEL} {{
            name: $model_name,
            format: 'gsf'
        }})
        SET envelope.pending_embedding_deletes = []
        """,
        {"model_name": model_name},
    )


def _require_row_count(
    kind: str,
    expected: int,
    rows: list[dict[str, Any]],
) -> None:
    if len(rows) != expected:
        raise SemanticModelPersistenceError(
            f"Expected {expected} {kind}, but Neo4j wrote {len(rows)}"
        )


def read_models(
    *,
    database_name: str,
    model_name: str | None,
) -> list[dict[str, Any]]:
    """Read GSF model envelopes associated with a catalog database."""
    return get_neo4j_conn().query_read(
        f"""
        MATCH (model:{LABEL_SEMANTIC_MODEL} {{format: 'gsf'}})
        WHERE ($model_name IS NULL OR model.name = $model_name)
          AND EXISTS {{
              MATCH (model)-[:{REL_HAS_DATASET}]->(:{LABEL_TERM})
                    <-[:{REL_REPRESENTS}]-(:{Labels.TABLE})
                    <-[:{Edges.CONTAINS}]-(:{Labels.SCHEMA})
                    <-[:{Edges.CONTAINS}]-
                    (:{Labels.DB} {{name: $database_name}})
          }}
        RETURN model.name AS name, model.format_version AS format_version,
               model.database_name AS database_name,
               model.description AS description,
               model.ai_context AS ai_context,
               model.original_root AS original_root,
               model.original_model AS original_model
        ORDER BY model.name
        """,
        {"database_name": database_name, "model_name": model_name},
    )


def _model_scope(_table_variable: str) -> str:
    return f"""
    ($model_name IS NULL OR EXISTS {{
        MATCH (:{LABEL_SEMANTIC_MODEL} {{
            name: $model_name,
            format: 'gsf'
        }})-[:{REL_HAS_DATASET}]->(term)
    }})
    """


def read_datasets(
    *,
    database_name: str,
    model_name: str | None,
) -> list[dict[str, Any]]:
    """Read Terms and represented tables for GSF model export."""
    return get_neo4j_conn().query_read(
        f"""
        MATCH (db:{Labels.DB} {{name: $database_name}})-[:{Edges.CONTAINS}]->
              (schema:{Labels.SCHEMA})-[:{Edges.CONTAINS}]->
              (table:{Labels.TABLE})-[:{REL_REPRESENTS}]->
              (term:{LABEL_TERM} {{source: $source}})
        WHERE {_model_scope("table")}
        RETURN term.name AS term_name, term.description AS description,
               term.synonyms AS synonyms,
               term.import_ai_context AS ai_context,
               term.import_original AS original, table.pk AS pk,
               db.name AS database_name, schema.name AS schema_name,
               table.name AS table_name
        ORDER BY schema_name, table_name, term_name
        """,
        {
            "database_name": database_name,
            "model_name": model_name,
            "source": SEMANTIC_SOURCE,
        },
    )


def read_column_attributes(
    *,
    database_name: str,
    model_name: str | None,
) -> list[dict[str, Any]]:
    """Read ColumnAttributes for GSF model export."""
    return get_neo4j_conn().query_read(
        f"""
        MATCH (:{Labels.DB} {{name: $database_name}})-[:{Edges.CONTAINS}]->
              (:{Labels.SCHEMA})-[:{Edges.CONTAINS}]->
              (table:{Labels.TABLE})-[:{REL_REPRESENTS}]->
              (term:{LABEL_TERM} {{source: $source}})
        WHERE {_model_scope("table")}
        MATCH (table)-[:{Edges.CONTAINS}]->(column:{Labels.COLUMN})
              -[:{REL_HAS_ATTRIBUTE}]->
              (attribute:{LABEL_COLUMN_ATTRIBUTE})
              -[:{REL_PROPERTY_OF}]->(term)
        WHERE coalesce(attribute.import_synthetic, false) = false
        RETURN term.name AS term_name, attribute.name AS name,
               attribute.source_column AS source_column,
               attribute.description AS description,
               attribute.datatype AS datatype,
               attribute.import_ai_context AS ai_context,
               attribute.import_original AS original
        ORDER BY term_name, name
        """,
        {
            "database_name": database_name,
            "model_name": model_name,
            "source": SEMANTIC_SOURCE,
        },
    )


def read_sql_attributes(
    *,
    database_name: str,
    model_name: str | None,
) -> list[dict[str, Any]]:
    """Read SqlAttributes and their SQL text for GSF model export."""
    return get_neo4j_conn().query_read(
        f"""
        MATCH (:{Labels.DB} {{name: $database_name}})-[:{Edges.CONTAINS}]->
              (:{Labels.SCHEMA})-[:{Edges.CONTAINS}]->
              (table:{Labels.TABLE})-[:{REL_REPRESENTS}]->
              (term:{LABEL_TERM} {{source: $source}})
        WHERE {_model_scope("table")}
        MATCH (attribute:{LABEL_SQL_ATTRIBUTE})-[:{REL_PROPERTY_OF}]->(term)
        MATCH (attribute)-[:{Edges.HAS_SQL}]->(sql:{Labels.SQL})
        OPTIONAL MATCH (sql)-[:{Edges.SQL}]->(referenced_table:{Labels.TABLE})
              -[:{REL_REPRESENTS}]->(referenced_term:{LABEL_TERM})
        WITH term, attribute, sql,
             collect(DISTINCT referenced_term.name) AS table_refs
        RETURN term.name AS term_name, attribute.name AS name,
               attribute.description AS description,
               attribute.expression AS expression,
               attribute.import_kind AS kind,
               attribute.import_original AS original,
               sql.sql_full_query AS sql, attribute.id AS id, table_refs
        ORDER BY term_name, name
        """,
        {
            "database_name": database_name,
            "model_name": model_name,
            "source": SEMANTIC_SOURCE,
        },
    )


def read_relationships(
    *,
    database_name: str,
    model_name: str | None,
) -> list[dict[str, Any]]:
    """Read semantic FKs grouped into GSF model relationships."""
    return get_neo4j_conn().query_read(
        f"""
        MATCH (:{Labels.DB} {{name: $database_name}})-[:{Edges.CONTAINS}]->
              (:{Labels.SCHEMA})-[:{Edges.CONTAINS}]->
              (from_table:{Labels.TABLE})-[:{REL_REPRESENTS}]->
              (term:{LABEL_TERM} {{source: $source}})
        WHERE {_model_scope("from_table")}
        MATCH (from_table)-[:{Edges.CONTAINS}]->
              (from_column:{Labels.COLUMN})
              -[relationship:{REL_SEMANTIC_FK}]->
              (to_attribute:{LABEL_COLUMN_ATTRIBUTE})
              <-[:{REL_HAS_ATTRIBUTE}]-(to_column:{Labels.COLUMN})
              <-[:{Edges.CONTAINS}]-(to_table:{Labels.TABLE})
        MATCH (to_attribute)-[:{REL_PROPERTY_OF}]->
              (to_term:{LABEL_TERM} {{source: $source}})
        WITH relationship, term, to_term, from_column, to_column,
             coalesce(
                 relationship.import_name,
                 term.name + '_to_' + to_term.name
             ) AS relationship_name
        ORDER BY coalesce(relationship.import_index, 0)
        RETURN relationship_name AS name,
               coalesce(
                   relationship.import_from_dataset,
                   term.name
               ) AS from_dataset,
               coalesce(
                   relationship.import_to_dataset,
                   to_term.name
               ) AS to_dataset,
               collect(from_column.name) AS from_columns,
               collect(to_column.name) AS to_columns,
               head(collect(relationship.import_original)) AS original
        ORDER BY name
        """,
        {
            "database_name": database_name,
            "model_name": model_name,
            "source": SEMANTIC_SOURCE,
        },
    )
