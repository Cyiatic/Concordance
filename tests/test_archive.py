from __future__ import annotations

import io
import json
import sys
import tempfile
import unittest
from contextlib import redirect_stderr
from copy import deepcopy
from pathlib import Path


PROJECT_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(PROJECT_ROOT / "src"))

from discord_archive.core import (  # noqa: E402
    build_archive,
    import_data_package,
    import_transcript,
    load_json,
    merge_transcripts,
    _template_path,
    validate_archive,
    verify_transcript_coverage,
    verify_build,
)
from discord_archive.cli import main as cli_main  # noqa: E402


class ArchiveTests(unittest.TestCase):
    def setUp(self) -> None:
        self.fixture = PROJECT_ROOT / "fixtures" / "sample" / "archive.json"
        self.archive = load_json(self.fixture)

    def test_sample_archive_is_valid(self) -> None:
        self.assertEqual(validate_archive(self.archive), [])
        self.assertEqual(len(self.archive["messages"]), 5)

    def test_build_copies_viewer_data_and_assets(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            output = Path(temporary) / "sample"
            missing = build_archive(self.fixture, output)
            self.assertEqual(missing, [])
            self.assertTrue((output / "index.html").is_file())
            self.assertTrue((output / "app.js").is_file())
            self.assertTrue((output / "archive.json").is_file())
            self.assertTrue((output / "manifest.json").is_file())
            self.assertTrue((output / "assets" / "avatars" / "mara.svg").is_file())
            self.assertTrue((output / "assets" / "attachments" / "note.svg").is_file())
            html = (output / "index.html").read_text(encoding="utf-8")
            self.assertIn("Archive Ledger", html)
            self.assertIn('src="app.js"', html)
            app = (output / "app.js").read_text(encoding="utf-8")
            self.assertIn("Good one", app)
            self.assertIn("window.__ARCHIVE_DATA__", app)
            self.assertIn("Open local file", app)
            self.assertIn("<audio", app)
            self.assertIn("windowSize: 240", app)
            self.assertIn('data-action="load-newer"', app)
            self.assertIn("searchTextById", app)
            self.assertIn('window.addEventListener("afterprint"', app)
            self.assertIn("Remote image reference", app)
            self.assertIn("Import coverage", app)
            self.assertIn("Unreadable source files", app)
            self.assertIn("coverage-banner", app)
            self.assertIn("This is not yet the whole conversation", app)
            self.assertIn("coverage-ledger", app)
            manifest = load_json(output / "manifest.json")
            self.assertEqual(manifest["manifest_version"], 1)
            self.assertEqual(
                {entry["path"] for entry in manifest["files"]},
                {"archive.json", "app.js", "index.html", "assets/avatars/eli.svg", "assets/avatars/mara.svg", "assets/attachments/note.svg"},
            )
            self.assertEqual(verify_build(output), [])

            (output / "app.js").write_text(app + "\n// tampered", encoding="utf-8")
            self.assertTrue(any("hash mismatch: app.js" in error for error in verify_build(output)))

    def test_payload_is_embedded_without_network_fetch(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            output = Path(temporary) / "sample"
            build_archive(self.fixture, output)
            html = (output / "index.html").read_text(encoding="utf-8")
            self.assertIn('src="app.js"', html)
            app = (output / "app.js").read_text(encoding="utf-8")
            self.assertIn("window.__ARCHIVE_DATA__", app)
            self.assertNotIn("https://cdn.jsdelivr.net", html)

    def test_data_package_importer_accepts_capitalized_message_fields(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            package = Path(temporary) / "DiscordDataPackage"
            message_dir = package / "messages" / "channel-123"
            message_dir.mkdir(parents=True)
            (message_dir / "transcript.json").write_text(
                json.dumps(
                    [
                        {
                            "ID": "discord-message-1",
                            "Timestamp": "2020-01-02T03:04:05Z",
                            "Contents": "Imported text",
                            "Attachments": [],
                            "Reply To": {"ID": "prior-message"},
                            "Reactions": [{"Emoji": "👍", "Count": "2", "Me": "true"}],
                            "Embeds": [{
                                "Title": "Official embed",
                                "Description": "Preserved embed text",
                                "URL": "https://example.invalid/official",
                                "Image URL": "https://example.invalid/official.png",
                            }],
                            "Edited At": "2020-01-02T03:05:05Z",
                            "Message Link": "https://discord.com/channels/@me/demo/discord-message-1",
                        }
                    ]
                ),
                encoding="utf-8",
            )
            (message_dir / "invalid.json").write_text(
                json.dumps(
                    [
                        {"Timestamp": "2020-01-02T03:04:05Z", "Contents": "No source ID"},
                        {"ID": "naive-timestamp", "Timestamp": "2020-01-02T03:04:05", "Contents": "No timezone"},
                    ]
                ),
                encoding="utf-8",
            )
            (message_dir / "broken.json").write_text("{not valid JSON", encoding="utf-8")
            output = Path(temporary) / "imported.json"
            archive = import_data_package(package, output)
            self.assertTrue(output.is_file())
            self.assertEqual(validate_archive(archive), [])
            self.assertEqual(archive["messages"][0]["content"], "Imported text")
            self.assertEqual(archive["messages"][0]["id"], "discord-message-1")
            self.assertEqual(archive["messages"][0]["reply_to"], "prior-message")
            self.assertEqual(archive["messages"][0]["edited_at"], "2020-01-02T03:05:05Z")
            self.assertEqual(archive["messages"][0]["reactions"], [{"emoji": "👍", "count": 2, "me": True}])
            self.assertEqual(archive["messages"][0]["embeds"][0]["title"], "Official embed")
            self.assertEqual(archive["messages"][0]["embeds"][0]["description"], "Preserved embed text")
            self.assertEqual(archive["messages"][0]["embeds"][0]["image_url"], "https://example.invalid/official.png")
            self.assertEqual(archive["messages"][0]["message_link"], "https://discord.com/channels/@me/demo/discord-message-1")
            self.assertEqual(
                archive["messages"][0]["provenance"]["source_file"],
                "messages/channel-123/transcript.json",
            )
            summary = archive["metadata"]["source"]["import_summary"]
            self.assertEqual(summary["files_scanned"], 3)
            self.assertEqual(summary["files_with_records"], 2)
            self.assertEqual(summary["records_seen"], 3)
            self.assertEqual(summary["records_imported"], 1)
            self.assertEqual(summary["records_skipped"], 2)
            self.assertEqual(
                summary["skipped_record_reasons"],
                {"missing_message_id": 1, "timestamp_missing_timezone": 1},
            )
            self.assertEqual(summary["unreadable_files"], ["messages/channel-123/broken.json"])
            self.assertTrue(any("import_summary" in note for note in archive["metadata"]["source"]["notes"]))
            self.assertNotIn(str(package), json.dumps(archive))

    def test_data_package_cli_reports_import_diagnostics(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            package = Path(temporary) / "DiscordDataPackage"
            message_dir = package / "messages" / "channel-123"
            message_dir.mkdir(parents=True)
            (message_dir / "broken.json").write_text("{not valid JSON", encoding="utf-8")
            output = Path(temporary) / "imported.json"
            stderr = io.StringIO()

            with redirect_stderr(stderr):
                exit_code = cli_main(
                    [
                        "import-data-package",
                        "--input",
                        str(package),
                        "--output",
                        str(output),
                    ]
                )

            self.assertEqual(exit_code, 0)
            self.assertIn("unreadable file(s)", stderr.getvalue())
            self.assertIn("metadata.source.import_summary", stderr.getvalue())

    def test_validation_checks_timestamp_shapes_and_asset_paths(self) -> None:
        archive = deepcopy(self.archive)
        archive["messages"][0]["timestamp"] = "2018-11-30T06:50:00"
        archive["messages"][0]["reactions"] = [{"emoji": "👍", "count": -1}]
        archive["messages"][0]["attachments"] = [
            {
                "name": "escape.txt",
                "path": "../escape.txt",
                "mime": "text/plain",
            }
        ]
        archive["messages"][0]["message_link"] = "javascript:alert(1)"
        archive["messages"][0]["provenance"] = {"source_file": "C:\\private\\raw.json"}
        archive["participants"][0]["avatar_ref"] = "C:\\private\\avatar.png"
        errors = validate_archive(archive)
        self.assertTrue(any("timestamp must be an ISO-8601 timestamp with a timezone" in error for error in errors))
        self.assertTrue(any("count must be a non-negative integer" in error for error in errors))
        self.assertTrue(any("safe relative local asset path" in error for error in errors))
        self.assertTrue(any("message_link must be an HTTP(S) URL" in error for error in errors))
        self.assertTrue(any("source_file must be a safe relative source path" in error for error in errors))
        self.assertTrue(any("avatar_ref must be an HTTP(S) URL" in error for error in errors))

    def test_transcript_import_preserves_provenance_and_builds_local_assets(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            (root / "assets" / "avatars").mkdir(parents=True)
            (root / "assets" / "attachments").mkdir(parents=True)
            (root / "assets" / "avatars" / "mara.svg").write_text("avatar", encoding="utf-8")
            (root / "assets" / "attachments" / "recording.mp3").write_bytes(b"synthetic media")
            input_path = root / "transcript.json"
            input_path.write_text(
                json.dumps(
                    {
                        "metadata": {
                            "kind": "direct_message",
                            "title": "Mara and Eli",
                            "channel_handle": "mara-eli",
                            "display_timezone": "America/Phoenix",
                            "source": {"notes": ["Provided by the archive owner."]},
                        },
                        "participants": [
                            {
                                "id": "user-mara",
                                "display_name": "Mara",
                                "username": "mara",
                                "avatar_path": "assets/avatars/mara.svg",
                            },
                            {
                                "id": "user-eli",
                                "display_name": "Eli",
                                "avatar_url": "https://example.invalid/eli.png",
                            },
                        ],
                        "messages": [
                            {
                                "id": "m2",
                                "author_id": "user-eli",
                                "timestamp": "2020-01-02T03:05:00Z",
                                "content": "Reply with a file",
                                "grouped": True,
                                "reply_to": "m1",
                                "edited_at": "2020-01-02T03:06:00Z",
                                "reactions": [{"emoji": "👍", "count": 2, "me": True}],
                                "embeds": [{"title": "Reference", "url": "https://example.invalid/ref"}],
                                "attachments": [
                                    {
                                        "name": "recording.mp3",
                                        "path": "assets/attachments/recording.mp3",
                                        "mime": "audio/mpeg",
                                        "size_bytes": 15,
                                    }
                                ],
                                "message_link": "https://discord.com/channels/@me/demo/m2",
                            },
                            {
                                "id": "m1",
                                "author_id": "user-mara",
                                "timestamp": "2020-01-02T03:04:00Z",
                                "content": "Original message",
                            },
                        ],
                    }
                ),
                encoding="utf-8",
            )
            output = root / "archive.json"
            archive = import_transcript(input_path, output)

            self.assertEqual(validate_archive(archive), [])
            self.assertEqual([message["id"] for message in archive["messages"]], ["m1", "m2"])
            self.assertTrue(archive["messages"][1]["grouped"])
            self.assertEqual(archive["messages"][1]["provenance"]["source_file"], "transcript.json")
            self.assertEqual(archive["messages"][1]["provenance"]["record_index"], 0)
            self.assertEqual(archive["metadata"]["source"]["source_name"], "transcript.json")
            self.assertEqual(archive["metadata"]["channel_handle"], "mara-eli")
            self.assertIn("Provided by the archive owner.", archive["metadata"]["source"]["notes"])
            self.assertEqual(archive["participants"][1]["avatar_ref"], "https://example.invalid/eli.png")
            self.assertNotIn(str(root), json.dumps(archive))

            built = root / "built"
            self.assertEqual(build_archive(output, built), [])
            self.assertTrue((built / "assets" / "avatars" / "mara.svg").is_file())
            self.assertTrue((built / "assets" / "attachments" / "recording.mp3").is_file())
            built_app = (built / "app.js").read_text(encoding="utf-8")
            self.assertIn("recording.mp3", built_app)
            self.assertIn("audio/mpeg", built_app)

    def test_transcript_import_infers_participant_from_author_name(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            input_path = root / "messages.json"
            input_path.write_text(
                json.dumps(
                    [{
                        "timestamp": "2020-01-02T03:04:00Z",
                        "author": "Mara",
                        "content": "Inferred author",
                    }]
                ),
                encoding="utf-8",
            )
            archive = import_transcript(input_path, root / "archive.json")
            self.assertEqual(validate_archive(archive), [])
            self.assertEqual(archive["participants"][0]["id"], "author-mara")
            self.assertEqual(archive["messages"][0]["author_id"], "author-mara")
            self.assertEqual(archive["messages"][0]["provenance"]["id_generated"], True)

    def test_viewer_template_is_available_from_the_source_checkout(self) -> None:
        self.assertTrue(_template_path().is_file())

    def test_visible_browser_capture_is_scoped_and_read_only(self) -> None:
        capture_source = (PROJECT_ROOT / "tools" / "discord_visible_capture.js").read_text(encoding="utf-8")
        self.assertIn("/channels/@me/", capture_source)
        self.assertIn('[role="article"][data-list-item-id]', capture_source)
        self.assertIn("currently rendered by Discord", capture_source)
        self.assertIn("groupStart", capture_source)
        self.assertIn("channel_handle", capture_source)
        self.assertIn("capture_range", capture_source)
        self.assertIn("scroll_height", capture_source)
        self.assertNotIn("document.cookie", capture_source)
        self.assertNotIn("localStorage", capture_source)
        self.assertNotIn("fetch(", capture_source)
        self.assertNotIn("XMLHttpRequest", capture_source)
        self.assertNotIn("WebSocket", capture_source)

    def test_materialize_media_requires_explicit_remote_acknowledgement(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            output_path = Path(temporary) / "archive.json"
            stderr = io.StringIO()
            with redirect_stderr(stderr):
                result = cli_main([
                    "materialize-media",
                    "--input",
                    str(self.fixture),
                    "--output",
                    str(output_path),
                ])
            self.assertEqual(result, 2)
            self.assertIn("requires --allow-remote", stderr.getvalue())

    def test_merge_transcripts_deduplicates_overlaps_and_verifies_boundaries(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            participant = {"id": "user-1", "display_name": "User 1", "username": "user-1"}

            def capture(name: str, messages: list[dict[str, str]], at_start: bool, at_end: bool) -> Path:
                path = root / name
                ordered = sorted(messages, key=lambda item: item["timestamp"])
                path.write_text(
                    json.dumps(
                        {
                            "metadata": {
                                "kind": "direct_message",
                                "title": "User 1",
                                "channel_id": "channel-1",
                                "capture_range": {
                                    "version": 1,
                                    "message_count": len(messages),
                                    "oldest_message_id": ordered[0]["id"],
                                    "oldest_timestamp": ordered[0]["timestamp"],
                                    "newest_message_id": ordered[-1]["id"],
                                    "newest_timestamp": ordered[-1]["timestamp"],
                                    "at_start": at_start,
                                    "at_end": at_end,
                                },
                            },
                            "participants": [participant],
                            "messages": messages,
                        }
                    ),
                    encoding="utf-8",
                )
                return path

            first = capture(
                "range-001.json",
                [
                    {"id": "m1", "author_id": "user-1", "timestamp": "2024-01-01T00:00:00Z", "content": "one"},
                    {"id": "m2", "author_id": "user-1", "timestamp": "2024-01-01T00:01:00Z", "content": "two"},
                ],
                True,
                False,
            )
            second = capture(
                "range-002.json",
                [
                    {"id": "m2", "author_id": "user-1", "timestamp": "2024-01-01T00:01:00Z", "content": "two"},
                    {"id": "m3", "author_id": "user-1", "timestamp": "2024-01-01T00:02:00Z", "content": "three"},
                ],
                False,
                True,
            )
            merged_path = root / "merged.json"
            summary = merge_transcripts([first, second], merged_path)
            self.assertEqual(summary["messages"], 3)
            self.assertEqual(summary["duplicates"], 1)
            self.assertEqual(summary["coverage"]["status"], "verified")
            self.assertTrue(summary["coverage"]["complete"])
            self.assertIn("No further capture step", summary["coverage"]["next_action"])
            self.assertEqual([message["id"] for message in load_json(merged_path)["messages"]], ["m1", "m2", "m3"])
            self.assertEqual(verify_transcript_coverage(merged_path)["status"], "verified")

            archive = import_transcript(merged_path, root / "archive.json")
            self.assertEqual(validate_archive(archive), [])
            self.assertEqual(archive["metadata"]["coverage"]["range_count"], 2)

    def test_partial_coverage_report_explains_next_action(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            capture = root / "partial.json"
            capture.write_text(
                json.dumps(
                    {
                        "metadata": {
                            "capture_range": {
                                "version": 1,
                                "message_count": 1,
                                "oldest_message_id": "m1",
                                "oldest_timestamp": "2024-01-01T00:00:00Z",
                                "newest_message_id": "m1",
                                "newest_timestamp": "2024-01-01T00:00:00Z",
                                "at_start": False,
                                "at_end": True,
                            }
                        },
                        "messages": [{"id": "m1", "author_id": "user-1", "timestamp": "2024-01-01T00:00:00Z", "content": "one"}],
                    }
                ),
                encoding="utf-8",
            )
            coverage = verify_transcript_coverage(capture)
            self.assertEqual(coverage["status"], "partial")
            self.assertIn("oldest", coverage["next_action"])


if __name__ == "__main__":
    unittest.main()
