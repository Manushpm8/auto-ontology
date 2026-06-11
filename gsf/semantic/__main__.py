"""CLI entry point for semantic layer compilation.

Usage:
    python -m gsf.semantic --database-name dor_prod
"""

from __future__ import annotations

import argparse
import logging

from gsf.server.env import load_server_env

load_server_env()


def main() -> None:
    parser = argparse.ArgumentParser(
        description="Compile business taxonomy (Term, ColumnAttribute, sample values)"
    )
    parser.add_argument(
        "--database-name",
        required=True,
        help="Database name — must match the SQL connector and tabular ingest.",
    )
    parser.add_argument(
        "--no-resume",
        action="store_true",
        help="Clear reviewed flags and start fresh.",
    )
    parser.add_argument(
        "--no-embed",
        action="store_true",
        help="Skip semantic VDB embedding after compilation.",
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

    from gsf.semantic.compile import run_semantic_compilation

    run_semantic_compilation(
        args.database_name,
        embed=not args.no_embed,
        resume=not args.no_resume,
    )


if __name__ == "__main__":
    main()
