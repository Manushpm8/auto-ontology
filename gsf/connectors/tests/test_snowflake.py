import json
from pathlib import Path
import base64

import pandas as pd
import pytest
from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric import rsa
from pytest import MonkeyPatch

from gsf.connectors.snowflake import (
    SnowflakeDatabase,
    load_metadata_allowlist,
    resolve_metadata_path,
    _parse_connection_string,
)


def _connection_string(query: str = "") -> str:
    suffix = f"&{query}" if query else ""
    return f"snowflake://user:password@account?warehouse=warehouse&database=db{suffix}"


@pytest.fixture(scope="module")
def rsa_key() -> rsa.RSAPrivateKey:
    """One key for the whole module; generation is the slow part of these tests."""
    return rsa.generate_private_key(public_exponent=65537, key_size=2048)


def _pem(key: rsa.RSAPrivateKey, passphrase: bytes | None = None) -> str:
    encryption = (
        serialization.BestAvailableEncryption(passphrase)
        if passphrase
        else serialization.NoEncryption()
    )
    return key.private_bytes(
        encoding=serialization.Encoding.PEM,
        format=serialization.PrivateFormat.PKCS8,
        encryption_algorithm=encryption,
    ).decode()


def _keypair_connection_string(pem: str, query: str = "") -> str:
    encoded = base64.urlsafe_b64encode(pem.encode()).decode()
    suffix = f"&{query}" if query else ""
    return (
        f"snowflake://user@account?warehouse=warehouse&database=db"
        f"&private_key={encoded}{suffix}"
    )


def _tables() -> pd.DataFrame:
    return pd.DataFrame(
        {
            "table_schema": ["GPU_FLEET", "GPU_FLEET", "CRM"],
            "table_name": ["GPUS", "JOBS", "CUSTOMERS"],
        }
    )


def _write_metadata(path: Path) -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        json.dumps(
            {
                "PRODUCT": {
                    "description": None,
                    "columns": [
                        {
                            "name": "productid",
                            "description": None,
                            "value_examples": ["1"],
                        },
                        {
                            "name": "name",
                            "description": "product name",
                            "value_examples": None,
                        },
                    ],
                },
                "CURRENCYRATE": {
                    "description": None,
                    "columns": [
                        {
                            "name": "currencyrateid",
                            "description": None,
                            "value_examples": [],
                        },
                    ],
                },
            }
        ),
        encoding="utf-8",
    )
    return path


def test_url_schema_filters_introspection() -> None:
    database = SnowflakeDatabase(_connection_string("schema=gpu_fleet"))

    filtered = database._filter_by_schema(_tables())

    assert filtered["table_name"].tolist() == ["GPUS", "JOBS"]


def test_explicit_schema_selection_overrides_url_schema() -> None:
    database = SnowflakeDatabase(
        _connection_string("schema=gpu_fleet"),
        schemas=["crm"],
    )

    filtered = database._filter_by_schema(_tables())

    assert filtered["table_name"].tolist() == ["CUSTOMERS"]


def test_missing_schema_keeps_all_visible_schemas() -> None:
    database = SnowflakeDatabase(_connection_string())

    filtered = database._filter_by_schema(_tables())

    assert filtered.equals(_tables())


def test_database_name_defaults_to_physical_database() -> None:
    database = SnowflakeDatabase(_connection_string())

    assert database.database_name == "db"
    assert database._physical_database == "db"


def test_distinct_databases_yield_distinct_names() -> None:
    first = SnowflakeDatabase(
        "snowflake://user:password@account?warehouse=warehouse&database=PATENTS"
    )
    second = SnowflakeDatabase(
        "snowflake://user:password@account?warehouse=warehouse&database=GITHUB_REPOS"
    )

    assert first.database_name == "PATENTS"
    assert second.database_name == "GITHUB_REPOS"
    assert first.database_name != second.database_name


def test_metadata_database_overrides_logical_name() -> None:
    database = SnowflakeDatabase(
        _connection_string("metadata_database=spider2%2Fpatents")
    )

    assert database.database_name == "spider2/patents"
    assert database._physical_database == "db"
    assert database._connect_kwargs["database"] == "db"


def test_resolve_metadata_path_prefers_logical_database_name(tmp_path: Path) -> None:
    metadata = _write_metadata(
        tmp_path / "spider2" / "adventureworks" / "metadata.json"
    )

    resolved = resolve_metadata_path(
        database_name="spider2/adventureworks",
        physical_database="ADVENTUREWORKS",
        datasets_root=tmp_path,
    )

    assert resolved == metadata


