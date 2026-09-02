# SPDX-FileCopyrightText: Copyright (c) 2026, NVIDIA CORPORATION & AFFILIATES.
# All rights reserved.
# SPDX-License-Identifier: Apache-2.0

"""Tag CRUD.

One thing here is less obvious than it looks: **a tag name is unique
case-insensitively on the trimmed name**, and unlike ``zone.name`` that rule is
a real database constraint — ``uq_tag_name_lower`` in ``gsf/dal/schema.py``.

The duplicate check in :func:`create_tag` therefore exists for its error
message rather than for correctness. The index is what holds when two requests
create the same name at once, and the check is what turns the ordinary case
into a readable 409 instead of a driver error. Both raise the same
``ValueError``, so a caller has one behaviour to handle rather than two.
"""

from __future__ import annotations

from typing import Any

from sqlalchemy import func, select
from sqlalchemy.exc import IntegrityError

from gsf.dal import schema as s
from gsf.dal.session import store

#: The rule ``uq_tag_name_lower`` indexes, as a comparison the DAL can run.
_FOLDED_NAME = func.lower(func.trim(s.tag.c.name))

#: Every column the API returns for a tag, in one place so the list and the
#: create path cannot drift into returning different shapes.
_COLUMNS = (s.tag.c.id, s.tag.c.name, s.tag.c.created, s.tag.c.modified)


def _name_taken(name: str) -> bool:
    return bool(
        store().query_read(
            select(s.tag.c.id).where(_FOLDED_NAME == name.strip().lower())
        )
    )


def list_tags() -> list[dict[str, Any]]:
    """Every tag, ordered as the settings page renders them.

    Sorted case-insensitively with the id as a tie-break, so the order is total
    and a page does not reshuffle between two reads.
    """
    return store().query_read(
        select(*_COLUMNS).order_by(func.lower(s.tag.c.name), s.tag.c.id)
    )


def create_tag(*, name: str) -> dict[str, Any]:
    """Create a tag and return it.

    *name* is stored as given; the caller is expected to have trimmed it. Raises
    ``ValueError`` when the name is already taken, whether that is caught by the
    check or by the unique index underneath it.
    """
    if _name_taken(name):
        raise ValueError(f"Tag with name {name!r} already exists")

    try:
        rows = store().query_write(
            s.tag.insert().values(name=name).returning(*_COLUMNS)
        )
    except IntegrityError as exc:
        # Only the name rule is translated. A different constraint failing means
        # something this function does not model, and hiding it behind "already
        # exists" would send the caller after the wrong bug.
        if "uq_tag_name_lower" not in str(exc.orig):
            raise
        raise ValueError(f"Tag with name {name!r} already exists") from exc

    return rows[0]


def delete_tag(tag_id: str) -> bool:
    """Delete a tag. ``False`` when no tag has that id.

    The boolean is what separates "deleted" from "was never there" for the
    router, which owes the caller a 404 rather than a silent 204 for the second.
    """
    rows = store().query_write(
        s.tag.delete().where(s.tag.c.id == tag_id).returning(s.tag.c.id)
    )
    return bool(rows)
