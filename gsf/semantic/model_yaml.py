# SPDX-FileCopyrightText: Copyright (c) 2026, NVIDIA CORPORATION & AFFILIATES.
# All rights reserved.
# SPDX-License-Identifier: Apache-2.0

"""Standalone GSF semantic-model YAML import and export."""

from __future__ import annotations

import json
from collections import Counter, defaultdict
from dataclasses import asdict, dataclass
from typing import Any, Mapping

import yaml

from gsf.dal import semantic_models as model_dal
from gsf.semantic.embed import build_semantic_embedder
from gsf.server.sql_attributes.service import refresh_sql_attribute_embeddings
from gsf.server.sql_utils import get_dialects, get_schemas, validate_sql
from gsf.vdb import get_semantic_vdb

GSF_MODEL_VERSION = "1.0"


class SemanticModelYamlError(Exception):
    """Raised when a GSF semantic-model document cannot be processed."""


@dataclass(frozen=True)
class ImportSummary:
    """Counts and side effects from a native GSF model import."""

    model: str
    terms: int
    column_attributes: int
    sql_attributes: int
    semantic_foreign_keys: int
    embeddings: int

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


def import_model_yaml(
    model_yaml: str,
    *,
    database_name: str | None = None,
    replace: bool = True,
    validate_queries: bool = True,
    embed: bool = True,
) -> ImportSummary:
    """Import one standalone GSF semantic model into the live graph."""
    try:
        root = _parse_model_yaml(model_yaml)
        plan = _build_import_plan(root, database_name=database_name)
        _resolve_catalog_sources(plan, required_database=database_name)
        _validate_term_conflicts(plan, replace=replace)
        _validate_table_conflicts(plan)
        _finish_import_plan(plan)
        _validate_catalog_columns(plan)
        _validate_sql_attribute_names(plan)
        if validate_queries:
            _validate_sql_queries(plan)
    except SemanticModelYamlError:
        raise
    except Exception as exc:
        raise SemanticModelYamlError(
            f"Failed to validate GSF semantic model: {exc}"
        ) from exc

    model_name = plan["model"]["model_name"]
    try:
        result = model_dal.apply_import_plan(
            model=plan["model"],
            datasets=plan["datasets"],
            column_attributes=plan["column_attributes"],
            sql_attributes=plan["sql_attributes"],
            relationships=plan["relationships"],
            replace=replace,
        )
    except model_dal.SemanticModelPersistenceError as exc:
        raise SemanticModelYamlError(str(exc)) from exc
    except Exception as exc:
        raise SemanticModelYamlError(
            f"Neo4j transaction failed while importing {model_name!r}: {exc}"
        ) from exc

    dataset_rows = result["datasets"]
    column_rows = result["column_attributes"]
    sql_rows = result["sql_attributes"]
    stale_ids = result["stale_ids"]

    try:
        if stale_ids:
            _delete_semantic_embeddings(
                database_name=plan["database_name"],
                ids=stale_ids,
            )
            model_dal.clear_pending_embedding_deletes(model_name)

        embedding_count = 0
        if embed:
            embedding_count = _embed_imported_model(
                database_name=plan["database_name"],
                plan=plan,
                dataset_rows=dataset_rows,
                column_rows=column_rows,
                sql_rows=sql_rows,
            )
    except Exception as exc:
        raise SemanticModelYamlError(
            f"Model {model_name!r} was imported into Neo4j, but semantic "
            f"embedding refresh failed: {exc}"
        ) from exc

    return ImportSummary(
        model=model_name,
        terms=len(plan["datasets"]),
        column_attributes=len(plan["column_attributes"]),
        sql_attributes=len(plan["sql_attributes"]),
        semantic_foreign_keys=len(plan["relationships"]),
        embeddings=embedding_count,
    )


