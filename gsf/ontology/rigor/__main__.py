"""CLI entry point for the Rigor ontology pipeline.

Usage:
    python -m gsf.ontology.rigor --database-name financial
    python -m gsf.ontology.rigor --all --bird-root ~/bird/minidev/MINIDEV
"""

from __future__ import annotations

import argparse
import json
import logging

from dotenv import load_dotenv

load_dotenv()


def main() -> None:
    parser = argparse.ArgumentParser(
        description="Rigor: Iterative Business Ontology Construction"
    )
    group = parser.add_mutually_exclusive_group(required=True)
    group.add_argument(
        "--database-name",
        help="Name of a single database in Neo4j to build ontology for.",
    )
    group.add_argument(
        "--all",
        action="store_true",
        help="Run on all BIRD databases from the domain map.",
    )
    parser.add_argument(
        "--bird-root",
        default=None,
        help="Path to BIRD minidev root for supplementary evidence/descriptions.",
    )
    parser.add_argument(
        "--skip-threshold",
        type=int,
        default=0,
        help="Skip tables with fewer than N SQL references (default: 0).",
    )
    parser.add_argument(
        "--no-write",
        action="store_true",
        help="Skip writing results to Neo4j (dry run).",
    )
    parser.add_argument(
        "--no-resume",
        action="store_true",
        help="Ignore checkpoints and start fresh (default: resume from last checkpoint).",
    )
    parser.add_argument(
        "--no-embed",
        action="store_true",
        help="Skip embedding ontology elements into pgvector.",
    )
    parser.add_argument(
        "--log-level",
        default="INFO",
        choices=["DEBUG", "INFO", "WARNING", "ERROR"],
        help="Logging level (default: INFO).",
    )

    args = parser.parse_args()

    logging.basicConfig(
        level=getattr(logging, args.log_level),
        format="%(asctime)s [%(name)s] %(levelname)s: %(message)s",
        datefmt="%H:%M:%S",
    )

    from gsf.ontology.rigor.external_vocab import DOMAIN_MAP
    from gsf.ontology.rigor.pipeline import build_ontology

    db_names: list[str] = list(DOMAIN_MAP.keys()) if args.all else [args.database_name]

    logger = logging.getLogger(__name__)
    all_summaries: dict[str, dict] = {}

    for i, db_name in enumerate(db_names, 1):
        logger.info(
            "\n%s [%d/%d] Database: %s %s",
            "=" * 20,
            i,
            len(db_names),
            db_name,
            "=" * 20,
        )

        ontology = build_ontology(
            database_name=db_name,
            bird_root=args.bird_root,
            skip_threshold=args.skip_threshold,
            write_to_neo4j=not args.no_write,
            resume=not args.no_resume,
        )

        if not args.no_write and not args.no_embed:
            from gsf.ontology.rigor.embed import embed_ontology

            embed_ontology(ontology, database_name="bird", schema_name=db_name)

        all_summaries[db_name] = {
            "business_terms": len(ontology.business_terms),
            "attributes": len(ontology.attributes),
            "object_properties": len(ontology.object_properties),
            "metrics": len(ontology.metrics),
            "term_names": ontology.term_names(),
        }

    print(json.dumps(all_summaries, indent=2))


if __name__ == "__main__":
    main()
