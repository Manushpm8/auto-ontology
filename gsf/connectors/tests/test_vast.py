# SPDX-FileCopyrightText: Copyright (c) 2024-2026, NVIDIA CORPORATION & AFFILIATES.
# All rights reserved.
# SPDX-License-Identifier: Apache-2.0

from contextlib import contextmanager
from urllib.parse import quote

import pandas as pd
import pytest
from nemo_retriever.tabular_data.ingestion.model.reserved_words import TableTypes
from pytest import MonkeyPatch

from gsf.connectors.connection_string_factory import build_connection_string
from gsf.connectors.registry import create_connector
from gsf.connectors.vast import VastDatabase, _parse_connection_string


def _connection_string(secret: str = "secret") -> str:
    return (
        f"vast://ak:{quote(secret, safe='')}@172.200.207.121:8080/my_catalog?secure=0"
    )


def test_parse_connection_string() -> None:
    db_kwargs, conn_kwargs, catalog = _parse_connection_string(
        _connection_string("secret/with@chars")
    )

    assert db_kwargs == {
        "vast.db.endpoint": "http://172.200.207.121:8080",
        "vast.db.access_key": "ak",
        "vast.db.secret_key": "secret/with@chars",
    }
    assert conn_kwargs == {}
    assert catalog == "my_catalog"


def test_parse_connection_string_secure_and_end_user() -> None:
    db_kwargs, conn_kwargs, catalog = _parse_connection_string(
        "vast://ak:sk@gsf-query-engine.example.com/cat?secure=1&end_user=alice"
    )

    assert db_kwargs["vast.db.endpoint"] == "https://gsf-query-engine.example.com"
    assert conn_kwargs == {"vast.db.end_user": "alice"}
    assert catalog == "cat"


def test_parse_connection_string_requires_secret_key() -> None:
    with pytest.raises(ValueError, match="secret key"):
        _parse_connection_string("vast://ak@172.200.207.121/cat")


def test_build_connection_string() -> None:
    connection_string = build_connection_string(
        {
            "type": "vast",
            "endpoint": "https://172.200.207.121:8443",
            "access_key": "ak/with@reserved",
            "secret_key": "sk/with@reserved",
            "database": "my_catalog",
            "end_user": "alice",
        }
    )

    assert connection_string == (
        "vast://ak%2Fwith%40reserved:sk%2Fwith%40reserved@172.200.207.121:8443"
        "/my_catalog?secure=1&end_user=alice"
    )


def test_build_connection_string_bare_host_defaults_to_http() -> None:
    connection_string = build_connection_string(
        {
            "type": "vast",
            "endpoint": "172.200.207.121",
            "access_key": "ak",
            "secret_key": "sk",
            "database": "cat",
        }
    )

    assert connection_string == "vast://ak:sk@172.200.207.121/cat?secure=0"


def test_dialect_is_trino() -> None:
    assert VastDatabase(_connection_string()).dialect == "trino"
    assert VastDatabase(_connection_string()).database_name == "my_catalog"


def test_schema_selection_filters_introspection() -> None:
    database = VastDatabase(_connection_string(), schemas=["analytics"])
    tables = pd.DataFrame(
        {
            "table_schema": ["default", "Analytics", "analytics"],
            "table_name": ["customers", "orders", "line_items"],
        }
    )

    filtered = database._filter_by_schema(tables)

    assert filtered["table_name"].tolist() == ["orders", "line_items"]


def test_registry_creates_vast_connector_with_schema_selection() -> None:
    database = create_connector(_connection_string(), schemas=["analytics"])

    assert isinstance(database, VastDatabase)
    assert database._schema_filter == {"analytics"}


class _FakeField:
    def __init__(self, name: str, type_: str, nullable: bool = True) -> None:
        self.name = name
        self.type = type_
        self.nullable = nullable