def export_model_yaml(
    *,
    database_name: str,
    model_name: str | None = None,
) -> str:
    """Export the GSF semantic graph as standalone GSF YAML."""
    try:
        model_rows = model_dal.read_models(
            database_name=database_name,
            model_name=model_name,
        )
    except Exception as exc:
        raise SemanticModelYamlError(
            f"Failed to read GSF model metadata: {exc}"
        ) from exc

    effective_model_name = model_name
    if model_name is None:
        if len(model_rows) > 1:
            names = ", ".join(str(row["name"]) for row in model_rows)
            raise SemanticModelYamlError(
                "Multiple GSF models exist for this database; choose "
                f"--model-name from: {names}"
            )
        if len(model_rows) == 1:
            effective_model_name = str(model_rows[0]["name"])

    try:
        dataset_rows = model_dal.read_datasets(
            database_name=database_name,
            model_name=effective_model_name,
        )
        column_rows = model_dal.read_column_attributes(
            database_name=database_name,
            model_name=effective_model_name,
        )
        sql_rows = model_dal.read_sql_attributes(
            database_name=database_name,
            model_name=effective_model_name,
        )
        relationship_rows = model_dal.read_relationships(
            database_name=database_name,
            model_name=effective_model_name,
        )
    except Exception as exc:
        raise SemanticModelYamlError(
            f"Failed to read semantic graph from GSF: {exc}"
        ) from exc

    if not dataset_rows:
        scope = f" model {model_name!r}" if model_name else ""
        raise SemanticModelYamlError(
            f"No semantic terms found for database {database_name!r}{scope}"
        )

    root = _build_model_export(
        database_name=database_name,
        requested_model_name=effective_model_name,
        model_row=model_rows[0] if model_rows else None,
        dataset_rows=dataset_rows,
        column_rows=column_rows,
        sql_rows=sql_rows,
        relationship_rows=relationship_rows,
    )
    return yaml.safe_dump(
        root,
        default_flow_style=False,
        sort_keys=False,
        allow_unicode=True,
    )


def _parse_model_yaml(model_yaml: str) -> dict[str, Any]:
    try:
        root = yaml.safe_load(model_yaml)
    except yaml.YAMLError as exc:
        raise SemanticModelYamlError(f"Invalid GSF YAML: {exc}") from exc
    if not isinstance(root, dict):
        raise SemanticModelYamlError("Invalid GSF YAML: expected a mapping at the root")
    unknown = sorted(set(root) - {"version", "model", "terms", "semantic_foreign_keys"})
    if unknown:
        raise SemanticModelYamlError(
            "Unsupported GSF root properties: " + ", ".join(unknown)
        )
    if str(root.get("version", "")) != GSF_MODEL_VERSION:
        raise SemanticModelYamlError(
            f"Unsupported GSF model version {root.get('version')!r}; "
            f"supported version is {GSF_MODEL_VERSION!r}"
        )
    model = root.get("model")
    if not isinstance(model, dict) or not model.get("name"):
        raise SemanticModelYamlError("'model.name' is required")
    terms = root.get("terms")
    if not isinstance(terms, list) or not terms:
        raise SemanticModelYamlError("'terms' must be a non-empty list")
    relationships = root.get("semantic_foreign_keys", [])
    if not isinstance(relationships, list):
        raise SemanticModelYamlError("'semantic_foreign_keys' must be a list")
    return root


def _build_import_plan(
    root: dict[str, Any],
    *,
    database_name: str | None,
) -> dict[str, Any]:
    source_model = root["model"]
    declared_database = source_model.get("database")
    if database_name and declared_database and database_name != declared_database:
        raise SemanticModelYamlError(
            f"Model database {declared_database!r} does not match {database_name!r}"
        )
    default_database = database_name or declared_database
    model_name = str(source_model["name"])
    plan: dict[str, Any] = {
        "model": {
            "model_name": model_name,
            "format_version": GSF_MODEL_VERSION,
            "description": source_model.get("description"),
            "ai_context": _json_or_none(source_model.get("ai_context")),
            "original_root": json.dumps({"version": GSF_MODEL_VERSION}),
            "original_model": json.dumps(source_model),
        },
        "database_name": default_database,
        "datasets": [],
        "column_attributes": [],
        "sql_attributes": [],
        "relationships": [],
        "_source_terms": root["terms"],
        "_source_relationships": root.get("semantic_foreign_keys") or [],
    }

    seen_names: set[str] = set()
    for term in root["terms"]:
        if not isinstance(term, dict) or not term.get("name"):
            raise SemanticModelYamlError("Every term must be a mapping with a name")
        name = str(term["name"])
        if name in seen_names:
            raise SemanticModelYamlError(f"Duplicate term name {name!r}")
        seen_names.add(name)
        source = _parse_source(term.get("source"), default_database)
        plan["datasets"].append(
            {
                "name": name,
                "description": term.get("description"),
                "synonyms": [
                    str(value) for value in term.get("synonyms") or [] if value
                ],
                "ai_context": _json_or_none(term.get("ai_context")),
                "database": source["database"],
                "schema": source["schema"],
                "table": source["table"],
                "original": json.dumps(term),
            }
        )
    return plan


