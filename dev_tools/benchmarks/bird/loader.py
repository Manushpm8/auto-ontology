# SPDX-FileCopyrightText: Copyright (c) 2024-25, NVIDIA CORPORATION & AFFILIATES.
# All rights reserved.
# SPDX-License-Identifier: Apache-2.0

"""BIRD benchmark bulk loader for DuckDB.

Provides ``load_bird``: loads BIRD SQLite databases into DuckDB,
one schema per database, with full data and foreign key constraints.

Each database in ``<bird_dir>/dev_databases/<db_name>/<db_name>.sqlite``
becomes a DuckDB schema, so you can query:

    conn.execute("SELECT * FROM california_schools.schools LIMIT 5")

Foreign keys from the original SQLite databases are preserved in DuckDB,
enabling join-path discovery and schema introspection.
"""

from __future__ import annotations

import logging
import re
import sqlite3
from pathlib import Path
from typing import Any, Dict, List, Tuple, Union

import duckdb

logger = logging.getLogger(__name__)

SQLITE_TO_DUCKDB_TYPE = {
    "integer": "BIGINT",
    "int": "BIGINT",
    "bigint": "BIGINT",
    "smallint": "SMALLINT",
    "tinyint": "TINYINT",
    "real": "DOUBLE",
    "float": "DOUBLE",
    "double": "DOUBLE",
    "numeric": "DOUBLE",
    "text": "VARCHAR",
    "varchar": "VARCHAR",
    "char": "VARCHAR",
    "blob": "BLOB",
    "boolean": "BOOLEAN",
    "date": "VARCHAR",
    "datetime": "VARCHAR",
    "timestamp": "VARCHAR",
}


def _sanitize(name: str) -> str:
    s = re.sub(r"[^0-9a-zA-Z_]", "_", name)
    return ("s_" + s) if s and s[0].isdigit() else s or "unnamed"


def _map_type(sqlite_type: str) -> str:
    """Map a SQLite declared type to a DuckDB type."""
    if not sqlite_type:
        return "VARCHAR"
    base = sqlite_type.strip().split("(")[0].lower()
    return SQLITE_TO_DUCKDB_TYPE.get(base, "VARCHAR")


def _get_table_info(sqlite_conn: sqlite3.Connection, table: str) -> List[Tuple]:
    """Return PRAGMA table_info rows: (cid, name, type, notnull, dflt_value, pk)."""
    return sqlite_conn.execute(f'PRAGMA table_info("{table}")').fetchall()


def _get_primary_keys(table_info: List[Tuple]) -> List[str]:
    """Extract primary key columns from table_info (pk > 0, ordered by pk index)."""
    pk_cols = [(row[5], row[1]) for row in table_info if row[5] > 0]
    pk_cols.sort(key=lambda x: x[0])
    return [col for _, col in pk_cols]


def _get_foreign_keys(sqlite_conn: sqlite3.Connection, table: str) -> List[Dict[str, str]]:
    """Return foreign keys: list of {from_col, to_table, to_col}."""
    rows = sqlite_conn.execute(f'PRAGMA foreign_key_list("{table}")').fetchall()
    fks = []
    for row in rows:
        fks.append({"from_col": row[3], "to_table": row[2], "to_col": row[4]})
    return fks


def _topological_sort(tables: List[str], fk_map: Dict[str, List[Dict[str, str]]]) -> List[str]:
    """Sort tables so that referenced tables come before referencing ones.

    Falls back gracefully for circular references by appending remaining tables.
    """
    table_set = set(tables)
    in_degree = {t: 0 for t in tables}
    for table in tables:
        for fk in fk_map.get(table, []):
            ref = fk["to_table"]
            if ref in table_set and ref != table:
                in_degree[table] += 1

    queue = [t for t in tables if in_degree[t] == 0]
    sorted_tables = []

    while queue:
        node = queue.pop(0)
        sorted_tables.append(node)
        for table in tables:
            if table in [t for t in sorted_tables]:
                continue
            for fk in fk_map.get(table, []):
                if fk["to_table"] == node and fk["to_table"] != table:
                    in_degree[table] -= 1
                    if in_degree[table] == 0:
                        queue.append(table)

    for t in tables:
        if t not in sorted_tables:
            sorted_tables.append(t)

    return sorted_tables


