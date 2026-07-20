#!/usr/bin/env python3
"""Smoke-test the VAST ADBC driver and the GSF VastDatabase connector.

Run inside the backend pod (which bundles adbc-driver-vastdb + the native .so):

    VAST_ENDPOINT=http://172.200.207.121 \
    VAST_ACCESS_KEY=... VAST_SECRET_KEY=... \
    [VAST_CATALOG=my_catalog] \
    python dev_tools/vast_smoke_test.py

Steps 1-3 use the raw ADBC driver to prove connectivity and DISCOVER catalog /
schema names. If VAST_CATALOG is set (or discovered), step 4 exercises the GSF
VastDatabase connector end-to-end.
"""

from __future__ import annotations

import os
import sys


def _require(name: str) -> str:
    value = os.environ.get(name, "").strip()
    if not value:
        sys.exit(f"Missing required env var: {name}")
    return value


def main() -> None:
    endpoint = _require("VAST_ENDPOINT")
    access_key = _require("VAST_ACCESS_KEY")
    secret_key = _require("VAST_SECRET_KEY")
    catalog = os.environ.get("VAST_CATALOG", "").strip()

    import adbc_driver_manager.dbapi
    import adbc_driver_vastdb

    print(f"driver path: {adbc_driver_vastdb.get_driver_path()}")
    print(f"endpoint:    {endpoint}")

    conn = adbc_driver_manager.dbapi.connect(
        driver=adbc_driver_vastdb.get_driver_path(),
        db_kwargs={
            "vast.db.endpoint": endpoint,
            "vast.db.access_key": access_key,
            "vast.db.secret_key": secret_key,
        },
        autocommit=True,
    )

    def run(sql: str) -> list:
        with conn.cursor() as cur:
            cur.execute(sql)
            return cur.fetchall() if cur.description else []

    print("\n[1] SELECT 1 ->", run("SELECT 1"))

    print("\n[2] SHOW CATALOGS ->")
    catalogs = [row[0] for row in run("SHOW CATALOGS")]
    for name in catalogs:
        print("   ", name)

    print("\n[3] schemas per catalog ->")
    for name in catalogs:
        if name in {"system", "jmx"}:
            continue
        try:
            schemas = [row[0] for row in run(f'SHOW SCHEMAS FROM "{name}"')]
            print(f"    {name}: {schemas}")
            if not catalog:
                catalog = name  # first real catalog, for step 4
        except Exception as exc:  # noqa: BLE001
            print(f"    {name}: <error: {exc}>")

    conn.close()

    if not catalog:
        print("\n[4] skipped: no catalog to test VastDatabase against")
        return

    print(f"\n[4] VastDatabase against catalog '{catalog}' ->")
    from gsf.connectors.vast import VastDatabase

    scheme = "1" if endpoint.startswith("https") else "0"
    host = endpoint.split("://", 1)[-1]
    from urllib.parse import quote

    cs = (
        f"vast://{quote(access_key, safe='')}:{quote(secret_key, safe='')}"
        f"@{host}/{quote(catalog, safe='')}?secure={scheme}"
    )
    db = VastDatabase(cs)
    db.ping()
    print("    ping OK; dialect:", db.dialect, "database_name:", db.database_name)
    print("    get_schemas ->", db.get_schemas())
    tables = db.get_tables()
    print("    get_tables rows:", len(tables))
    print(tables.head(10).to_string(index=False))
    db.close()
    print("\nAll steps passed.")


if __name__ == "__main__":
    main()