def _resolve_catalog_sources(
    plan: dict[str, Any],
    *,
    required_database: str | None,
) -> None:
    rows = model_dal.resolve_catalog_sources(plan["datasets"])
    matches: dict[str, list[dict[str, Any]]] = defaultdict(list)
    for row in rows:
        matches[str(row["name"])].append(row)

    resolved_databases: set[str] = set()
    for dataset in plan["datasets"]:
        found = matches.get(dataset["name"], [])
        if not found:
            requested = ".".join(
                str(part)
                for part in (
                    dataset["database"],
                    dataset["schema"],
                    dataset["table"],
                )
                if part
            )
            raise SemanticModelYamlError(
                f"Term {dataset['name']!r} source {requested!r} was not "
                "found. Ingest the datasource into GSF first."
            )
        if len(found) > 1:
            choices = ", ".join(
                f"{row['database']}.{row['schema']}.{row['table']}" for row in found
            )
            raise SemanticModelYamlError(
                f"Term {dataset['name']!r} source is ambiguous: {choices}"
            )
        resolved = found[0]
        if required_database and resolved["database"] != required_database:
            raise SemanticModelYamlError(
                f"Term {dataset['name']!r} resolved to database "
                f"{resolved['database']!r}, expected {required_database!r}"
            )
        resolved_databases.add(str(resolved["database"]))
        dataset.update(
            {
                "database": resolved["database"],
                "schema": resolved["schema"],
                "table": resolved["table"],
                "table_id": resolved["table_id"],
            }
        )

    if len(resolved_databases) != 1:
        raise SemanticModelYamlError(
            "A GSF semantic model must resolve to exactly one database; "
            f"found: {', '.join(sorted(resolved_databases))}"
        )
    plan["database_name"] = next(iter(resolved_databases))
    plan["model"]["database_name"] = plan["database_name"]


def _validate_term_conflicts(
    plan: dict[str, Any],
    *,
    replace: bool,
) -> None:
    conflicts = model_dal.find_term_conflicts(
        plan["datasets"],
        model_name=plan["model"]["model_name"],
        replace=replace,
    )
    if conflicts:
        names = ", ".join(str(row["name"]) for row in conflicts)
        raise SemanticModelYamlError(
            "GSF Term names are already owned outside this model: " + names
        )


def _validate_table_conflicts(plan: dict[str, Any]) -> None:
    conflicts = model_dal.find_table_conflicts(
        plan["datasets"],
        model_name=plan["model"]["model_name"],
    )
    if conflicts:
        names = ", ".join(str(row["name"]) for row in conflicts)
        raise SemanticModelYamlError(
            "Physical tables are already represented outside this model: " + names
        )


