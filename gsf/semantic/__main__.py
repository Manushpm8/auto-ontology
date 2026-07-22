"""CLI entry point for compilation and GSF model-file interchange."""

from __future__ import annotations

import argparse
import logging
import sys
from pathlib import Path

from gsf.env import load_env

load_env()


def _add_database_argument(parser: argparse.ArgumentParser) -> None:
    parser.add_argument(
        "--database-name",
        required=True,
        help="GSF catalog Database.name.",
    )


def _add_log_level_argument(parser: argparse.ArgumentParser) -> None:
    parser.add_argument(
        "--log-level",
        default="INFO",
        choices=["DEBUG", "INFO", "WARNING", "ERROR"],
    )


def _build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="Compile semantics or import/export standalone GSF YAML"
    )
    subparsers = parser.add_subparsers(dest="command", required=True)

    compile_parser = subparsers.add_parser(
        "compile",
        help="Compile business taxonomy using the GSF semantic pipeline",
    )
    _add_database_argument(compile_parser)
    _add_log_level_argument(compile_parser)

    import_parser = subparsers.add_parser(
        "import",
        help="Import a standalone GSF semantic-model YAML file",
    )
    _add_database_argument(import_parser)
    _add_log_level_argument(import_parser)
    import_parser.add_argument(
        "-i",
        "--input",
        required=True,
        type=Path,
        help="GSF semantic-model YAML input file.",
    )
    import_parser.add_argument(
        "--no-replace",
        action="store_true",
        help="Keep graph objects from a previous import of this model.",
    )
    import_parser.add_argument(
        "--skip-embeddings",
        action="store_true",
        help="Do not refresh semantic vector embeddings after import.",
    )

    export_parser = subparsers.add_parser(
        "export",
        help="Export the semantic graph as standalone GSF YAML",
    )
    _add_database_argument(export_parser)
    _add_log_level_argument(export_parser)
    export_parser.add_argument(
        "-o",
        "--output",
        type=Path,
        help="Output file. Omit to write YAML to stdout.",
    )
    export_parser.add_argument(
        "--model-name",
        help="Limit export to one named GSF semantic model.",
    )
    return parser


def _configure_logging(level: str) -> None:
    logging.basicConfig(
        level=getattr(logging, level),
        format="%(asctime)s [%(name)s] %(levelname)s: %(message)s",
        datefmt="%H:%M:%S",
    )


def main(argv: list[str] | None = None) -> None:
    arguments = list(sys.argv[1:] if argv is None else argv)
    commands = {"compile", "import", "export"}
    if (
        arguments
        and arguments[0] not in commands
        and arguments[0] not in {"-h", "--help"}
    ):
        # Preserve the original `python -m gsf.semantic --database-name ...`
        # compilation interface.
        arguments.insert(0, "compile")
    args = _build_parser().parse_args(arguments)
    _configure_logging(args.log_level)

    if args.command == "compile":
        from gsf.semantic.compile import run_semantic_compilation

        run_semantic_compilation(args.database_name)
        return

    from gsf.semantic.model_yaml import (
        SemanticModelYamlError,
        export_model_yaml,
        import_model_yaml,
    )

    try:
        if args.command == "import":
            summary = import_model_yaml(
                args.input.read_text(encoding="utf-8"),
                database_name=args.database_name,
                replace=not args.no_replace,
                embed=not args.skip_embeddings,
            )
            print(
                f"Imported GSF model {summary.model!r}: "
                f"{summary.terms} terms, "
                f"{summary.column_attributes} column attributes, "
                f"{summary.sql_attributes} SQL attributes, "
                f"{summary.semantic_foreign_keys} semantic foreign keys, "
                f"{summary.embeddings} embeddings"
            )
            return

        output = export_model_yaml(
            database_name=args.database_name,
            model_name=args.model_name,
        )
        if args.output is None:
            print(output, end="")
        else:
            args.output.write_text(output, encoding="utf-8")
            print(f"Exported GSF model to {args.output}")
    except (SemanticModelYamlError, OSError, UnicodeError) as exc:
        print(f"Error: {exc}", file=sys.stderr)
        raise SystemExit(1) from exc


if __name__ == "__main__":
    main()
