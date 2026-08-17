import sqlite3
from pathlib import Path

from gsf.connectors.sqlite import SQLiteDatabase


def _database_with_stale_view(tmp_path: Path) -> Path:
    """A database holding a view whose base column no longer exists.

    SQLite validates a view only when it is created, so replacing the base table
    afterwards leaves a view that ``sqlite_master`` lists but ``PRAGMA
    table_info`` cannot resolve — the shape spider2's ``oracle_sql`` ships. The
    table is dropped and recreated rather than renamed because ``ALTER TABLE
    RENAME COLUMN`` rewrites dependent views, which is precisely not the case
    under test.
    """
    path = tmp_path / "stale_view.sqlite"
    conn = sqlite3.connect(path)
    conn.executescript(
        """
        CREATE TABLE hire_periods (emp_id INTEGER PRIMARY KEY, start_date TEXT);
        CREATE TABLE employees (id INTEGER PRIMARY KEY, name TEXT);
        CREATE VIEW hire_periods_named AS
            SELECT h.emp_id, h.start_date, e.name
            FROM hire_periods h JOIN employees e ON e.id = h.emp_id;
        CREATE VIEW employee_names AS SELECT name FROM employees;
        DROP TABLE hire_periods;
        CREATE TABLE hire_periods (emp_id INTEGER PRIMARY KEY, start_ TEXT);
        """
    )
    conn.commit()
    conn.close()
    return path


def test_unresolvable_view_is_skipped_rather_than_raising(tmp_path: Path) -> None:
    database = SQLiteDatabase(str(_database_with_stale_view(tmp_path)))

    tables = database.get_tables()
    columns = database.get_columns()
    views = database.get_views()

    assert "hire_periods_named" not in set(tables["table_name"])
    assert "hire_periods_named" not in set(columns["table_name"])
    assert "hire_periods_named" not in set(views["table_name"])


def test_sound_relations_survive_the_skip(tmp_path: Path) -> None:
    database = SQLiteDatabase(str(_database_with_stale_view(tmp_path)))

    tables = database.get_tables()
    columns = database.get_columns()

    assert set(tables["table_name"]) == {
        "hire_periods",
        "employees",
        "employee_names",
    }
    assert set(database.get_views()["table_name"]) == {"employee_names"}
    assert set(columns[columns["table_name"] == "hire_periods"]["column_name"]) == {
        "emp_id",
        "start_",
    }
    assert set(database.get_pks()["table_name"]) == {"hire_periods", "employees"}