def _build_create_table_sql(
    schema: str,
    table: str,
    table_info: List[Tuple],
    pk_cols: List[str],
    foreign_keys: List[Dict[str, str]],
    valid_targets: set,
) -> str:
    """Build a CREATE TABLE statement with columns, PK, and FK constraints."""
    sanitized_table = _sanitize(table)
    col_defs = []
    for row in table_info:
        col_name = row[1]
        col_type = _map_type(row[2])
        col_defs.append(f'    "{col_name}" {col_type}')

    constraints = []
    if pk_cols:
        pk_quoted = ", ".join(f'"{c}"' for c in pk_cols)
        constraints.append(f"    PRIMARY KEY ({pk_quoted})")

    for fk in foreign_keys:
        ref_table = fk["to_table"]
        if ref_table not in valid_targets or ref_table == table:
            continue
        sanitized_ref = _sanitize(ref_table)
        from_col = fk["from_col"]
        to_col = fk["to_col"]
        if not to_col:
            continue
        constraints.append(
            f'    FOREIGN KEY ("{from_col}") REFERENCES "{schema}"."{sanitized_ref}"("{to_col}")'
        )

    all_parts = col_defs + constraints
    body = ",\n".join(all_parts)
    return f'CREATE TABLE "{schema}"."{sanitized_table}" (\n{body}\n)'


