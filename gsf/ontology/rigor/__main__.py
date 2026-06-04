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
        "--schema-name",
        default=None,
        help=(
            "Neo4j Schema node name (e.g. 'public'). "
            "Defaults to --database-name for BIRD compatibility."
        ),
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
    from gsf.ontology.rigor.loaders import fetch_schemas_for_database
    from gsf.ontology.rigor.pipeline import build_ontology

    logger = logging.getLogger(__name__)

    # Build list of (database_name, schema_name) pairs to process.
    if args.all:
        # BIRD mode: DOMAIN_MAP keys are schema names used as db identifiers
        targets = [(name, name) for name in DOMAIN_MAP.keys()]
    elif args.schema_name:
        targets = [(args.database_name, args.schema_name)]
    else:
        # Auto-discover all schemas under the database in Neo4j
        schemas = fetch_schemas_for_database(args.database_name)
        if not schemas:
            logger.error(
                "No schemas found for database %r in Neo4j", args.database_name
            )
            return
        logger.info(
            "Discovered %d schema(s) for %r: %s",
            len(schemas),
            args.database_name,
            schemas,
        )
        targets = [(args.database_name, s) for s in schemas]

    all_summaries: dict[str, dict] = {}

    for i, (db_name, schema) in enumerate(targets, 1):
        label = f"{db_name}.{schema}" if db_name != schema else db_name
        logger.info(
            "\n%s [%d/%d] %s %s",
            "=" * 20,
            i,
            len(targets),
            label,
            "=" * 20,
        )

        ontology = build_ontology(
            database_name=db_name,
            bird_root=args.bird_root,
            skip_threshold=args.skip_threshold,
            write_to_neo4j=not args.no_write,
            resume=not args.no_resume,
            schema_name=schema,
        )

        if not args.no_write and not args.no_embed:
            from gsf.ontology.rigor.embed import embed_ontology

            embed_ontology(ontology, database_name=db_name, schema_name=schema)

        all_summaries[label] = {
            "business_terms": len(ontology.business_terms),
            "attributes": len(ontology.attributes),
            "object_properties": len(ontology.object_properties),
            "metrics": len(ontology.metrics),
            "term_names": ontology.term_names(),
        }

    print(json.dumps(all_summaries, indent=2))


if __name__ == "__main__":
    main()
