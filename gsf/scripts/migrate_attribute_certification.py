# SPDX-FileCopyrightText: Copyright (c) 2026, NVIDIA CORPORATION & AFFILIATES.
# All rights reserved.
# SPDX-License-Identifier: Apache-2.0

"""One-off migration: collapse attribute certification to a single flag.

ColumnAttribute and SqlAttribute nodes used to carry two boolean flags,
``name_certified`` and ``description_certified``. They now carry a single
``certified`` flag. This script seeds ``certified`` as the logical AND of the
two legacy flags (so only fully-certified attributes stay certified) and
removes the old properties.

Idempotent: re-running it is safe. Nodes that already have ``certified`` and
no legacy flags are left untouched.

Usage:
    python -m gsf.scripts.migrate_attribute_certification
"""

from __future__ import annotations

import argparse
import logging

from gsf.env import load_env

load_env()

logger = logging.getLogger(__name__)


def migrate_attribute_certification() -> dict[str, int]:
    """Seed ``certified`` from the legacy flags and drop the old props.

    Returns a mapping of label -> number of nodes updated.
    """
    from nemo_retriever.tabular_data.neo4j import get_neo4j_conn

    from gsf.semantic.constants import (
        LABEL_COLUMN_ATTRIBUTE,
        LABEL_SQL_ATTRIBUTE,
    )

    conn = get_neo4j_conn()
    counts: dict[str, int] = {}
    for label in (LABEL_COLUMN_ATTRIBUTE, LABEL_SQL_ATTRIBUTE):
        rows = conn.query_write(
            f"""
            MATCH (attr:{label})
            WHERE attr.name_certified IS NOT NULL
               OR attr.description_certified IS NOT NULL
               OR attr.certified IS NULL
            SET attr.certified = (
                coalesce(attr.name_certified, false)
                AND coalesce(attr.description_certified, false)
            )
            REMOVE attr.name_certified, attr.description_certified
            RETURN count(attr) AS updated
            """,
        )
        counts[label] = rows[0]["updated"] if rows else 0
    return counts


def main() -> None:
    parser = argparse.ArgumentParser(
        description=(
            "Collapse attribute name/description certification into a single "
            "`certified` flag on ColumnAttribute and SqlAttribute nodes."
        )
    )
    parser.add_argument(
        "--log-level",
        default="INFO",
        choices=["DEBUG", "INFO", "WARNING", "ERROR"],
    )
    args = parser.parse_args()

    logging.basicConfig(
        level=getattr(logging, args.log_level),
        format="%(asctime)s [%(name)s] %(levelname)s: %(message)s",
        datefmt="%H:%M:%S",
    )

    counts = migrate_attribute_certification()
    for label, updated in counts.items():
        logger.info("Migrated %d %s node(s)", updated, label)


if __name__ == "__main__":
    main()
