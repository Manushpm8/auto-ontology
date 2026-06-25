# SPDX-FileCopyrightText: Copyright (c) 2024-2026, NVIDIA CORPORATION & AFFILIATES.
# All rights reserved.
# SPDX-License-Identifier: Apache-2.0

"""Data Access Layer — Neo4j queries for Zone nodes."""

from __future__ import annotations

import logging
from typing import Any

from nemo_retriever.tabular_data.neo4j import get_neo4j_conn

from gsf.server.zones.utils import (
    LABEL_ZONE,
    REL_ZONE_OF,
    ZONE_DATA_LABELS,
    format_data_item,
    format_zone,
)

logger = logging.getLogger(__name__)

_DATA_ITEM_PATTERN = "|".join(ZONE_DATA_LABELS)


def list_zones() -> list[dict[str, Any]]:
    """Return all zones ordered by name."""
    rows = get_neo4j_conn().query_read(
        f"""
        MATCH (z:{LABEL_ZONE})
        RETURN z.id AS id,
               z.name AS name,
               z.description AS description,
               z.color AS color
        ORDER BY z.name
        """
    )
    return [format_zone(dict(r)) for r in rows]


def get_zone_by_id(zone_id: str) -> dict[str, Any] | None:
    """Return one zone with its linked catalog data items."""
    rows = get_neo4j_conn().query_read(
        f"""
        MATCH (z:{LABEL_ZONE} {{id: $zone_id}})
        OPTIONAL MATCH (z)-[:{REL_ZONE_OF}]->(item:{_DATA_ITEM_PATTERN})
        WHERE coalesce(item.deleted, false) = false
        WITH z,
             [i IN collect(DISTINCT item)
              WHERE i IS NOT NULL
              | {{
                  id: i.id,
                  name: i.name,
                  gsf_label: head(labels(i))
                }}] AS raw_items
        RETURN z.id AS id,
               z.name AS name,
               z.description AS description,
               z.color AS color,
               raw_items AS items
        """,
        {"zone_id": zone_id},
    )
    if not rows:
        return None

    row = dict(rows[0])
    items = [
        format_data_item(
            item_id=item["id"],
            name=item["name"],
            gsf_label=item["gsf_label"],
        )
        for item in row.pop("items", [])
    ]
    return format_zone(row, items=items)


def zone_name_exists(name: str, *, exclude_id: str | None = None) -> bool:
    """Return whether a zone with the same name already exists (case-insensitive).

    Pass ``exclude_id`` to ignore a specific zone (useful during updates).
    """
    exclude_clause = "AND z.id <> $exclude_id" if exclude_id is not None else ""
    params: dict[str, str] = {"name": name}
    if exclude_id is not None:
        params["exclude_id"] = exclude_id
    rows = get_neo4j_conn().query_read(
        f"""
        MATCH (z:{LABEL_ZONE})
        WHERE toLower(trim(z.name)) = toLower(trim($name))
          {exclude_clause}
        RETURN count(z) > 0 AS exists
        """,
        params,
    )
    return bool(rows and rows[0].get("exists"))


def create_zone(
    *,
    name: str,
    description: str | None,
    color: str | None,
    item_ids: list[str],
) -> dict[str, Any]:
    """Create a zone and link it to catalog data nodes (db/schema/table)."""
    conn = get_neo4j_conn()
    zone_rows = conn.query_write(
        f"""
        CREATE (z:{LABEL_ZONE})
        SET z.id = randomUUID(),
            z.name = $name,
            z.description = $description,
            z.color = $color
        RETURN z.id AS id,
               z.name AS name,
               z.description AS description,
               z.color AS color
        """,
        {
            "name": name,
            "description": description,
            "color": color,
        },
    )
    zone = format_zone(dict(zone_rows[0]), items=[])

    if item_ids:
        linked_rows = conn.query_write(
            f"""
            UNWIND $item_ids AS item_id
            MATCH (z:{LABEL_ZONE} {{id: $zone_id}})
            MATCH (item:{_DATA_ITEM_PATTERN} {{id: item_id}})
            WHERE coalesce(item.deleted, false) = false
            MERGE (z)-[:{REL_ZONE_OF}]->(item)
            RETURN DISTINCT item.id AS id
            """,
            {"zone_id": zone["id"], "item_ids": item_ids},
        )
        linked_ids = {row["id"] for row in linked_rows}
        ordered_linked_ids: list[str] = []
        seen: set[str] = set()
        for item_id in item_ids:
            if item_id not in linked_ids or item_id in seen:
                continue
            seen.add(item_id)
            ordered_linked_ids.append(item_id)
        zone["items"] = ordered_linked_ids

    return zone


def update_zone(
    *,
    zone_id: str,
    updates: dict[str, Any],
    item_ids: list[str] | None,
) -> dict[str, Any] | None:
    """Update zone properties and optional full items list, then return zone detail."""
    conn = get_neo4j_conn()
    existing_rows = conn.query_read(
        f"""
        MATCH (z:{LABEL_ZONE} {{id: $zone_id}})
        RETURN z.id AS id
        LIMIT 1
        """,
        {"zone_id": zone_id},
    )
    if not existing_rows:
        return None

    if updates:
        set_clauses = ", ".join(f"z.{field} = ${field}" for field in updates)
        conn.query_write(
            f"""
            MATCH (z:{LABEL_ZONE} {{id: $zone_id}})
            SET {set_clauses}
            """,
            {"zone_id": zone_id, **updates},
        )

    if item_ids is not None:
        conn.query_write(
            f"""
            MATCH (z:{LABEL_ZONE} {{id: $zone_id}})-[r:{REL_ZONE_OF}]->()
            DELETE r
            """,
            {"zone_id": zone_id},
        )

        if item_ids:
            conn.query_write(
                f"""
                UNWIND $item_ids AS item_id
                MATCH (z:{LABEL_ZONE} {{id: $zone_id}})
                MATCH (item:{_DATA_ITEM_PATTERN} {{id: item_id}})
                WHERE coalesce(item.deleted, false) = false
                MERGE (z)-[:{REL_ZONE_OF}]->(item)
                """,
                {"zone_id": zone_id, "item_ids": item_ids},
            )

    return get_zone_by_id(zone_id)


def delete_zone(zone_id: str) -> bool:
    """Delete a zone by id. Return whether a node was deleted."""
    conn = get_neo4j_conn()
    existing_rows = conn.query_read(
        f"""
        MATCH (z:{LABEL_ZONE} {{id: $zone_id}})
        RETURN z.id AS id
        LIMIT 1
        """,
        {"zone_id": zone_id},
    )
    if not existing_rows:
        return False

    conn.query_write(
        f"""
        MATCH (z:{LABEL_ZONE} {{id: $zone_id}})
        DETACH DELETE z
        """,
        {"zone_id": zone_id},
    )
    return True