class _FakeApi:
    """Stands in for ``tx._rpc.api``; serves columns per (schema, table)."""

    def __init__(self, columns: dict[tuple[str, str], list[_FakeField]]) -> None:
        self._columns = columns

    def list_columns(
        self,
        *,
        bucket: str,
        schema: str,
        table: str,
        txid: int,
        next_key: int = 0,
        **_: object,
    ) -> tuple[list[_FakeField], int, bool, int]:
        fields = self._columns[(schema, table)]
        return list(fields), 0, False, len(fields)


class _FakeTx:
    txid = 1

    def __init__(self, api: _FakeApi) -> None:
        self._rpc = type("_Rpc", (), {"api": api})()


class _FakeSchema:
    def __init__(self, name: str, table_names: list[str]) -> None:
        self.name = name
        self._table_names = table_names

    def tablenames(self) -> list[str]:
        return list(self._table_names)


class _FakeBucket:
    def __init__(self, name: str, schemas: list[_FakeSchema], api: _FakeApi) -> None:
        self.name = name
        self._schemas = schemas
        self.tx = _FakeTx(api)

    def schemas(self) -> list[_FakeSchema]:
        return self._schemas


def _sample_bucket() -> _FakeBucket:
    schemas = [
        _FakeSchema("analytics", ["orders"]),
        _FakeSchema("default", ["customers"]),
    ]
    columns = {
        ("analytics", "orders"): [
            _FakeField("id", "int64", nullable=False),
            _FakeField("total", "double", nullable=True),
        ],
        ("default", "customers"): [_FakeField("name", "string")],
    }
    return _FakeBucket("gsf-db-bucket", schemas, _FakeApi(columns))


def _patch_bucket(monkeypatch: MonkeyPatch, database: VastDatabase) -> None:
    @contextmanager
    def _cm():
        yield _sample_bucket()

    monkeypatch.setattr(database, "_open_bucket", _cm)


def test_get_schemas_uses_sdk(monkeypatch: MonkeyPatch) -> None:
    database = VastDatabase(_connection_string())
    _patch_bucket(monkeypatch, database)

    assert database.get_schemas() == ["analytics", "default"]


def test_get_schemas_respects_filter(monkeypatch: MonkeyPatch) -> None:
    database = VastDatabase(_connection_string(), schemas=["Analytics"])
    _patch_bucket(monkeypatch, database)

    assert database.get_schemas() == ["analytics"]


def test_get_tables_maps_sdk_hierarchy(monkeypatch: MonkeyPatch) -> None:
    database = VastDatabase(_connection_string())
    _patch_bucket(monkeypatch, database)

    tables = database.get_tables()

    assert tables.columns.tolist() == ["table_schema", "table_name", "table_type"]
    assert set(zip(tables["table_schema"], tables["table_name"])) == {
        ("analytics", "orders"),
        ("default", "customers"),
    }
    assert set(tables["table_type"]) == {TableTypes.BASE_TABLE}


def test_get_tables_respects_filter(monkeypatch: MonkeyPatch) -> None:
    database = VastDatabase(_connection_string(), schemas=["analytics"])
    _patch_bucket(monkeypatch, database)

    tables = database.get_tables()

    assert tables["table_name"].tolist() == ["orders"]


def test_get_columns_maps_arrow_fields(monkeypatch: MonkeyPatch) -> None:
    database = VastDatabase(_connection_string())
    _patch_bucket(monkeypatch, database)

    columns = database.get_columns()

    assert columns.columns.tolist() == [
        "table_schema",
        "table_name",
        "column_name",
        "data_type",
        "is_nullable",
        "ordinal_position",
    ]
    orders = columns[columns["table_name"] == "orders"]
    assert orders["column_name"].tolist() == ["id", "total"]
    assert orders["data_type"].tolist() == ["int64", "double"]
    assert orders["is_nullable"].tolist() == ["NO", "YES"]
    assert orders["ordinal_position"].tolist() == [1, 2]


def test_get_views_is_empty_with_expected_columns() -> None:
    views = VastDatabase(_connection_string()).get_views()

    assert views.columns.tolist() == ["table_schema", "table_name", "view_definition"]
    assert views.empty