def _finish_import_plan(plan: dict[str, Any]) -> None:
    terms_by_name = {row["name"]: row for row in plan["datasets"]}
    source_terms = {str(term["name"]): term for term in plan.pop("_source_terms")}
    preferred_dialects = _preferred_expression_dialects(
        get_dialects(plan["database_name"])
    )

    attribute_keys: set[tuple[str, str]] = set()
    sql_names: list[str] = []
    for term_name, term in source_terms.items():
        source = terms_by_name[term_name]
        columns = term.get("column_attributes") or []
        sql_attributes = term.get("sql_attributes") or []
        if not isinstance(columns, list) or not isinstance(sql_attributes, list):
            raise SemanticModelYamlError(f"Term {term_name!r} attributes must be lists")

        for attribute in columns:
            if not isinstance(attribute, dict) or not attribute.get("name"):
                raise SemanticModelYamlError(
                    f"Every column attribute in {term_name!r} needs a name"
                )
            source_column = str(attribute.get("source_column") or "")
            if not source_column:
                raise SemanticModelYamlError(
                    f"Column attribute {attribute['name']!r} needs 'source_column'"
                )
            key = (term_name, source_column)
            if key in attribute_keys:
                raise SemanticModelYamlError(
                    f"Multiple attributes in term {term_name!r} map to "
                    f"physical column {source_column!r}"
                )
            attribute_keys.add(key)
            plan["column_attributes"].append(
                {
                    "dataset": term_name,
                    "name": str(attribute["name"]),
                    "source_column": source_column,
                    "description": attribute.get("description"),
                    "ai_context": _json_or_none(attribute.get("ai_context")),
                    "original": json.dumps(attribute),
                    "synthetic": False,
                    "table": _table_ref(source),
                }
            )

        for attribute in sql_attributes:
            if not isinstance(attribute, dict) or not attribute.get("name"):
                raise SemanticModelYamlError(
                    f"Every SQL attribute in {term_name!r} needs a name"
                )
            name = str(attribute["name"])
            kind = str(attribute.get("kind") or "")
            if kind not in {"attribute", "field", "metric"}:
                raise SemanticModelYamlError(
                    f"SQL attribute {name!r} kind must be 'attribute', "
                    "'field', or 'metric'"
                )
            expressions = attribute.get("expressions")
            expression = _pick_expression(
                expressions,
                name,
                preferred_dialects,
            )
            sql = str(attribute.get("sql") or "").strip()
            if not sql:
                raise SemanticModelYamlError(
                    f"SQL attribute {name!r} requires full 'sql'"
                )
            ref_names = attribute.get("table_refs") or [term_name]
            if not isinstance(ref_names, list) or not ref_names:
                raise SemanticModelYamlError(
                    f"SQL attribute {name!r} requires 'table_refs'"
                )
            unknown_refs = sorted(
                str(ref) for ref in ref_names if ref not in terms_by_name
            )
            if unknown_refs:
                raise SemanticModelYamlError(
                    f"SQL attribute {name!r} references unknown terms: "
                    + ", ".join(unknown_refs)
                )
            plan["sql_attributes"].append(
                {
                    "term": term_name,
                    "name": name,
                    "description": attribute.get("description"),
                    "expression": expression,
                    "sql": sql,
                    "kind": kind,
                    "table_refs": [
                        _table_ref(terms_by_name[str(ref)]) for ref in ref_names
                    ],
                    "original": json.dumps(attribute),
                }
            )
            sql_names.append(name)

    duplicates = sorted(name for name, count in Counter(sql_names).items() if count > 1)
    if duplicates:
        raise SemanticModelYamlError(
            "GSF SqlAttribute names are global; duplicate names: "
            + ", ".join(duplicates)
        )

    for relationship in plan.pop("_source_relationships"):
        converted = _relationship_to_plan(relationship, terms_by_name)
        plan["relationships"].append(converted)
        for target_column in converted["to_columns"]:
            key = (converted["to_dataset"], target_column)
            if key not in attribute_keys:
                target = terms_by_name[converted["to_dataset"]]
                plan["column_attributes"].append(
                    {
                        "dataset": converted["to_dataset"],
                        "name": target_column,
                        "source_column": target_column,
                        "description": None,
                        "ai_context": None,
                        "original": None,
                        "synthetic": True,
                        "table": _table_ref(target),
                    }
                )
                attribute_keys.add(key)


def _relationship_to_plan(
    relationship: Any,
    terms: dict[str, dict[str, Any]],
) -> dict[str, Any]:
    if not isinstance(relationship, dict) or not relationship.get("name"):
        raise SemanticModelYamlError("Every semantic foreign key must have a name")
    name = str(relationship["name"])
    from_term = relationship.get("from_term")
    to_term = relationship.get("to_term")
    if from_term not in terms or to_term not in terms:
        raise SemanticModelYamlError(
            f"Semantic foreign key {name!r} references an unknown term"
        )
    from_columns = relationship.get("from_columns") or []
    to_columns = relationship.get("to_columns") or []
    if not from_columns or len(from_columns) != len(to_columns):
        raise SemanticModelYamlError(
            f"Semantic foreign key {name!r} must have equal, non-empty "
            "from_columns and to_columns"
        )
    return {
        "name": name,
        "from_dataset": from_term,
        "to_dataset": to_term,
        "from_columns": list(from_columns),
        "to_columns": list(to_columns),
        "from_table": _table_ref(terms[str(from_term)]),
        "to_table": _table_ref(terms[str(to_term)]),
        "original": json.dumps(relationship),
    }


