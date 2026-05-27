"""Run ontology extraction — per-table attributes & concept assignment.

Usage::

    uv run python -m gsf.ontology.attributes_and_concepts
"""

from __future__ import annotations

import json
import logging
import os

from gsf.ontology.attributes_and_concepts.attributes_extraction import (
    extract_attributes,
)

DATABASE_NAME = os.environ.get("ONTOLOGY_DB_NAME", "bird")
SKIP_THRESHOLD = int(os.environ.get("ONTOLOGY_SKIP_THRESHOLD", "0"))

if __name__ == "__main__":
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s %(levelname)s %(name)s: %(message)s",
    )

    result = extract_attributes(
        database_name=DATABASE_NAME,
        skip_threshold=SKIP_THRESHOLD,
    )

    print("\n=== Phase 1 Summary ===")
    print(json.dumps(result, indent=2))