def test_qualify_query_prepends_bucket() -> None:
    # _connection_string() uses bucket "my_catalog".
    database = VastDatabase(_connection_string())

    out = database._qualify_query("SELECT * FROM sales.orders LIMIT 10")

    assert '"my_catalog/sales"' in out
    assert "orders" in out


def test_qualify_query_folds_bucket_catalog() -> None:
    # The LLM sometimes emits the bucket as a 3-part catalog:
    #   "my_catalog".sales.orders  ->  "my_catalog/sales".orders
    database = VastDatabase(_connection_string())

    out = database._qualify_query('SELECT * FROM "my_catalog".sales.orders')

    assert '"my_catalog/sales"' in out
    # The bucket must not survive as a separate catalog qualifier.
    assert '"my_catalog".sales' not in out
    assert out.count("my_catalog/") == 1


def test_qualify_query_leaves_foreign_catalog() -> None:
    database = VastDatabase(_connection_string())

    out = database._qualify_query("SELECT * FROM other.sales.orders")

    assert "my_catalog" not in out


def test_qualify_query_preserves_alias_and_columns() -> None:
    database = VastDatabase(_connection_string())

    out = database._qualify_query("SELECT c.x FROM sales.orders AS c WHERE c.x > 0")

    assert '"my_catalog/sales"' in out
    assert "c.x" in out


def test_qualify_query_leaves_system_and_bucketless_refs() -> None:
    database = VastDatabase(_connection_string())

    # No schema qualifier and system schemas are passed through unchanged.
    assert database._qualify_query("SELECT 1") == "SELECT 1"
    assert "my_catalog/information_schema" not in database._qualify_query(
        "SELECT * FROM information_schema.tables"
    )


def test_qualify_query_is_idempotent() -> None:
    database = VastDatabase(_connection_string())

    once = database._qualify_query("SELECT * FROM sales.orders")
    twice = database._qualify_query(once)

    assert twice.count("my_catalog/") == 1


def test_get_queries_is_empty_without_executing() -> None:
    database = VastDatabase(_connection_string())

    # Must not touch the driver: VAST has no query-history table to query.
    def _fail() -> None:
        raise AssertionError("get_queries must not open a connection")

    database._connect = _fail  # type: ignore[method-assign]
    queries = database.get_queries()

    assert queries.columns.tolist() == ["end_time", "query_text"]
    assert queries.empty


def test_pks_and_fks_are_empty_with_expected_columns() -> None:
    database = VastDatabase(_connection_string())

    assert database.get_pks().columns.tolist() == [
        "table_schema",
        "table_name",
        "column_name",
        "ordinal_position",
    ]
    assert database.get_fks().columns.tolist() == [
        "table_schema",
        "table_name",
        "column_name",
        "referenced_schema",
        "referenced_table",
        "referenced_column",
    ]


def test_execute_returns_dataframe_and_closes_connection(
    monkeypatch: MonkeyPatch,
) -> None:
    class Cursor:
        description = [("VALUE",)]
        executed: tuple[str, list[int] | None] | None = None

        def __enter__(self) -> "Cursor":
            return self

        def __exit__(self, *_args: object) -> None:
            return None

        def execute(self, statement: str, parameters: list[int] | None = None) -> None:
            self.executed = (statement, parameters)

        def fetchall(self) -> list[tuple[int]]:
            return [(1,)]

    class Connection:
        closed = False
        cursor_instance = Cursor()

        def cursor(self) -> Cursor:
            return self.cursor_instance

        def close(self) -> None:
            self.closed = True

    connection = Connection()
    database = VastDatabase(_connection_string())
    monkeypatch.setattr(
        database,
        "_connect",
        lambda: _as_context(connection),
    )

    result = database.execute("SELECT ?", [1])

    assert result.to_dict(orient="records") == [{"value": 1}]
    assert connection.cursor_instance.executed == ("SELECT ?", [1])
    assert connection.closed


def _as_context(connection: object):
    from contextlib import contextmanager

    @contextmanager
    def _cm():
        try:
            yield connection
        finally:
            connection.close()

    return _cm()
