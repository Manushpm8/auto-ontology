"""Zone authorization helpers for the text-to-SQL retrieval pipeline."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from gsf.dal.custom_analyses import list_custom_analyses
from gsf.dal.sql_attributes import fetch_sql_attributes
from gsf.dal.terms import fetch_column_attributes
from gsf.dal.users import resolve_accessible_catalog_ids


@dataclass(frozen=True)
class ZoneAccessScope:
    """Authorization boundary resolved once for a text-to-SQL request."""

    zone_ids: list[str] | None
    data_ids_by_zone: dict[str, set[str]] | None
    column_attribute_ids: set[str] | None
    custom_analysis_ids: set[str] | None
    sql_attribute_ids: set[str] | None

    @property
    def table_ids(self) -> set[str] | None:
        """Allowed Table IDs, or ``None`` for an unrestricted internal scope."""
        if self.data_ids_by_zone is None:
            return None
        return self.data_ids_by_zone["table_ids"]


def resolve_zone_access_scope(zone_ids: list[str] | None) -> ZoneAccessScope:
    """Resolve the zone authorization boundary once for an agent request."""
    data_ids_by_zone = resolve_accessible_catalog_ids(zone_ids)
    if zone_ids is None:
        return ZoneAccessScope(zone_ids, None, None, None, None)

    return ZoneAccessScope(
        zone_ids=zone_ids,
        data_ids_by_zone=data_ids_by_zone,
        column_attribute_ids={
            str(row["id"])
            for row in fetch_column_attributes(
                zone_ids, data_ids_by_zone=data_ids_by_zone
            )
            if row.get("id")
        },
        custom_analysis_ids={
            str(row["id"])
            for row in list_custom_analyses(zone_ids, data_ids_by_zone=data_ids_by_zone)
            if row.get("id")
        },
        sql_attribute_ids={
            str(row["id"])
            for row in fetch_sql_attributes(zone_ids, data_ids_by_zone=data_ids_by_zone)
            if row.get("id")
        },
    )


def filter_hits_by_allowed_ids(
    hits: list[dict[str, Any]],
    allowed_ids: set[str] | None,
) -> list[dict[str, Any]]:
    """Keep only vector hits whose IDs are authorized for the current user."""
    if allowed_ids is None:
        return hits
    return [hit for hit in hits if str(hit.get("id") or "") in allowed_ids]