def _validate_catalog_columns(plan: dict[str, Any]) -> None:
    required: dict[tuple[str, str], dict[str, Any]] = {}
    by_term = {row["name"]: row for row in plan["datasets"]}
    for attribute in plan["column_attributes"]:
        term = by_term[attribute["dataset"]]
        required[(attribute["dataset"], attribute["source_column"])] = {
            "dataset": attribute["dataset"],
            "column": attribute["source_column"],
            **_table_ref(term),
        }
    for relationship in plan["relationships"]:
        for column in relationship["from_columns"]:
            required[(relationship["from_dataset"], column)] = {
                "dataset": relationship["from_dataset"],
                "column": column,
                **relationship["from_table"],
            }
        for column in relationship["to_columns"]:
            required[(relationship["to_dataset"], column)] = {
                "dataset": relationship["to_dataset"],
                "column": column,
                **relationship["to_table"],
            }
    rows = model_dal.resolve_catalog_columns(list(required.values()))
    found = {(str(row["dataset"]), str(row["column"])) for row in rows}
    missing = sorted(set(required) - found)
    if missing:
        formatted = ", ".join(f"{term}.{column}" for term, column in missing)
        raise SemanticModelYamlError(
            "Columns required by the GSF model were not found: " + formatted
        )


def _validate_sql_attribute_names(plan: dict[str, Any]) -> None:
    if not plan["sql_attributes"]:
        return
    conflicts = model_dal.find_sql_attribute_conflicts(
        plan["sql_attributes"],
        model_name=plan["model"]["model_name"],
    )
    if conflicts:
        names = ", ".join(str(row["name"]) for row in conflicts)
        raise SemanticModelYamlError(
            "GSF SqlAttribute names are already owned outside this model: " + names
        )


def _validate_sql_queries(plan: dict[str, Any]) -> None:
    database_name = plan["database_name"]
    dialects = [
        dialect
        for dialect in get_dialects(database_name)
        if dialect not in {"generic", "ansi"}
    ] or ["postgres"]
    schemas = get_schemas(database_name)
    for row in plan["sql_attributes"]:
        try:
            validate_sql(row["sql"], dialects, schemas)
        except Exception as exc:
            raise SemanticModelYamlError(
                f"Invalid SQL for {row['kind']} {row['name']!r}: {exc}"
            ) from exc


def _pick_expression(
    expressions: Any,
    name: str,
    preferred_dialects: list[str],
) -> str:
    if not isinstance(expressions, list) or not expressions:
        raise SemanticModelYamlError(
            f"SQL attribute {name!r} requires non-empty 'expressions'"
        )
    by_dialect: dict[str, str] = {}
    first: str | None = None
    for item in expressions:
        if not isinstance(item, dict) or not item.get("expression"):
            continue
        value = str(item["expression"])
        first = first or value
        dialect = str(item.get("dialect") or "").upper()
        if dialect:
            by_dialect[dialect] = value
    selected = next(
        (
            by_dialect[dialect]
            for dialect in preferred_dialects
            if dialect in by_dialect
        ),
        by_dialect.get("ANSI_SQL") or first,
    )
    if not selected:
        raise SemanticModelYamlError(f"SQL attribute {name!r} has no usable expression")
    return selected


def _preferred_expression_dialects(gsf_dialects: list[str]) -> list[str]:
    mapping = {
        "snowflake": "SNOWFLAKE",
        "databricks": "DATABRICKS",
        "bigquery": "BIGQUERY",
        "ansi": "ANSI_SQL",
        "generic": "ANSI_SQL",
        "postgres": "ANSI_SQL",
        "postgresql": "ANSI_SQL",
    }
    result: list[str] = []
    for dialect in gsf_dialects:
        mapped = mapping.get(dialect.casefold())
        if mapped and mapped not in result:
            result.append(mapped)
    if "ANSI_SQL" not in result:
        result.append("ANSI_SQL")
    return result


