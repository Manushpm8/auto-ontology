# SPDX-FileCopyrightText: Copyright (c) 2024-2026, NVIDIA CORPORATION & AFFILIATES.
# All rights reserved.
# SPDX-License-Identifier: Apache-2.0

"""HashiCorp Vault access for UI-managed database connection secrets.

Connection credentials are stored in Vault keyed by catalog database name. This
module is the single place that reads/writes those secrets; callers should treat
a missing or unreachable Vault as "no secret" rather than an error.

This module performs no import-time side effects (it does not load ``.env``);
entrypoints are responsible for loading the environment before use.
"""

from __future__ import annotations

import json
import logging
import os
import hvac

logger = logging.getLogger(__name__)

REQUIRED_VAULT_ENV_VARS = (
    "VAULT_ADDR",
    "VAULT_NAMESPACE",
    "VAULT_ROLE_ID",
    "VAULT_SECRET_ID",
)


def is_vault_configured() -> bool:
    """Return True if the user provided all required Vault env vars.

    Warn on partial configuration (some but not all set), which is almost
    always a misconfiguration that would otherwise silently disable Vault.
    """
    missing = [var for var in REQUIRED_VAULT_ENV_VARS if not os.environ.get(var)]
    if missing and len(missing) < len(REQUIRED_VAULT_ENV_VARS):
        logger.warning(
            "Vault is partially configured; ignoring Vault and treating "
            "connections as unencrypted. Missing env vars: %s",
            ", ".join(missing),
        )
    return not missing


def get_client() -> hvac.Client:
    client = hvac.Client(
        url=os.environ["VAULT_ADDR"],
        namespace=os.environ["VAULT_NAMESPACE"],
    )
    client.auth.approle.login(
        role_id=os.environ["VAULT_ROLE_ID"],
        secret_id=os.environ["VAULT_SECRET_ID"],
        mount_point=os.environ.get("VAULT_AUTH_MOUNT", "approle/nvdcs/dc1"),
    )
    return client


def write_secret(database_name: str, secret: dict[str, str]) -> None:
    client = get_client()
    client.secrets.kv.v1.create_or_update_secret(
        path=database_name,
        secret={"connection": json.dumps(secret)},
        mount_point=os.environ.get("VAULT_KV_MOUNT", "gsf"),
    )


def read_secret(database_name: str) -> dict[str, str] | str:
    if not is_vault_configured():
        return ""
    try:
        client = get_client()
        response = client.secrets.kv.v1.read_secret(
            path=database_name,
            mount_point=os.environ.get("VAULT_KV_MOUNT", "gsf"),
        )
        return json.loads(response["data"]["connection"])
    except Exception:
        return ""


def delete_secrets(database_name: str | None = None) -> None:
    """Delete a single secret if database_name is given, otherwise delete all."""
    if not is_vault_configured():
        return

    client = get_client()
    mount_point = os.environ.get("VAULT_KV_MOUNT", "gsf")

    def delete(path: str) -> None:
        client.secrets.kv.v1.delete_secret(path=path, mount_point=mount_point)

    if database_name:
        delete(database_name)
        return

    try:
        # Use GET with ?list=true instead of the LIST HTTP verb, which the
        # Vault endpoint/proxy rejects ("Unsupported HTTP method").
        response = client.adapter.get(
            f"v1/{mount_point}",
            params={"list": "true"},
        )
        keys = response["data"]["keys"]
    except hvac.exceptions.InvalidPath:
        # Nothing stored under this mount.
        print(f"No secrets found under mount '{mount_point}'.")
        return

    for key in keys:
        delete(key)
