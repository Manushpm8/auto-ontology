import pytest
from pytest import MonkeyPatch

from gsf.connectors.connection_string_factory import (
    build_connection_string,
    prepare_connection_for_storage,
)


def _snowflake_connection(password: str = "secret") -> dict[str, object]:
    return {
        "type": "snowflake",
        "account": "account",
        "warehouse": "warehouse",
        "user": "user",
        "password": password,
        "database": "database",
        "schemas": ["GPU_FLEET"],
    }


def test_storage_externalizes_matching_injected_password(
    monkeypatch: MonkeyPatch,
) -> None:
    monkeypatch.setenv("SNOWFLAKE_PASSWORD", "secret")

    stored = prepare_connection_for_storage(_snowflake_connection())

    assert "password" not in stored
    assert stored["password_env"] == "SNOWFLAKE_PASSWORD"
    assert stored["schemas"] == ["GPU_FLEET"]


def test_storage_rejects_password_that_differs_from_injected_secret(
    monkeypatch: MonkeyPatch,
) -> None:
    monkeypatch.setenv("SNOWFLAKE_PASSWORD", "injected")

    with pytest.raises(ValueError, match="does not match"):
        prepare_connection_for_storage(_snowflake_connection("submitted"))


def test_connection_string_hydrates_environment_backed_password(
    monkeypatch: MonkeyPatch,
) -> None:
    monkeypatch.setenv("SNOWFLAKE_PASSWORD", "secret/with@reserved")
    stored = _snowflake_connection()
    stored.pop("password")
    stored["password_env"] = "SNOWFLAKE_PASSWORD"

    connection_string = build_connection_string(stored)

    assert "secret%2Fwith%40reserved" in connection_string
    assert "password_env" not in connection_string


def test_connection_string_rejects_unapproved_password_environment(
    monkeypatch: MonkeyPatch,
) -> None:
    monkeypatch.setenv("AUTH_SECRET", "must-not-leak")
    stored = _snowflake_connection()
    stored.pop("password")
    stored["password_env"] = "AUTH_SECRET"

    with pytest.raises(ValueError, match="is not allowed"):
        build_connection_string(stored)


def test_storage_keeps_local_password_without_injected_secret(
    monkeypatch: MonkeyPatch,
) -> None:
    monkeypatch.delenv("SNOWFLAKE_PASSWORD", raising=False)

    stored = prepare_connection_for_storage(_snowflake_connection())

    assert stored["password"] == "secret"
    assert "password_env" not in stored