def _build_model_export(
    *,
    database_name: str,
    requested_model_name: str | None,
    model_row: dict[str, Any] | None,
    dataset_rows: list[dict[str, Any]],
    column_rows: list[dict[str, Any]],
    sql_rows: list[dict[str, Any]],
    relationship_rows: list[dict[str, Any]],
) -> dict[str, Any]:
    term_counts = Counter(str(row["term_name"]) for row in dataset_rows)
    multi_table = sorted(name for name, count in term_counts.items() if count > 1)
    if multi_table:
        raise SemanticModelYamlError(
            "Terms represented by multiple tables cannot be serialized: "
            + ", ".join(multi_table)
        )

    model: dict[str, Any] = {
        "name": (
            (model_row or {}).get("name")
            or requested_model_name
            or f"{database_name}_semantic_model"
        ),
        "database": database_name,
    }
    if (model_row or {}).get("description") is not None:
        model["description"] = model_row["description"]
    model_ai = _load_json_value((model_row or {}).get("ai_context"))
    if model_ai is not None:
        model["ai_context"] = model_ai
    original_model = _load_json_dict((model_row or {}).get("original_model"))
    if original_model.get("metadata") is not None:
        model["metadata"] = original_model["metadata"]

    columns_by_term: dict[str, list[dict[str, Any]]] = defaultdict(list)
    for row in column_rows:
        columns_by_term[str(row["term_name"])].append(_column_row_to_native(row))

    sql_by_term: dict[str, list[dict[str, Any]]] = defaultdict(list)
    for row in sql_rows:
        row = dict(row)
        if row.get("kind") not in {"attribute", "field", "metric"}:
            row["kind"] = "attribute"
        sql_by_term[str(row["term_name"])].append(_sql_row_to_native(row))

    terms: list[dict[str, Any]] = []
    for row in dataset_rows:
        original_term = _load_json_dict(row.get("original"))
        term: dict[str, Any] = {
            "name": row["term_name"],
            "source": {
                "database": row["database_name"],
                "schema": row["schema_name"],
                "table": row["table_name"],
            },
        }
        if row.get("description") is not None:
            term["description"] = row["description"]
        synonyms = [str(value) for value in row.get("synonyms") or [] if value]
        if synonyms:
            term["synonyms"] = synonyms
        ai_context = _load_json_value(row.get("ai_context"))
        if ai_context is not None:
            term["ai_context"] = ai_context
        primary_key = original_term.get("primary_key") or row.get("pk")
        if primary_key:
            term["primary_key"] = (
                primary_key if isinstance(primary_key, list) else [primary_key]
            )
        if original_term.get("unique_keys") is not None:
            term["unique_keys"] = original_term["unique_keys"]
        if original_term.get("metadata") is not None:
            term["metadata"] = original_term["metadata"]
        columns = columns_by_term.get(str(row["term_name"]), [])
        sql_attributes = sql_by_term.get(str(row["term_name"]), [])
        if columns:
            term["column_attributes"] = columns
        if sql_attributes:
            term["sql_attributes"] = sql_attributes
        terms.append(term)

    root: dict[str, Any] = {
        "version": GSF_MODEL_VERSION,
        "model": model,
        "terms": terms,
    }
    if relationship_rows:
        root["semantic_foreign_keys"] = [
            _relationship_row_to_native(row) for row in relationship_rows
        ]
    return root


def _column_row_to_native(row: dict[str, Any]) -> dict[str, Any]:
    original = _load_json_dict(row.get("original"))
    attribute: dict[str, Any] = {
        "name": row["name"],
        "source_column": row["source_column"],
    }
    if row.get("description") is not None:
        attribute["description"] = row["description"]
    ai_context = _load_json_value(row.get("ai_context"))
    if ai_context is not None:
        attribute["ai_context"] = ai_context
    expressions = original.get("expressions")
    if isinstance(expressions, list) and expressions:
        attribute["expressions"] = expressions
    if original.get("dimension") is not None:
        attribute["dimension"] = original["dimension"]
    if original.get("metadata") is not None:
        attribute["metadata"] = original["metadata"]
    return attribute


def _sql_row_to_native(row: dict[str, Any]) -> dict[str, Any]:
    original = _load_json_dict(row.get("original"))
    expressions = original.get("expressions")
    if not isinstance(expressions, list) or not expressions:
        expressions = [
            {
                "dialect": "ANSI_SQL",
                "expression": row.get("expression") or row.get("sql") or "",
            }
        ]
    result: dict[str, Any] = {
        "name": row["name"],
        "kind": row["kind"],
        "expressions": expressions,
        "sql": row.get("sql") or "",
        "table_refs": list(row.get("table_refs") or [row["term_name"]]),
    }
    if row.get("description") is not None:
        result["description"] = row["description"]
    if original.get("ai_context") is not None:
        result["ai_context"] = original["ai_context"]
    if original.get("dimension") is not None:
        result["dimension"] = original["dimension"]
    if original.get("metadata") is not None:
        result["metadata"] = original["metadata"]
    return result


