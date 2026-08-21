from __future__ import annotations

import argparse
import sys
from pathlib import Path

from .core import build_archive, import_data_package, load_json, validate_archive


def _path(value: str) -> Path:
    return Path(value).expanduser()


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="discord-archive",
        description="Validate, import, and build offline conversation archives.",
    )
    subparsers = parser.add_subparsers(dest="command", required=True)

    validate = subparsers.add_parser("validate", help="validate a normalized archive JSON file")
    validate.add_argument("input", type=_path)

    build = subparsers.add_parser("build", help="build a portable offline viewer")
    build.add_argument("input", type=_path)
    build.add_argument("--output", required=True, type=_path)

    importer = subparsers.add_parser(
        "import-data-package",
        help="import message JSON files from an official Discord Data Package",
    )
    importer.add_argument("--input", required=True, type=_path)
    importer.add_argument("--output", required=True, type=_path)

    return parser


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    try:
        if args.command == "validate":
            archive = load_json(args.input)
            errors = validate_archive(archive)
            if errors:
                for error in errors:
                    print(f"ERROR: {error}", file=sys.stderr)
                return 2
            print(f"Valid archive: {args.input}")
            print(f"Messages: {len(archive['messages'])}")
            print(f"Participants: {len(archive['participants'])}")
            return 0

        if args.command == "build":
            missing = build_archive(args.input, args.output)
            print(f"Built offline viewer: {args.output / 'index.html'}")
            if missing:
                print("Missing local assets:")
                for reference in missing:
                    print(f"- {reference}")
            return 0

        if args.command == "import-data-package":
            archive = import_data_package(args.input, args.output)
            print(f"Imported archive: {args.output}")
            print(f"Messages: {len(archive['messages'])}")
            return 0

    except (OSError, ValueError, FileNotFoundError) as error:
        print(f"ERROR: {error}", file=sys.stderr)
        return 1

    return 1