def test_resolve_metadata_path_falls_back_to_spider2_slug(tmp_path: Path) -> None:
    metadata = _write_metadata(
        tmp_path / "spider2" / "adventureworks" / "metadata.json"
    )

    resolved = resolve_metadata_path(
        database_name="ADVENTUREWORKS",
        physical_database="ADVENTUREWORKS",
        datasets_root=tmp_path,
    )

    assert resolved == metadata


def test_load_metadata_allowlist_is_case_normalized(tmp_path: Path) -> None:
    path = _write_metadata(tmp_path / "metadata.json")

    tables, columns = load_metadata_allowlist(path)

    assert tables == {"PRODUCT", "CURRENCYRATE"}
    assert columns["PRODUCT"] == {"PRODUCTID", "NAME"}
    assert columns["CURRENCYRATE"] == {"CURRENCYRATEID"}


def test_metadata_filters_tables_and_columns(
    tmp_path: Path, monkeypatch: MonkeyPatch
) -> None:
    metadata = _write_metadata(tmp_path / "spider2" / "db" / "metadata.json")
    monkeypatch.setenv("DATASETS_DIR", str(tmp_path))
    database = SnowflakeDatabase(_connection_string())

    assert database._metadata_path == metadata
    assert database._metadata_tables == {"PRODUCT", "CURRENCYRATE"}

    tables = pd.DataFrame(
        {
            "table_schema": ["PUBLIC", "PUBLIC", "PUBLIC"],
            "table_name": ["PRODUCT", "CURRENCYRATE", "EXTRA"],
            "table_type": ["BASE TABLE", "BASE TABLE", "BASE TABLE"],
        }
    )
    columns = pd.DataFrame(
        {
            "table_schema": ["PUBLIC", "PUBLIC", "PUBLIC", "PUBLIC"],
            "table_name": ["PRODUCT", "PRODUCT", "PRODUCT", "CURRENCYRATE"],
            "column_name": ["PRODUCTID", "NAME", "IGNORED", "CURRENCYRATEID"],
        }
    )

    assert database._filter_by_metadata(tables)["table_name"].tolist() == [
        "PRODUCT",
        "CURRENCYRATE",
    ]
    assert database._filter_by_metadata(columns, filter_columns=True)[
        "column_name"
    ].tolist() == ["PRODUCTID", "NAME", "CURRENCYRATEID"]


def test_metadata_file_query_param(tmp_path: Path) -> None:
    metadata = _write_metadata(tmp_path / "custom.json")
    database = SnowflakeDatabase(
        _connection_string(f"metadata_file={metadata.as_posix()}")
    )

    assert database._metadata_path == metadata
    assert database._metadata_tables == {"PRODUCT", "CURRENCYRATE"}


def test_get_tables_pushes_metadata_filter_into_sql(
    tmp_path: Path, monkeypatch: MonkeyPatch
) -> None:
    _write_metadata(tmp_path / "spider2" / "db" / "metadata.json")
    monkeypatch.setenv("DATASETS_DIR", str(tmp_path))
    database = SnowflakeDatabase(_connection_string())
    captured: list[str] = []

    def execute(sql: str) -> pd.DataFrame:
        captured.append(sql)
        return pd.DataFrame(
            {
                "table_schema": ["PUBLIC"],
                "table_name": ["PRODUCT"],
                "table_type": ["BASE TABLE"],
            }
        )

    monkeypatch.setattr(database, "execute", execute)

    frame = database.get_tables()

    assert frame["table_name"].tolist() == ["PRODUCT"]
    assert "UPPER(TABLE_NAME) IN ('CURRENCYRATE', 'PRODUCT')" in captured[0]


def test_password_auth_passes_password_and_no_key() -> None:
    kwargs, warehouse, database = _parse_connection_string(_connection_string())

    assert kwargs["password"] == "password"
    assert "private_key" not in kwargs
    assert (warehouse, database) == ("warehouse", "db")


def test_private_key_replaces_password(rsa_key: rsa.RSAPrivateKey) -> None:
    kwargs, _, _ = _parse_connection_string(_keypair_connection_string(_pem(rsa_key)))

    # The driver takes DER bytes, and no password may be sent alongside a key.
    assert isinstance(kwargs["private_key"], bytes)
    assert "password" not in kwargs
    assert kwargs["user"] == "user"


def test_private_key_der_matches_the_supplied_key(rsa_key: rsa.RSAPrivateKey) -> None:
    kwargs, _, _ = _parse_connection_string(_keypair_connection_string(_pem(rsa_key)))

    expected = rsa_key.private_bytes(
        encoding=serialization.Encoding.DER,
        format=serialization.PrivateFormat.PKCS8,
        encryption_algorithm=serialization.NoEncryption(),
    )
    assert kwargs["private_key"] == expected