def load_bird(
    db_path: str,
    bird_dir: Union[str, Path],
    *,
    overwrite: bool = False,
) -> Dict[str, Any]:
    """Load BIRD SQLite databases into DuckDB using one schema per database.

    BIRD stores each database as a ``.sqlite`` file under:
      ``<bird_dir>/dev_databases/<db_name>/<db_name>.sqlite``

    Foreign keys from the SQLite sources are preserved in DuckDB.

    Parameters
    ----------
    db_path:
        Path to the DuckDB database file to create or update.
    bird_dir:
        Root of the BIRD data directory (e.g. ``~/bird/mini_dev_data``).
    overwrite:
        Drop and recreate schemas that already exist (default: False).
    """
    conn = duckdb.connect(database=str(db_path))
    try:
        conn.execute("INSTALL sqlite; LOAD sqlite;")

        root = Path(bird_dir).expanduser().resolve()
        dev_db_dir = root / "dev_databases"

        if not dev_db_dir.is_dir():
            raise ValueError(
                f"BIRD dev_databases directory not found: {dev_db_dir}\n"
                "Expected layout: <bird_dir>/dev_databases/<db_name>/<db_name>.sqlite"
            )

        sqlite_files = sorted(dev_db_dir.rglob("*.sqlite"))

        if not sqlite_files:
            raise ValueError(f"No .sqlite files found under {dev_db_dir}")

        loaded_schemas: List[str] = []
        skipped_schemas: List[str] = []
        failed: List[Dict[str, str]] = []

        existing_schemas = set(
            conn.execute(
                "SELECT schema_name FROM information_schema.schemata "
                "WHERE schema_name NOT IN ('main', 'information_schema', 'pg_catalog')"
            )
            .df()["schema_name"]
            .tolist()
        )

        for sqlite_path in sqlite_files:
            schema = _sanitize(sqlite_path.stem)

            if schema in existing_schemas and not overwrite:
                logger.debug("Skipping schema '%s' — already exists.", schema)
                skipped_schemas.append(schema)
                continue

            try:
                if overwrite and schema in existing_schemas:
                    conn.execute(f'DROP SCHEMA IF EXISTS "{schema}" CASCADE')

                conn.execute(f'CREATE SCHEMA IF NOT EXISTS "{schema}"')

                sqlite_conn = sqlite3.connect(str(sqlite_path))
                tables_raw = sqlite_conn.execute(
                    "SELECT name FROM sqlite_master WHERE type='table' AND name NOT LIKE 'sqlite_%'"
                ).fetchall()
                tables = [row[0] for row in tables_raw]
                table_set = set(tables)

                # Gather metadata for all tables
                metadata: Dict[str, Dict[str, Any]] = {}
                fk_map: Dict[str, List[Dict[str, str]]] = {}
                for table in tables:
                    info = _get_table_info(sqlite_conn, table)
                    pk_cols = _get_primary_keys(info)
                    fks = _get_foreign_keys(sqlite_conn, table)
                    metadata[table] = {"info": info, "pk_cols": pk_cols, "fks": fks}
                    fk_map[table] = fks

                sorted_tables = _topological_sort(tables, fk_map)

                # Phase 1: Create tables in dependency order
                fk_failures = set()
                for table in sorted_tables:
                    meta = metadata[table]
                    create_sql = _build_create_table_sql(
                        schema=schema,
                        table=table,
                        table_info=meta["info"],
                        pk_cols=meta["pk_cols"],
                        foreign_keys=meta["fks"],
                        valid_targets=table_set,
                    )
                    try:
                        conn.execute(create_sql)
                    except Exception as fk_exc:
                        # FK creation failed (e.g. referenced col not unique) — retry without FKs
                        logger.debug(
                            "FK constraint failed for %s.%s (%s), creating without FKs.",
                            schema, table, fk_exc,
                        )
                        fk_failures.add(table)
                        create_sql_no_fk = _build_create_table_sql(
                            schema=schema,
                            table=table,
                            table_info=meta["info"],
                            pk_cols=meta["pk_cols"],
                            foreign_keys=[],
                            valid_targets=table_set,
                        )
                        conn.execute(f'DROP TABLE IF EXISTS "{schema}"."{_sanitize(table)}"')
                        conn.execute(create_sql_no_fk)

                # Phase 2: Insert data from SQLite
                conn.execute(f"ATTACH '{sqlite_path}' AS _bird_src (TYPE sqlite, READ_ONLY)")

                try:
                    for table in sorted_tables:
                        sanitized_table = _sanitize(table)
                        conn.execute(
                            f'INSERT INTO "{schema}"."{sanitized_table}" '
                            f'SELECT * FROM _bird_src.main."{table}"'
                        )
                        logger.debug("Loaded %s → %s.%s", table, schema, sanitized_table)
                except Exception as insert_exc:
                    # FK violation in source data — rebuild schema without FKs
                    logger.warning(
                        "FK violation in '%s' (%s). Rebuilding without FK constraints.",
                        schema, insert_exc,
                    )
                    conn.execute(f'DROP SCHEMA IF EXISTS "{schema}" CASCADE')
                    conn.execute(f'CREATE SCHEMA IF NOT EXISTS "{schema}"')
                    fk_failures = set(tables)

                    for table in sorted_tables:
                        create_sql_no_fk = _build_create_table_sql(
                            schema=schema,
                            table=table,
                            table_info=metadata[table]["info"],
                            pk_cols=metadata[table]["pk_cols"],
                            foreign_keys=[],
                            valid_targets=table_set,
                        )
                        conn.execute(create_sql_no_fk)
                        sanitized_table = _sanitize(table)
                        conn.execute(
                            f'INSERT INTO "{schema}"."{sanitized_table}" '
                            f'SELECT * FROM _bird_src.main."{table}"'
                        )

                conn.execute("DETACH _bird_src")
                sqlite_conn.close()

                loaded_schemas.append(schema)
                existing_schemas.add(schema)
                fk_count = sum(
                    len(m["fks"]) for t, m in metadata.items() if t not in fk_failures
                )
                logger.info(
                    "Schema '%s' loaded (%d tables, %d FK constraints).",
                    schema, len(tables), fk_count,
                )

            except Exception as exc:
                try:
                    conn.execute("DETACH _bird_src")
                except Exception:
                    pass
                try:
                    sqlite_conn.close()
                except Exception:
                    pass
                logger.error("Failed loading schema '%s': %s", schema, exc)
                failed.append({"database": sqlite_path.stem, "schema": schema, "error": str(exc)})
    finally:
        conn.close()

    return {
        "dev_db_dir": str(dev_db_dir),
        "databases_found": len(sqlite_files),
        "loaded": len(loaded_schemas),
        "skipped": len(skipped_schemas),
        "failed": len(failed),
        "schemas": loaded_schemas,
        "failures": failed,
    }
