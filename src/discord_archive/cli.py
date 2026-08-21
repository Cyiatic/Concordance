from __future__ import annotations

import argparse
import sys
from pathlib import Path

from .core import (
    build_archive,
    import_data_package,
    import_transcript,
    load_json,
    materialize_remote_media,
    merge_transcripts,
    validate_archive,
    verify_transcript_coverage,
    verify_build,
)


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

    verify = subparsers.add_parser(
        "verify",
        help="verify a generated offline viewer, archive, and local assets",
    )
    verify.add_argument("input", type=_path)

    importer = subparsers.add_parser(
        "import-data-package",
        help="import message JSON files from an official Discord Data Package",
    )
    importer.add_argument("--input", required=True, type=_path)
    importer.add_argument("--output", required=True, type=_path)

    transcript = subparsers.add_parser(
        "import-transcript",
        help="import a user-supplied transcript JSON file without network access",
    )
    transcript.add_argument("--input", required=True, type=_path)
    transcript.add_argument("--output", required=True, type=_path)

    media = subparsers.add_parser(
        "materialize-media",
        help="explicitly copy Discord CDN image references into local archive assets",
    )
    media.add_argument("--input", required=True, type=_path)
    media.add_argument("--output", required=True, type=_path)
    media.add_argument(
        "--allow-remote",
        action="store_true",
        help="required acknowledgement that already-recorded Discord CDN URLs may be copied",
    )

    merge = subparsers.add_parser(
        "merge-transcripts",
        help="merge overlapping attended Discord transcript ranges",
    )
    merge.add_argument("--input", action="append", required=True, type=_path, help="capture JSON; repeat for each range")
    merge.add_argument("--output", required=True, type=_path)
    merge.add_argument("--reached-start", action="store_true", help="attest that the oldest DM boundary was reached")
    merge.add_argument("--reached-end", action="store_true", help="attest that the newest DM boundary was reached")

    coverage = subparsers.add_parser(
        "verify-coverage",
        help="verify the range-coverage report embedded in a transcript or archive",
    )
    coverage.add_argument("input", type=_path)

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

        if args.command == "verify":
            errors = verify_build(args.input)
            if errors:
                for error in errors:
                    print(f"ERROR: {error}", file=sys.stderr)
                return 2
            print(f"Verified offline viewer: {args.input}")
            return 0

        if args.command == "import-data-package":
            archive = import_data_package(args.input, args.output)
            print(f"Imported archive: {args.output}")
            print(f"Messages: {len(archive['messages'])}")
            summary = archive.get("metadata", {}).get("source", {}).get("import_summary", {})
            skipped = summary.get("records_skipped", 0)
            unreadable = len(summary.get("unreadable_files", []))
            if skipped or unreadable:
                print(
                    f"Warnings: skipped {skipped} record(s) and found {unreadable} unreadable file(s).",
                    file=sys.stderr,
                )
                print("See metadata.source.import_summary in the archive for details.", file=sys.stderr)
            return 0

        if args.command == "import-transcript":
            archive = import_transcript(args.input, args.output)
            print(f"Imported transcript archive: {args.output}")
            print(f"Messages: {len(archive['messages'])}")
            print(f"Participants: {len(archive['participants'])}")
            return 0

        if args.command == "materialize-media":
            if not args.allow_remote:
                print("ERROR: materialize-media requires --allow-remote for this explicit network step.", file=sys.stderr)
                return 2
            summary = materialize_remote_media(args.input, args.output)
            print(f"Materialized local media archive: {args.output}")
            print(f"Downloaded: {summary['downloaded']}")
            print(f"Reused: {summary['reused']}")
            print(f"Skipped: {summary['skipped']}")
            return 0

        if args.command == "merge-transcripts":
            summary = merge_transcripts(
                args.input,
                args.output,
                reached_start=args.reached_start,
                reached_end=args.reached_end,
            )
            coverage = summary["coverage"]
            print(f"Merged transcript: {args.output}")
            print(f"Messages: {summary['messages']}")
            print(f"Participants: {summary['participants']}")
            print(f"Duplicate overlap records: {summary['duplicates']}")
            print(f"Coverage: {coverage['status']} ({coverage['range_count']} range(s))")
            for note in coverage.get("notes", []):
                print(f"Coverage note: {note}")
            if coverage.get("next_action"):
                print(f"Next action: {coverage['next_action']}")
            return 0

        if args.command == "verify-coverage":
            coverage = verify_transcript_coverage(args.input)
            print(f"Coverage: {coverage.get('status', 'unverified')}")
            print(f"Ranges: {coverage.get('range_count', 0)}")
            print(f"Unique messages: {coverage.get('unique_message_count', 0)}")
            print(f"Complete: {bool(coverage.get('complete'))}")
            for note in coverage.get("notes", []):
                print(f"Coverage note: {note}")
            if coverage.get("next_action"):
                print(f"Next action: {coverage['next_action']}")
            return 0 if coverage.get("complete") else 2

    except (OSError, ValueError, FileNotFoundError) as error:
        print(f"ERROR: {error}", file=sys.stderr)
        return 1

    return 1
