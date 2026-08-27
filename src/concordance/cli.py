from __future__ import annotations

import argparse
import getpass
import json
import sys
from pathlib import Path

from .core import (
    add_capture_to_session,
    attach_capture_evidence,
    audit_archive_media,
    build_archive,
    build_catalog,
    build_capture_session_dashboard,
    capture_session_next,
    capture_session_status,
    decrypt_bundle,
    encrypt_bundle,
    export_bundle,
    export_evidence,
    finalize_capture_session,
    import_data_package,
    import_bundle,
    import_transcript,
    init_capture_session,
    load_json,
    materialize_remote_media,
    migrate_archive,
    merge_transcripts,
    redact_archive,
    set_capture_session_checkpoints,
    validate_archive,
    verify_catalog,
    verify_capture_session_dashboard,
    verify_evidence,
    verify_transcript_coverage,
    verify_build,
)


def _path(value: str) -> Path:
    return Path(value).expanduser()


def _print_checkpoint_summary(coverage: dict) -> None:
    checkpoints = coverage.get("checkpoints")
    if not isinstance(checkpoints, list) or not checkpoints:
        return
    print("Expected date checkpoints:")
    for checkpoint in checkpoints:
        if not isinstance(checkpoint, dict):
            continue
        date = checkpoint.get("date") or "unknown date"
        observed = "observed" if checkpoint.get("observed") else "MISSING"
        range_count = checkpoint.get("range_count")
        suffix = f" in {range_count} range(s)" if isinstance(range_count, int) else ""
        print(f"- {date}: {observed}{suffix}")


