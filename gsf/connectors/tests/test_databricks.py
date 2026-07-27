import json
import os
from typing import Any
from urllib.parse import quote

import pandas as pd
import pytest
from pytest import MonkeyPatch

from gsf.connectors.connection_string_factory import build_connection_string
from gsf.connectors.databricks import DatabricksDatabase, _parse_connection_string
from gsf.connectors.registry import create_connector


def _connection_string(token: str = "secret") -> str:
    return (
        f"databricks://token:{quote(token, safe='')}@example.databricks.com/main"
        "?http_path=%2Fsql%2F1.0%2Fwarehouses%2Fwarehouse-id"
    )


def test_parse_connection_string() -> None:
    kwargs, catalog, schema_contains = _parse_connection_string(
        _connection_string("secret/with@chars")
    )

    assert kwargs == {
        "server_hostname": "example.databricks.com",
        "http_path": "/sql/1.0/warehouses/warehouse-id",
        "access_token": "secret/with@chars",
        "catalog": "main",
        "enable_telemetry": False,
    }
    assert catalog == "main"
    assert schema_contains is None


def test_build_connection_string() -> None:
    connection_string = build_connection_string(
        {
            "type": "databricks",
            "host": "https://example.databricks.com",
            "http_path": "/sql/1.0/warehouses/warehouse-id",
            "password": "token/with@reserved",
            "database": "main",
            "schemas": ["analytics"],
        }
    )

    assert connection_string == (
        "databricks://token:token%2Fwith%40reserved@example.databricks.com/main"
        "?http_path=%2Fsql%2F1.0%2Fwarehouses%2Fwarehouse-id"
    )


def test_build_connection_string_carries_schema_contains() -> None:
    connection_string = build_connection_string(
        {
            "type": "databricks",
            "host": "example.databricks.com",
            "http_path": "/sql/1.0/warehouses/warehouse-id",
            "password": "token",
            "database": "main",
            "schema_contains": "sales & co",
        }
    )

    assert connection_string.endswith("&schema_contains=sales%20%26%20co")
    _kwargs, _catalog, schema_contains = _parse_connection_string(connection_string)
    assert schema_contains == "sales & co"


def test_get_schemas_applies_contains_filter(monkeypatch: MonkeyPatch) -> None:
    executed: list[str] = []

    def fake_execute(self: DatabricksDatabase, sql_text: str, parameters: Any = None):
        executed.append(sql_text)
        return pd.DataFrame({"databaseName": ["sales_raw", "sales_curated"]})

    monkeypatch.setattr(DatabricksDatabase, "execute", fake_execute)

    plain = DatabricksDatabase(_connection_string())
    assert plain.get_schemas() == ["sales_raw", "sales_curated"]
    assert executed[-1] == "SHOW SCHEMAS IN `main`"

    filtered = DatabricksDatabase(_connection_string() + "&schema_contains=sal*es")
    filtered.get_schemas()
    # ``*`` is escaped so the user's substring matches literally.
    assert executed[-1] == "SHOW SCHEMAS IN `main` LIKE '*sal\\*es*'"


def test_create_connection_drops_schema_contains(monkeypatch: MonkeyPatch) -> None:
    from gsf.server.connections import service

    captured: dict[str, Any] = {}
    monkeypatch.setattr(
        service, "insert_connection", lambda **kwargs: captured.update(kwargs)
    )
    monkeypatch.setattr(service, "_database_already_connected", lambda _name: False)
    monkeypatch.setattr(service, "is_vault_configured", lambda: False)
    monkeypatch.setattr(service, "invalidate_connectors_cache", lambda: None)
    monkeypatch.setattr(service, "refresh_chat_workers", lambda: None)
    monkeypatch.setattr(service, "trigger_ingest", lambda _connection: None)

    stored = service.create_connection(
        connection={
            "type": "databricks",
            "database": "main",
            "schema_contains": "sales",
        }
    )

    assert "schema_contains" not in stored
    assert "schema_contains" not in json.loads(captured["connection"])


def test_schema_selection_filters_introspection() -> None:
    database = DatabricksDatabase(_connection_string(), schemas=["analytics"])
    tables = pd.DataFrame(
        {
            "table_schema": ["default", "Analytics", "analytics"],
            "table_name": ["customers", "orders", "line_items"],
        }
    )

    filtered = database._filter_by_schema(tables)

    assert filtered["table_name"].tolist() == ["orders", "line_items"]


def test_registry_creates_databricks_connector_with_schema_selection() -> None:
    database = create_connector(_connection_string(), schemas=["analytics"])

    assert isinstance(database, DatabricksDatabase)
    assert database._schema_filter == {"analytics"}


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
    monkeypatch.setattr(
        "gsf.connectors.databricks.sql.connect",
        lambda **_kwargs: connection,
    )
    database = DatabricksDatabase(_connection_string())

    result = database.execute("SELECT ?", [1])

    assert result.to_dict(orient="records") == [{"value": 1}]
    assert connection.cursor_instance.executed == ("SELECT ?", [1])
    assert connection.closed


def _live_connection() -> dict[str, Any]:
    environment_fields = {
        "host": "DATABRICKS_SERVER_HOSTNAME",
        "http_path": "DATABRICKS_HTTP_PATH",
        "password": "DATABRICKS_TOKEN",
        "database": "DATABRICKS_CATALOG",
    }
    missing = [
        environment_name
        for environment_name in environment_fields.values()
        if not os.environ.get(environment_name)
    ]
    if missing:
        pytest.skip(
            "Live Databricks credentials are not configured: " + ", ".join(missing)
        )

    connection: dict[str, Any] = {
        "type": "databricks",
        **{
            field: os.environ[environment_name]
            for field, environment_name in environment_fields.items()
        },
    }
    schema = os.environ.get("DATABRICKS_SCHEMA")
    if schema:
        connection["schemas"] = [schema]
    return connection


def test_live_databricks_connector_end_to_end() -> None:
    connection = _live_connection()
    connection_string = build_connection_string(connection)
    database = DatabricksDatabase(
        connection_string,
        schemas=connection.get("schemas"),
    )

    try:
        database.ping()
        assert database.execute("SELECT 1 AS value").iloc[0]["value"] == 1
        assert isinstance(database.get_schemas(), list)

        metadata_frames = [
            database.get_tables(),
            database.get_columns(),
            database.get_views(),
            database.get_pks(),
            database.get_fks(),
            database.get_queries(),
        ]
        assert all(isinstance(frame, pd.DataFrame) for frame in metadata_frames)
    finally:
        database.close()