def _relationship_row_to_native(row: dict[str, Any]) -> dict[str, Any]:
    original = _load_json_dict(row.get("original"))
    result: dict[str, Any] = {
        "name": row["name"],
        "from_term": row["from_dataset"],
        "to_term": row["to_dataset"],
        "from_columns": list(row["from_columns"]),
        "to_columns": list(row["to_columns"]),
    }
    if original.get("ai_context") is not None:
        result["ai_context"] = original["ai_context"]
    if original.get("metadata") is not None:
        result["metadata"] = original["metadata"]
    return result


def _parse_source(
    source: Any,
    default_database: str | None,
) -> dict[str, str | None]:
    if isinstance(source, dict):
        database = source.get("database") or default_database
        schema = source.get("schema")
        table = source.get("table")
        if not table:
            raise SemanticModelYamlError("Term source requires 'table'")
        return {
            "database": str(database) if database else None,
            "schema": str(schema) if schema else None,
            "table": str(table),
        }
    value = str(source or "").strip()
    if not value:
        raise SemanticModelYamlError("Every term needs a physical 'source'")
    parts = _split_identifier(value)
    if len(parts) == 3:
        database, schema, table = parts
    elif len(parts) == 2:
        database, (schema, table) = default_database, parts
    elif len(parts) == 1:
        database, schema, table = default_database, None, parts[0]
    else:
        raise SemanticModelYamlError(
            f"Source {value!r} must be table, schema.table, or database.schema.table"
        )
    return {"database": database, "schema": schema, "table": table}


def _split_identifier(value: str) -> list[str]:
    parts: list[str] = []
    current: list[str] = []
    quote: str | None = None
    for char in value:
        if char in ('"', "`"):
            quote = None if quote == char else char if quote is None else quote
        elif char == "." and quote is None:
            parts.append("".join(current).strip())
            current = []
            continue
        current.append(char)
    parts.append("".join(current).strip())
    return [
        part[1:-1]
        if len(part) > 1 and part[0] == part[-1] and part[0] in ('"', "`")
        else part
        for part in parts
    ]


def _table_ref(source: Mapping[str, Any]) -> dict[str, str]:
    return {
        "database": str(source["database"]),
        "schema": str(source["schema"]),
        "table": str(source["table"]),
    }


def _delete_semantic_embeddings(
    *,
    database_name: str,
    ids: list[str],
) -> None:
    vdb = get_semantic_vdb(database_name=database_name)
    for item_id in set(ids):
        vdb.delete_by_id(item_id)


def _embed_imported_model(
    *,
    database_name: str,
    plan: dict[str, Any],
    dataset_rows: list[dict[str, Any]],
    column_rows: list[dict[str, Any]],
    sql_rows: list[dict[str, Any]],
) -> int:
    embedder = build_semantic_embedder(database_name, reset=False)
    if embedder is None:
        return 0

    plan_by_name = {row["name"]: row for row in plan["datasets"]}
    attributes_by_term: dict[str, list[dict[str, Any]]] = defaultdict(list)
    for row in column_rows:
        attributes_by_term[str(row["term_name"])].append(row)

    for row in dataset_rows:
        embedder.vdb.delete_by_id(str(row["term_id"]))
    for row in column_rows:
        if row.get("id"):
            embedder.vdb.delete_by_id(str(row["id"]))

    count = 0
    for row in dataset_rows:
        name = str(row["name"])
        source = plan_by_name[name]
        count += embedder.embed_term(
            {
                "id": row["term_id"],
                "name": name,
                "description": source.get("description") or "",
                "synonyms": source.get("synonyms") or [],
                "schema_names": [source["schema"]],
            },
            attributes_by_term.get(name, []),
        )

    sql_ids = [str(row["id"]) for row in sql_rows if row.get("id")]
    count += refresh_sql_attribute_embeddings(
        sql_ids,
        database_name=database_name,
    )
    return count


def _json_or_none(value: Any) -> str | None:
    return json.dumps(value) if value is not None else None


def _load_json_value(value: Any) -> Any:
    if value is None or not isinstance(value, str):
        return value
    try:
        return json.loads(value)
    except json.JSONDecodeError:
        return value


def _load_json_dict(value: Any) -> dict[str, Any]:
    loaded = _load_json_value(value)
    return loaded if isinstance(loaded, dict) else {}