def _password_from_args(args: argparse.Namespace) -> str:
    if args.password_file:
        password = args.password_file.read_text(encoding="utf-8").rstrip("\r\n")
    else:
        password = getpass.getpass("Bundle password: ")
    if not password:
        raise ValueError("A non-empty bundle password is required")
    return password


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="concordance",
        description="Validate, import, and build offline conversation archives.",
    )
    subparsers = parser.add_subparsers(dest="command", required=True)

    validate = subparsers.add_parser("validate", help="validate a normalized archive JSON file")
    validate.add_argument("input", type=_path)

    build = subparsers.add_parser("build", help="build a portable offline viewer")
    build.add_argument("input", type=_path)
    build.add_argument("--output", required=True, type=_path)

    bundle_export = subparsers.add_parser(
        "export-bundle",
        help="export an archive file or generated viewer directory as a portable ZIP",
    )
    bundle_export.add_argument("--input", required=True, type=_path)
    bundle_export.add_argument("--output", required=True, type=_path)

    bundle_import = subparsers.add_parser(
        "import-bundle",
        help="safely extract a portable archive bundle",
    )
    bundle_import.add_argument("--input", required=True, type=_path)
    bundle_import.add_argument("--output", required=True, type=_path)

    bundle_encrypt = subparsers.add_parser(
        "encrypt-bundle",
        help="encrypt a portable bundle with a password-protected AES-GCM envelope",
    )
    bundle_encrypt.add_argument("--input", required=True, type=_path)
    bundle_encrypt.add_argument("--output", required=True, type=_path)
    bundle_encrypt.add_argument("--password-file", type=_path, help="read the password from a private file; otherwise prompt securely")

    bundle_decrypt = subparsers.add_parser(
        "decrypt-bundle",
        help="decrypt and safely extract a password-protected bundle",
    )
    bundle_decrypt.add_argument("--input", required=True, type=_path)
    bundle_decrypt.add_argument("--output", required=True, type=_path)
    bundle_decrypt.add_argument("--password-file", type=_path, help="read the password from a private file; otherwise prompt securely")

    redact = subparsers.add_parser(
        "redact",
        help="create a safe-share archive with content, identities, and media redacted",
    )
    redact.add_argument("--input", required=True, type=_path)
    redact.add_argument("--output", required=True, type=_path)
    redact.add_argument("--profile", default="safe-share", choices=("safe-share",))

    migrate = subparsers.add_parser(
        "migrate",
        help="migrate a legacy archive into the current normalized schema",
    )
    migrate.add_argument("--input", required=True, type=_path)
    migrate.add_argument("--output", required=True, type=_path)
    migrate.add_argument("--target-version", type=int, default=1)

    catalog = subparsers.add_parser(
        "build-catalog",
        help="build a local launcher for multiple normalized archives",
    )
    catalog.add_argument("--input", action="append", required=True, type=_path, help="normalized archive JSON; repeat for each conversation")
    catalog.add_argument("--output", required=True, type=_path)
    catalog.add_argument(
        "--include-message-index",
        action="store_true",
        help="opt in to a local cross-archive message search index; keep the output private",
    )

    verify = subparsers.add_parser(
        "verify",
        help="verify a generated offline viewer, archive, and local assets",
    )
    verify.add_argument("input", type=_path)

    catalog_verify = subparsers.add_parser(
        "verify-catalog",
        help="verify a generated multi-archive catalog and linked viewers",
    )
    catalog_verify.add_argument("input", type=_path)

    evidence = subparsers.add_parser(
        "export-evidence",
        help="export a private, message-free provenance report for an archive",
    )
    evidence.add_argument("--input", required=True, type=_path)
    evidence.add_argument("--output", required=True, type=_path)
    evidence.add_argument(
        "--session",
        type=_path,
        help="optional same-directory capture-session manifest to link",
    )
    evidence.add_argument(
        "--dom",
        action="append",
        default=[],
        type=_path,
        help="optional local rendered-DOM snapshot to copy into the evidence bundle; repeat as needed",
    )
    evidence.add_argument(
        "--screenshot",
        action="append",
        default=[],
        type=_path,
        help="optional local screenshot to copy into the evidence bundle; repeat as needed",
    )

    evidence_verify = subparsers.add_parser(
        "verify-evidence",
        help="verify an evidence report, archive, local assets, and session link",
    )
    evidence_verify.add_argument("input", type=_path)

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
        help="explicitly copy already-recorded Discord CDN media and profile references into local archive assets",
    )
    media.add_argument("--input", required=True, type=_path)
    media.add_argument("--output", required=True, type=_path)
    media.add_argument(
        "--allow-remote",
        action="store_true",
        help="required acknowledgement that already-recorded Discord CDN URLs may be copied",
    )
    media.add_argument(
        "--profile-only",
        action="store_true",
        help="copy only captured participant/profile pictures; leave message media as references",
    )

    media_audit = subparsers.add_parser(
        "audit-media",
        help="report offline readiness and unresolved media references without downloading",
    )
    media_audit.add_argument("--input", required=True, type=_path)
    media_audit.add_argument(
        "--output",
        type=_path,
        help="optional same-directory JSON report; without it, print the summary only",
    )

    merge = subparsers.add_parser(
        "merge-transcripts",
        help="merge overlapping attended Discord transcript ranges",
    )
    merge.add_argument("--input", action="append", required=True, type=_path, help="capture JSON; repeat for each range")
    merge.add_argument("--output", required=True, type=_path)
    merge.add_argument("--reached-start", action="store_true", help="attest that the oldest DM boundary was reached")
    merge.add_argument("--reached-end", action="store_true", help="attest that the newest DM boundary was reached")
    merge.add_argument(
        "--expect-date",
        action="append",
        default=[],
        metavar="YYYY-MM-DD",
        help="expected calendar date that must appear in a captured message (UTC or visible Discord date); repeat as needed",
    )

    coverage = subparsers.add_parser(
        "verify-coverage",
        help="verify the range-coverage report embedded in a transcript or archive",
    )
    coverage.add_argument("input", type=_path)

    session = subparsers.add_parser(
        "capture-session",
        help="track overlapping attended Discord capture ranges",
    )
    session_subparsers = session.add_subparsers(dest="session_command", required=True)

    session_init = session_subparsers.add_parser(
        "init",
        help="create an empty private capture-session manifest",
    )
    session_init.add_argument("--output", required=True, type=_path)
    session_init.add_argument("--channel-id", type=str)
    session_init.add_argument("--title", type=str)
    session_init.add_argument(
        "--expect-date",
        action="append",
        default=[],
        metavar="YYYY-MM-DD",
        help="expected calendar date that must appear in a captured message (UTC or visible Discord date); repeat as needed",
    )

    session_add = session_subparsers.add_parser(
        "add",
        help="add one user-captured rendered range to a session",
    )
    session_add.add_argument("--session", required=True, type=_path)
    session_add.add_argument("--input", required=True, type=_path)

    session_checkpoints = session_subparsers.add_parser(
        "checkpoints",
        help="add or replace expected date checkpoints in a session",
    )
    session_checkpoints.add_argument("--session", required=True, type=_path)
    session_checkpoints.add_argument(
        "--expect-date",
        action="append",
        default=[],
        metavar="YYYY-MM-DD",
        help="expected calendar date (UTC or visible Discord date); repeat as needed",
    )
    session_checkpoints.add_argument(
        "--replace",
        action="store_true",
        help="replace existing checkpoints instead of adding to them; with no dates, clear them",
    )

    session_status = session_subparsers.add_parser(
        "status",
        help="show session coverage and the next capture action",
    )
    session_status.add_argument("--session", required=True, type=_path)

    session_next = session_subparsers.add_parser(
        "next",
        help="show the next bounded browser step and adapter options",
    )
    session_next.add_argument("--session", required=True, type=_path)

    session_dashboard = session_subparsers.add_parser(
        "dashboard",
        help="build a local guided-capture dashboard without message bodies",
    )
    session_dashboard.add_argument("--session", required=True, type=_path)
    session_dashboard.add_argument("--output", required=True, type=_path)

    session_dashboard_verify = session_subparsers.add_parser(
        "verify-dashboard",
        help="verify a generated guided-capture dashboard",
    )
    session_dashboard_verify.add_argument("input", type=_path)

    session_evidence = session_subparsers.add_parser(
        "attach-evidence",
        help="attach explicit DOM snapshots or screenshots to one tracked range",
    )
    session_evidence.add_argument("--session", required=True, type=_path)
    session_evidence.add_argument("--capture", required=True, type=_path)
    session_evidence.add_argument(
        "--dom",
        action="append",
        default=[],
        type=_path,
        help="local rendered-DOM snapshot; repeat as needed",
    )
    session_evidence.add_argument(
        "--screenshot",
        action="append",
        default=[],
        type=_path,
        help="local screenshot; repeat as needed",
    )

    session_finalize = session_subparsers.add_parser(
        "finalize",
        help="merge tracked ranges into a transcript and record its coverage",
    )
    session_finalize.add_argument("--session", required=True, type=_path)
    session_finalize.add_argument("--output", required=True, type=_path)
    session_finalize.add_argument(
        "--reached-start",
        action="store_true",
        help="attest that the oldest DM boundary was reached",
    )
    session_finalize.add_argument(
        "--reached-end",
        action="store_true",
        help="attest that the newest DM boundary was reached",
    )

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

        if args.command == "redact":
            archive = redact_archive(args.input, args.output, profile=args.profile)
            print(f"Redacted archive: {args.output}")
            print(f"Messages retained: {len(archive['messages'])}")
            print("Profile: safe-share")
            return 0

        if args.command == "migrate":
            result = migrate_archive(args.input, args.output, target_version=args.target_version)
            print(f"Migrated archive: {args.output}")
            print(f"Schema: {result['from_version']} -> {result['to_version']}")
            print(f"Migration path: {' -> '.join(str(version) for version in result.get('migration_path', []))}")
            print(f"Changed: {bool(result['changed'])}")
            return 0

        if args.command == "export-bundle":
            summary = export_bundle(args.input, args.output)
            print(f"Exported bundle: {args.output}")
            print(f"Files: {summary['files']}")
            print(f"Bytes: {summary['bytes']}")
            return 0

        if args.command == "import-bundle":
            summary = import_bundle(args.input, args.output)
            print(f"Imported bundle: {args.output}")
            print(f"Files: {summary['files']}")
            print(f"Verified viewer: {bool(summary['verified'])}")
            return 0

        if args.command == "encrypt-bundle":
            summary = encrypt_bundle(args.input, args.output, _password_from_args(args))
            print(f"Encrypted bundle: {args.output}")
            print(f"Bytes: {summary['bytes']}")
            return 0

        if args.command == "decrypt-bundle":
            summary = decrypt_bundle(args.input, args.output, _password_from_args(args))
            print(f"Decrypted bundle: {args.output}")
            print(f"Files: {summary['files']}")
            print(f"Verified viewer: {bool(summary['verified'])}")
            return 0

        if args.command == "build-catalog":
            summary = build_catalog(args.input, args.output, include_message_index=args.include_message_index)
            print(f"Built archive catalog: {args.output / 'index.html'}")
            print(f"Archives: {summary['archives']}")
            for missing in summary.get("missing_assets", []):
                print(f"Missing local assets for {missing['archive_id']}:")
                for reference in missing["references"]:
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

        if args.command == "verify-catalog":
            errors = verify_catalog(args.input)
            if errors:
                for error in errors:
                    print(f"ERROR: {error}", file=sys.stderr)
                return 2
            print(f"Verified archive catalog: {args.input}")
            return 0

        if args.command == "export-evidence":
            evidence = export_evidence(
                args.input,
                args.output,
                session_path=args.session,
                dom_paths=args.dom,
                screenshot_paths=args.screenshot,
            )
            print(f"Exported evidence report: {args.output}")
            print(f"Archive SHA-256: {evidence['archive']['sha256']}")
            print(f"Coverage: {evidence['coverage'].get('status', 'unverified')}")
            session = evidence.get("capture_session")
            if isinstance(session, dict) and session.get("archive_match") is False:
                print(
                    "Capture session finalized a different transcript filename; relationship recorded.",
                    file=sys.stderr,
                )
            missing = [
                asset["path"]
                for asset in evidence.get("local_assets", [])
                if not asset.get("exists")
            ]
            if missing:
                print("Missing local assets:", file=sys.stderr)
                for reference in missing:
                    print(f"- {reference}", file=sys.stderr)
            return 0

        if args.command == "verify-evidence":
            errors = verify_evidence(args.input)
            if errors:
                for error in errors:
                    print(f"ERROR: {error}", file=sys.stderr)
                return 2
            print(f"Verified evidence report: {args.input}")
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
            summary = materialize_remote_media(args.input, args.output, profile_only=args.profile_only)
            print(f"Materialized local media archive: {args.output}")
            print(f"Downloaded: {summary['downloaded']}")
            print(f"Reused: {summary['reused']}")
            print(f"Skipped: {summary['skipped']}")
            return 0

        if args.command == "audit-media":
            report = audit_archive_media(args.input, args.output)
            counts = report["counts"]
            print(f"Media audit: {args.input}")
            print(f"Total references: {counts['total']}")
            print(f"Offline-ready: {counts['offline_ready']}")
            print(f"Downloadable with explicit approval: {counts['downloadable']}")
            print(f"Missing local files: {counts['missing_local']}")
            print(f"Reference-only hosts: {counts['reference_only']}")
            print(f"Next action: {report['next_action']}")
            if args.output:
                print(f"Report: {args.output}")
            return 0 if report["unresolved_count"] == 0 else 2

        if args.command == "merge-transcripts":
            summary = merge_transcripts(
                args.input,
                args.output,
                reached_start=args.reached_start,
                reached_end=args.reached_end,
                expected_dates=args.expect_date,
            )
            coverage = summary["coverage"]
            print(f"Merged transcript: {args.output}")
            print(f"Messages: {summary['messages']}")
            print(f"Participants: {summary['participants']}")
            print(f"Duplicate overlap records: {summary['duplicates']}")
            print(f"Coverage: {coverage['status']} ({coverage['range_count']} range(s))")
            for note in coverage.get("notes", []):
                print(f"Coverage note: {note}")
            _print_checkpoint_summary(coverage)
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
            _print_checkpoint_summary(coverage)
            if coverage.get("next_action"):
                print(f"Next action: {coverage['next_action']}")
            return 0 if coverage.get("complete") else 2

        if args.command == "capture-session":
            if args.session_command == "init":
                init_capture_session(
                    args.output,
                    channel_id=args.channel_id,
                    title=args.title,
                    expected_dates=args.expect_date,
                )
                print(f"Initialized capture session: {args.output}")
                return 0

            if args.session_command == "add":
                result = add_capture_to_session(args.session, args.input)
                capture = result["capture"]
                coverage = result["coverage"]
                print(f"Added capture: {capture['path']}")
                print(f"SHA-256: {capture['sha256']}")
                print(f"Coverage: {coverage['status']} ({coverage['range_count']} range(s))")
                _print_checkpoint_summary(coverage)
                print(f"Next action: {coverage['next_action']}")
                return 0

            if args.session_command == "checkpoints":
                result = set_capture_session_checkpoints(
                    args.session,
                    expected_dates=args.expect_date,
                    replace=args.replace,
                )
                print(f"Updated capture session: {result['session']}")
                print(f"Expected date checkpoints: {', '.join(result['expected_dates']) or 'none'}")
                _print_checkpoint_summary(result["coverage"])
                print(f"Coverage: {result['status']} ({result['coverage']['range_count']} range(s))")
                print(f"Next action: {result['next_action']}")
                return 0

            if args.session_command == "status":
                status = capture_session_status(args.session)
                print(f"Capture session: {status['session']}")
                if status.get("title"):
                    print(f"Title: {status['title']}")
                if status.get("channel_id"):
                    print(f"Channel ID: {status['channel_id']}")
                print(f"Ranges: {status['capture_count']}")
                for capture in status["captures"]:
                    span = f"{capture.get('oldest_timestamp') or '?'} -> {capture.get('newest_timestamp') or '?'}"
                    boundaries = []
                    if capture.get("at_start"):
                        boundaries.append("oldest")
                    if capture.get("at_end"):
                        boundaries.append("newest")
                    boundary_label = f"; boundaries: {', '.join(boundaries)}" if boundaries else ""
                    print(f"- {capture['path']}: {capture['message_count']} message(s); {span}{boundary_label}")
                print(f"Coverage: {status['status']} ({status['coverage']['range_count']} range(s))")
                print(f"Complete: {bool(status['complete'])}")
                _print_checkpoint_summary(status["coverage"])
                media = status.get("media", {})
                if isinstance(media, dict):
                    print(
                        "Media/features: "
                        f"{media.get('attachments', 0)} attachment(s), "
                        f"{media.get('embeds', 0)} embed(s), "
                        f"{media.get('calls', 0)} call(s), "
                        f"{media.get('unapproved_remote_media', 0)} unapproved remote reference(s)"
                    )
                print(f"Next action: {status['next_action']}")
                return 0

            if args.session_command == "next":
                result = capture_session_next(args.session)
                step = result["next_step"]
                print(f"Capture session: {result['session']}")
                print(f"Coverage: {result['status']}")
                print(f"Step: {step['kind']}")
                print(f"Direction: {step['direction']}")
                print(f"Action: {step['action']}")
                print(f"Reason: {step['reason']}")
                if step.get("reference_capture"):
                    print(f"Reference range: {step['reference_capture']}")
                print(f"Adapter options: {json.dumps(step['adapter_options'], separators=(',', ':'))}")
                print(f"After capture: {step['copy_text']}")
                return 0

            if args.session_command == "dashboard":
                result = build_capture_session_dashboard(args.session, args.output)
                print(f"Built capture dashboard: {args.output / 'index.html'}")
                print(f"Ranges: {result['capture_count']}")
                print(f"Coverage: {result['status']}")
                print(f"Complete: {bool(result['complete'])}")
                return 0

            if args.session_command == "attach-evidence":
                if not args.dom and not args.screenshot:
                    raise ValueError("attach-evidence requires --dom and/or --screenshot")
                result = attach_capture_evidence(
                    args.session,
                    args.capture,
                    dom_paths=args.dom,
                    screenshot_paths=args.screenshot,
                )
                print(f"Attached evidence to: {result['capture']['path']}")
                print(f"Files attached: {len(result['attached'])}")
                print(f"Session evidence files: {result['session']['evidence']['files']}")
                print(f"Coverage: {result['session']['status']} ({result['session']['coverage']['range_count']} range(s))")
                return 0

            if args.session_command == "verify-dashboard":
                errors = verify_capture_session_dashboard(args.input)
                if errors:
                    for error in errors:
                        print(f"ERROR: {error}", file=sys.stderr)
                    return 2
                print(f"Verified capture dashboard: {args.input}")
                return 0

            if args.session_command == "finalize":
                result = finalize_capture_session(
                    args.session,
                    args.output,
                    reached_start=args.reached_start,
                    reached_end=args.reached_end,
                )
                coverage = result["coverage"]
                print(f"Finalized transcript: {args.output}")
                print(f"Messages: {result['messages']}")
                print(f"Participants: {result['participants']}")
                print(f"Duplicate overlap records: {result['duplicates']}")
                print(f"Conflicting records: {result['conflicts']}")
                print(f"Coverage: {coverage['status']} ({coverage['range_count']} range(s))")
                for note in coverage.get("notes", []):
                    print(f"Coverage note: {note}")
                _print_checkpoint_summary(coverage)
                print(f"Next action: {coverage['next_action']}")
                return 0

    except (OSError, RuntimeError, ValueError, FileNotFoundError) as error:
        print(f"ERROR: {error}", file=sys.stderr)
        return 1

    return 1