def test_bare_pem_is_accepted_without_base64(rsa_key: rsa.RSAPrivateKey) -> None:
    """Hand-written connection strings should not have to pre-encode the key."""
    from urllib.parse import quote

    conn = (
        f"snowflake://user@account?warehouse=warehouse&database=db"
        f"&private_key={quote(_pem(rsa_key), safe='')}"
    )

    kwargs, _, _ = _parse_connection_string(conn)

    assert isinstance(kwargs["private_key"], bytes)


def test_pem_with_literal_backslash_n_is_repaired(rsa_key: rsa.RSAPrivateKey) -> None:
    """A PEM round-tripped through JSON often loses its real newlines."""
    mangled = _pem(rsa_key).replace("\n", "\\n")

    kwargs, _, _ = _parse_connection_string(_keypair_connection_string(mangled))

    assert isinstance(kwargs["private_key"], bytes)


def test_encrypted_key_accepts_passphrase(rsa_key: rsa.RSAPrivateKey) -> None:
    conn = _keypair_connection_string(
        _pem(rsa_key, passphrase=b"s3cret"), query="private_key_passphrase=s3cret"
    )

    kwargs, _, _ = _parse_connection_string(conn)

    assert isinstance(kwargs["private_key"], bytes)


def test_encrypted_key_without_passphrase_says_so(rsa_key: rsa.RSAPrivateKey) -> None:
    conn = _keypair_connection_string(_pem(rsa_key, passphrase=b"s3cret"))

    with pytest.raises(ValueError, match="private_key_passphrase"):
        _parse_connection_string(conn)


def test_missing_both_credentials_is_rejected() -> None:
    with pytest.raises(ValueError, match="password or a private_key"):
        _parse_connection_string("snowflake://user@account?warehouse=wh&database=db")


def test_unparseable_private_key_is_rejected() -> None:
    conn = _keypair_connection_string("not a key at all")

    with pytest.raises(ValueError, match="could not be parsed"):
        _parse_connection_string(conn)


def test_query_history_excludes_blank_query_text(monkeypatch: MonkeyPatch) -> None:
    database = SnowflakeDatabase(_connection_string())
    captured_sql = ""

    def execute(sql: str) -> pd.DataFrame:
        nonlocal captured_sql
        captured_sql = sql
        return pd.DataFrame(columns=["end_time", "query_text"])

    monkeypatch.setattr(database, "execute", execute)

    database.get_queries()

    assert "NULLIF(TRIM(QUERY_TEXT), '') IS NOT NULL" in captured_sql


def test_spider2_eval_skips_query_history(monkeypatch: MonkeyPatch) -> None:
    database = SnowflakeDatabase(_connection_string("spider2_eval=1"))

    def unexpected_execute(sql: str) -> pd.DataFrame:
        raise AssertionError(f"query history should be skipped, got: {sql}")

    monkeypatch.setattr(database, "execute", unexpected_execute)

    result = database.get_queries()

    assert database._spider2_eval is True
    assert result.empty
    assert result.columns.tolist() == ["end_time", "query_text"]


def test_non_spider2_database_does_not_skip_query_history() -> None:
    database = SnowflakeDatabase(_connection_string())

    assert database._spider2_eval is False


def test_execute_reuses_one_snowflake_connection(monkeypatch: MonkeyPatch) -> None:
    statements: list[str] = []

    class FakeCursor:
        description = [("VALUE",)]

        def __enter__(self) -> "FakeCursor":
            return self

        def __exit__(self, *args: object) -> None:
            return None

        def execute(self, sql: str, parameters: object = None) -> None:
            statements.append(sql)

        def fetchall(self) -> list[tuple[int]]:
            return [(1,)]

    class FakeConnection:
        def __init__(self) -> None:
            self.closed = False

        def cursor(self) -> FakeCursor:
            return FakeCursor()

        def close(self) -> None:
            self.closed = True

    connections: list[FakeConnection] = []

    def connect(**kwargs: object) -> FakeConnection:
        connection = FakeConnection()
        connections.append(connection)
        return connection

    monkeypatch.setattr("gsf.connectors.snowflake.snowflake.connector.connect", connect)
    database = SnowflakeDatabase(_connection_string())

    first = database.execute("SELECT 1")
    second = database.execute("SELECT 2")
    database.close()

    assert first.iloc[0, 0] == 1
    assert second.iloc[0, 0] == 1
    assert len(connections) == 1
    assert statements == [
        'USE WAREHOUSE "warehouse"',
        'USE DATABASE "db"',
        "SELECT 1",
        "SELECT 2",
    ]
    assert connections[0].closed is True
