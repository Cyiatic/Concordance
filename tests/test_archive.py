from __future__ import annotations

import hashlib
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
    export_evidence,
    export_bundle,
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
    _template_path,
    validate_archive,
    verify_catalog,
    verify_capture_session_dashboard,
    verify_evidence,
    verify_transcript_coverage,
    verify_build,
)
import discord_archive.core as core_module  # noqa: E402
from discord_archive.cli import main as cli_main  # noqa: E402


class ArchiveTests(unittest.TestCase):
    def setUp(self) -> None:
        self.fixture = PROJECT_ROOT / "fixtures" / "sample" / "archive.json"
        self.archive = load_json(self.fixture)

    def test_sample_archive_is_valid(self) -> None:
        self.assertEqual(validate_archive(self.archive), [])
        self.assertEqual(len(self.archive["messages"]), 5)

    def test_codex_plugin_package_is_source_only(self) -> None:
        plugin_root = PROJECT_ROOT / "plugins" / "concordance"
        manifest_path = plugin_root / ".codex-plugin" / "plugin.json"
        manifest = load_json(manifest_path)
        self.assertEqual(manifest["name"], "concordance")
        self.assertRegex(manifest["version"], r"^\d+\.\d+\.\d+$")
        self.assertEqual(manifest["skills"], "./skills/")
        self.assertEqual(manifest["interface"]["displayName"], "Concordance")
        self.assertEqual(len(manifest["interface"]["defaultPrompt"]), 3)
        self.assertTrue((plugin_root / "skills" / "concordance-archive" / "SKILL.md").is_file())
        self.assertTrue((plugin_root / "assets" / "concordance-mark.png").is_file())
        self.assertFalse(any(
            part in {"private-data", "raw", "archives", "dist", "output"}
            for path in plugin_root.rglob("*")
            for part in path.relative_to(plugin_root).parts
        ))

    def test_build_copies_viewer_data_and_assets(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            output = Path(temporary) / "sample"
            missing = build_archive(self.fixture, output)
            self.assertEqual(missing, [])
            self.assertTrue((output / "index.html").is_file())
            self.assertTrue((output / "app.js").is_file())
            self.assertTrue((output / "archive.json").is_file())
            self.assertTrue((output / "evidence.json").is_file())
            self.assertTrue((output / "manifest.json").is_file())
            self.assertTrue((output / "assets" / "concordance-mark.png").is_file())
            self.assertTrue((output / "assets" / "avatars" / "mara.svg").is_file())
            self.assertTrue((output / "assets" / "attachments" / "note.svg").is_file())
            manifest = load_json(output / "manifest.json")
            self.assertIn("evidence.json", {entry["path"] for entry in manifest["files"]})
            html = (output / "index.html").read_text(encoding="utf-8")
            self.assertIn("Concordance", html)
            self.assertIn("concordance-mark.png", html)
            self.assertIn('<link rel="icon" type="image/png" href="assets/concordance-mark.png">', html)
            self.assertIn('src="app.js"', html)
            self.assertIn("Content-Security-Policy", html)
            self.assertIn("script-src 'self'", html)
            app = (output / "app.js").read_text(encoding="utf-8")
            self.assertIn("Good one", app)
            self.assertIn("window.__ARCHIVE_DATA__", app)
            self.assertIn("window.__ARCHIVE_EVIDENCE__", app)
            self.assertIn("Open local file", app)
            self.assertIn("<audio", app)
            self.assertIn("windowSize: 240", app)
            self.assertIn('data-action="load-newer"', app)
            self.assertIn("searchTextById", app)
            self.assertIn("renderIntegrity", app)
            self.assertIn('window.addEventListener("afterprint"', app)
            self.assertIn("Remote image reference", app)
            self.assertIn("Import coverage", app)
            self.assertIn("Unreadable source files", app)
            self.assertIn("coverage-banner", html)
            self.assertIn("coverage-banner-context", html)
            self.assertIn("integrity-disclosure", html)
            self.assertIn("workspace-controls", html)
            self.assertIn("sidebar-footer", html)
            self.assertIn("sidebar-search-input", html)
            self.assertIn('placeholder="Find a message…"', html)
            self.assertIn("sidebar-participants", html)
            self.assertIn("control-group", html)
            self.assertIn('data-action="edge"', html)
            self.assertIn("Jump to Newest", html)
            self.assertIn(">Author</label>", html)
            self.assertIn(">Timezone</label>", html)
            self.assertIn("workspace-author-filter", html)
            self.assertIn("workspace-timezone-mode", html)
            self.assertNotIn("sidebar-header-button", html)
            self.assertNotIn("server-status", html)
            self.assertNotIn("sidebar-user-dot", html)
            self.assertNotIn("sidebar-userbar", html)
            self.assertNotIn("Archive reader", html)
            self.assertNotIn("transcript-participants", html)
            self.assertEqual(html.count('class="status-dot"'), 1)
            self.assertEqual(html.count('data-action="print"'), 1)
            self.assertNotIn("sidebar-local-status", html)
            self.assertIn("This is not yet the whole conversation", app)
            self.assertIn("coverage-ledger", html)
            self.assertIn("Ignore malformed user-edited deep links", app)
            self.assertIn("sidebarSearchMode", app)
            self.assertIn("archiveCatalog", app)
            self.assertIn("scrollToNewest", app)
            self.assertIn("scrollToOldest", app)
            self.assertIn("renderTranscriptNavigation", app)
            self.assertIn("Source display", app)
            self.assertIn("source_display", app)
            manifest = load_json(output / "manifest.json")
            self.assertEqual(manifest["manifest_version"], 1)
            self.assertEqual(
                {entry["path"] for entry in manifest["files"]},
                {"archive.json", "app.js", "index.html", "evidence.json", "assets/concordance-mark.png", "assets/avatars/eli.svg", "assets/avatars/mara.svg", "assets/attachments/note.svg"},
            )
            self.assertEqual(verify_build(output), [])

            (output / "app.js").write_text(app + "\n// tampered", encoding="utf-8")
            self.assertTrue(any("hash mismatch: app.js" in error for error in verify_build(output)))

            evidence_text = (output / "evidence.json").read_text(encoding="utf-8")
            (output / "evidence.json").write_text(evidence_text + "\n", encoding="utf-8")
            self.assertTrue(any("hash mismatch: evidence.json" in error for error in verify_build(output)))

    def test_build_catalog_indexes_archives_and_verifies_linked_viewers(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            source_archives = []
            for index, title in enumerate(("Mara and Eli", "Juniper and Rowan"), start=1):
                archive = deepcopy(self.archive)
                archive["metadata"]["title"] = title
                archive["metadata"].pop("coverage", None)
                archive["metadata"].pop("capture_range", None)
                for participant in archive["participants"]:
                    participant.pop("avatar_path", None)
                    participant.pop("avatar_ref", None)
                for message in archive["messages"]:
                    message["attachments"] = []
                    message["embeds"] = []
                source = root / f"source-{index}.json"
                source.write_text(json.dumps(archive), encoding="utf-8")
                source_archives.append(source)

            output = root / "catalog"
            summary = build_catalog(source_archives, output)
            self.assertEqual(summary["archives"], 2)
            self.assertEqual(summary["missing_assets"], [])
            catalog = load_json(output / "catalog.json")
            self.assertEqual(catalog["catalog_version"], 1)
            self.assertEqual(len(catalog["archives"]), 2)
            self.assertNotIn("message_index_path", catalog)
            self.assertNotIn(str(root), json.dumps(catalog))
            self.assertNotIn("Good one", json.dumps(catalog))
            self.assertTrue(all((output / entry["viewer_path"]).is_file() for entry in catalog["archives"]))
            self.assertEqual(verify_catalog(output), [])
            html = (output / "index.html").read_text(encoding="utf-8")
            app = (output / "catalog.js").read_text(encoding="utf-8")
            self.assertIn('src="catalog.js"', html)
            self.assertIn("Archive register", html)
            self.assertIn("Find a conversation", html)
            self.assertIn("window.__CONCORDANCE_CATALOG__", app)
            manifest = load_json(output / "manifest.json")
            self.assertIn("catalog.json", {entry["path"] for entry in manifest["files"]})
            self.assertTrue(any(entry["path"].startswith("archives/") for entry in manifest["files"]))

            catalog_app = (output / "catalog.js").read_text(encoding="utf-8")
            (output / "catalog.js").write_text(catalog_app + "\n// tampered", encoding="utf-8")
            self.assertTrue(any("hash mismatch: catalog.js" in error for error in verify_catalog(output)))

            indexed_output = root / "indexed-catalog"
            indexed_summary = build_catalog(source_archives, indexed_output, include_message_index=True)
            self.assertEqual(indexed_summary["archives"], 2)
            indexed_catalog = load_json(indexed_output / "catalog.json")
            self.assertEqual(indexed_catalog["message_index_path"], "message-index.json")
            self.assertEqual(indexed_catalog["message_index_count"], 10)
            self.assertIn("Good one", (indexed_output / "message-index.json").read_text(encoding="utf-8"))
            indexed_app = (indexed_output / "catalog.js").read_text(encoding="utf-8")
            self.assertIn("window.__CONCORDANCE_MESSAGE_INDEX__", indexed_app)
            self.assertIn("Message matches", (indexed_output / "index.html").read_text(encoding="utf-8"))
            self.assertEqual(verify_catalog(indexed_output), [])

    def test_safe_share_redaction_and_schema_migration(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            redacted_path = root / "redacted.json"
            redacted = redact_archive(self.fixture, redacted_path)
            self.assertEqual(validate_archive(redacted), [])
            serialized = json.dumps(redacted, ensure_ascii=False)
            self.assertIn("[redacted]", serialized)
            self.assertNotIn("Good one", serialized)
            self.assertNotIn("Mara", serialized)
            self.assertNotIn("Eli", serialized)
            self.assertNotIn("note.svg", serialized)
            self.assertEqual(redacted["metadata"]["redaction"]["profile"], "safe-share")
            redacted_viewer = root / "redacted-view"
            self.assertEqual(build_archive(redacted_path, redacted_viewer), [])
            self.assertEqual(verify_build(redacted_viewer), [])

            current_path = root / "current-copy.json"
            current_result = migrate_archive(self.fixture, current_path)
            self.assertFalse(current_result["changed"])
            self.assertEqual(current_result["from_version"], 1)
            self.assertEqual(current_result["migration_path"], [1])
            self.assertEqual(validate_archive(load_json(current_path)), [])

            legacy = deepcopy(self.archive)
            legacy.pop("schema_version")
            legacy_path = root / "legacy.json"
            legacy_path.write_text(json.dumps(legacy), encoding="utf-8")
            migrated_path = root / "migrated.json"
            migrated_result = migrate_archive(legacy_path, migrated_path)
            self.assertTrue(migrated_result["changed"])
            self.assertEqual(migrated_result["from_version"], 0)
            self.assertEqual(migrated_result["to_version"], 1)
            self.assertEqual(migrated_result["migration_path"], [0, 1])
            self.assertEqual(validate_archive(load_json(migrated_path)), [])

            future_path = root / "future.json"
            future = deepcopy(self.archive)
            future["schema_version"] = 2
            future_path.write_text(json.dumps(future), encoding="utf-8")
            with self.assertRaisesRegex(ValueError, "No migration path exists"):
                migrate_archive(future_path, root / "future-migrated.json")

            malformed_path = root / "malformed.json"
            malformed = deepcopy(self.archive)
            malformed["schema_version"] = "1"
            malformed_path.write_text(json.dumps(malformed), encoding="utf-8")
            with self.assertRaisesRegex(ValueError, "schema_version must be an integer"):
                migrate_archive(malformed_path, root / "malformed-migrated.json")

    def test_portable_and_encrypted_bundles_round_trip(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            viewer = root / "viewer"
            build_archive(self.fixture, viewer)

            bundle_path = root / "viewer.zip"
            exported = export_bundle(viewer, bundle_path)
            self.assertGreater(exported["bytes"], 0)
            restored = root / "restored"
            imported = import_bundle(bundle_path, restored)
            self.assertTrue(imported["verified"])
            self.assertEqual(verify_build(restored), [])

            encrypted_path = root / "viewer.concordance.enc"
            try:
                encrypted = encrypt_bundle(viewer, encrypted_path, "test passphrase")
            except RuntimeError as error:
                self.skipTest(str(error))
            self.assertGreater(encrypted["bytes"], 0)
            decrypted = root / "decrypted"
            decrypted_summary = decrypt_bundle(encrypted_path, decrypted, "test passphrase")
            self.assertTrue(decrypted_summary["verified"])
            self.assertEqual(verify_build(decrypted), [])
            with self.assertRaisesRegex(ValueError, "password may be wrong"):
                decrypt_bundle(encrypted_path, root / "wrong-password", "wrong passphrase")

    def test_evidence_report_tracks_integrity_without_message_payloads(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            viewer = root / "viewer"
            build_archive(self.fixture, viewer)
            archive_path = viewer / "archive.json"
            evidence_path = viewer / "evidence.json"

            evidence = export_evidence(archive_path, evidence_path)
            self.assertEqual(evidence["evidence_version"], 1)
            self.assertEqual(evidence["archive"]["path"], "archive.json")
            self.assertEqual(evidence["archive"]["message_count"], 5)
            self.assertEqual(evidence["coverage"]["status"], "unverified")
            self.assertEqual(
                {asset["path"] for asset in evidence["local_assets"]},
                {
                    "assets/attachments/note.svg",
                    "assets/avatars/eli.svg",
                    "assets/avatars/mara.svg",
                },
            )
            serialized = json.dumps(evidence)
            self.assertNotIn(str(root), serialized)
            self.assertNotIn("Good one", serialized)
            self.assertEqual(verify_evidence(evidence_path), [])

            dom_snapshot = root / "rendered.html"
            dom_snapshot.write_text("<main data-capture='visible'>message snapshot</main>", encoding="utf-8")
            screenshot = root / "rendered.png"
            screenshot.write_bytes(b"synthetic screenshot bytes")
            rendered_evidence_path = viewer / "rendered-evidence.json"
            rendered = export_evidence(
                archive_path,
                rendered_evidence_path,
                dom_paths=[dom_snapshot],
                screenshot_paths=[screenshot],
            )
            self.assertEqual({item["kind"] for item in rendered["rendered_evidence"]}, {"dom", "screenshots"})
            self.assertNotIn(str(root), json.dumps(rendered))
            self.assertEqual(verify_evidence(rendered_evidence_path), [])
            screenshot_copy = viewer / rendered["rendered_evidence"][1]["path"]
            screenshot_copy.write_bytes(b"tampered")
            self.assertTrue(any("rendered_evidence" in error for error in verify_evidence(rendered_evidence_path)))

            session_path = viewer / "capture-session.json"
            init_capture_session(session_path, channel_id="synthetic-dm-001", title="Mara ↔ Eli")
            session_data = load_json(session_path)
            session_data["finalized_archive"] = "merged-transcript.json"
            session_path.write_text(json.dumps(session_data), encoding="utf-8")
            linked_path = viewer / "linked-evidence.json"
            linked = export_evidence(archive_path, linked_path, session_path=session_path)
            self.assertEqual(linked["capture_session"]["capture_count"], 0)
            self.assertFalse(linked["capture_session"]["archive_match"])
            self.assertEqual(verify_evidence(linked_path), [])

            asset_path = viewer / "assets" / "avatars" / "mara.svg"
            asset_path.write_text(asset_path.read_text(encoding="utf-8") + "\n", encoding="utf-8")
            self.assertTrue(any("local_assets" in error and "mara.svg" in error for error in verify_evidence(evidence_path)))

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
                                "source_display": {
                                    "label": "Thursday, January 2, 2020 at 8:05 PM",
                                    "date": "Thursday, January 2, 2020",
                                    "time": "8:05 PM",
                                },
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
            self.assertEqual(archive["messages"][1]["source_display"]["time"], "8:05 PM")
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
        self.assertIn("requestedDirection", capture_source)
        self.assertIn("previousScrollTop", capture_source)
        self.assertIn("read-only capture", capture_source)
        self.assertIn("source_display", capture_source)
        self.assertIn("hiddenVisually", capture_source)
        self.assertIn("custom_emojis", capture_source)
        self.assertIn("message.stickers", capture_source)
        self.assertIn('data-type="sticker"', capture_source)
        self.assertIn("data-format-type", capture_source)
        self.assertIn("stickerReferenceUrl", capture_source)
        self.assertIn("Discord image-only link previews", capture_source)
        self.assertIn("querySelectorAll('[class*=\"embed\"]')", capture_source)
        self.assertIn("outermostEmbedRoot", capture_source)
        self.assertIn("callFromContent", capture_source)
        self.assertIn("duration_label", capture_source)
        self.assertIn("readVisibleProfile", capture_source)
        self.assertIn("visible_profile_card", capture_source)
        self.assertIn("mutual_friends", capture_source)
        self.assertIn("banner_ref", capture_source)
        self.assertIn("avatar_decoration_ref", capture_source)
        self.assertIn("custom_status", capture_source)
        self.assertIn("capture_diagnostics", capture_source)
        self.assertIn("remote_media_hosts", capture_source)
        self.assertIn("youtubeThumbnailUrl", capture_source)
        self.assertNotIn("document.cookie", capture_source)
        self.assertNotIn("localStorage", capture_source)
        self.assertNotIn("fetch(", capture_source)
        self.assertNotIn("XMLHttpRequest", capture_source)
        self.assertNotIn("WebSocket", capture_source)

    def test_call_metadata_is_normalised_and_retained_for_offline_rendering(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            input_path = root / "calls.json"
            input_path.write_text(
                json.dumps([
                    {
                        "id": "call-1",
                        "timestamp": "2026-02-22T18:00:00Z",
                        "author": "Test Sender",
                        "content": "Test Sender started a call that lasted an hour.",
                        "call": {
                            "type": "voice",
                            "status": "completed",
                            "initiator_name": "Test Sender",
                            "duration_label": "an hour",
                        },
                    },
                    {
                        "id": "call-2",
                        "timestamp": "2026-02-22T19:00:00Z",
                        "author": "Archive Owner",
                        "content": "You missed a call from Test Sender that lasted 3 minutes.",
                        "call": {
                            "type": "voice",
                            "status": "missed",
                            "initiator_name": "Test Sender",
                            "duration_label": "3 minutes",
                        },
                    },
                ]),
                encoding="utf-8",
            )
            archive = import_transcript(input_path, root / "archive.json")
            self.assertEqual(validate_archive(archive), [])
            self.assertEqual(archive["messages"][0]["call"]["duration_label"], "an hour")
            self.assertEqual(archive["messages"][1]["call"]["status"], "missed")
            redacted_path = root / "redacted.json"
            redacted = redact_archive(root / "archive.json", redacted_path)
            self.assertEqual(validate_archive(redacted), [])
        self.assertEqual(redacted["messages"][1]["call"]["duration_label"], "3 minutes")
        self.assertNotIn("Test Sender", json.dumps(redacted["messages"][1]["call"]))

    def test_visible_profile_metadata_is_normalised_and_retained(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            input_path = root / "profile.json"
            input_path.write_text(
                json.dumps({
                    "metadata": {
                        "title": "Profile Test User",
                        "captured_at": "2026-08-23T12:00:00Z",
                    },
                    "participants": [{
                        "id": "profile-user-001",
                        "display_name": "Profile Test User",
                        "username": "profile_test_user",
                        "avatar_ref": "https://cdn.discordapp.com/avatars/profile-user-001/avatar.webp?size=80",
                        "profile": {
                            "presence": "Offline",
                            "pronouns": "they/them",
                            "member_since": "Nov 21, 2025",
                            "banner_ref": "https://cdn.discordapp.com/banners/profile-user-001/banner.png",
                            "avatar_decoration_ref": "https://cdn.discordapp.com/avatar-decoration-presets/decoration.png",
                            "custom_status": "Building a local archive",
                            "activities": ["Playing a permitted test session"],
                            "captured_at": "2026-08-23T12:00:00Z",
                            "source": "visible_profile_card",
                            "badges": [{
                                "label": "Game Time",
                                "detail": "Committed. 147 hours of games played",
                                "icon_ref": "https://cdn.discordapp.com/badge-icons/game-time.png",
                            }],
                            "mutual_friends": [{
                                "id": "profile-friend-001",
                                "display_name": "Archive Owner",
                                "avatar_ref": "https://cdn.discordapp.com/avatars/profile-friend-001/avatar.webp?size=16",
                            }],
                        },
                    }],
                    "messages": [{
                        "id": "profile-message-1",
                        "author_id": "profile-user-001",
                        "timestamp": "2026-08-23T12:00:00Z",
                        "content": "Profile metadata test",
                    }],
                }),
                encoding="utf-8",
            )
            archive = import_transcript(input_path, root / "archive.json")
            self.assertEqual(validate_archive(archive), [])
            profile = archive["participants"][0]["profile"]
            self.assertEqual(profile["presence"], "Offline")
            self.assertEqual(profile["pronouns"], "they/them")
            self.assertEqual(profile["badges"][0]["detail"], "Committed. 147 hours of games played")
            self.assertEqual(profile["mutual_friends"][0]["display_name"], "Archive Owner")
            self.assertEqual(profile["banner_ref"], "https://cdn.discordapp.com/banners/profile-user-001/banner.png")
            self.assertEqual(profile["custom_status"], "Building a local archive")
            self.assertEqual(profile["activities"], ["Playing a permitted test session"])

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

    def test_materialize_media_profile_only_copies_avatars_without_message_media(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            source = root / "source.json"
            archive = deepcopy(self.archive)
            for index, participant in enumerate(archive["participants"], start=1):
                participant["avatar_path"] = None
                participant["avatar_ref"] = f"https://cdn.discordapp.com/avatars/user-{index}/avatar.webp?size=80"
            for item in archive["messages"]:
                item["attachments"] = []
            archive["messages"][0]["attachments"] = [{
                "name": "private-note.txt",
                "url": "https://cdn.discordapp.com/attachments/1/2/private-note.txt",
                "mime": "text/plain",
            }]
            self.assertEqual(validate_archive(archive), [])
            source.write_text(json.dumps(archive), encoding="utf-8")
            output = root / "profile-only.json"
            original_download = core_module._download_remote_media

            def fake_download(url: str, destination: Path, max_bytes: int = 0) -> int:
                destination.parent.mkdir(parents=True, exist_ok=True)
                payload = f"offline fixture for {url}".encode("utf-8")
                destination.write_bytes(payload)
                return len(payload)

            core_module._download_remote_media = fake_download
            try:
                summary = materialize_remote_media(source, output, profile_only=True)
            finally:
                core_module._download_remote_media = original_download
            self.assertEqual(summary["downloaded"], 2)
            materialized = load_json(output)
            self.assertTrue(all(participant.get("avatar_path", "").startswith("assets/avatars/") for participant in materialized["participants"]))
            self.assertNotIn("path", materialized["messages"][0]["attachments"][0])
            self.assertEqual(build_archive(output, root / "profile-only-view"), [])

    def test_materialize_media_copies_non_image_attachments(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            source = root / "source.json"
            archive = deepcopy(self.archive)
            archive["messages"][0]["attachments"] = [
                {
                    "name": "voice.mp3",
                    "url": "https://cdn.discordapp.com/attachments/1/2/voice.mp3",
                    "mime": "audio/mpeg",
                },
                {
                    "name": "notes.pdf",
                    "url": "https://cdn.discordapp.com/attachments/1/3/notes.pdf",
                    "mime": "application/pdf",
                },
            ]
            source.write_text(json.dumps(archive), encoding="utf-8")
            output = root / "materialized.json"
            original_download = core_module._download_remote_media

            def fake_download(url: str, destination: Path, max_bytes: int = 0) -> int:
                destination.parent.mkdir(parents=True, exist_ok=True)
                payload = f"offline fixture for {url}".encode("utf-8")
                destination.write_bytes(payload)
                return len(payload)

            core_module._download_remote_media = fake_download
            try:
                from discord_archive.core import materialize_remote_media

                summary = materialize_remote_media(source, output)
            finally:
                core_module._download_remote_media = original_download
            self.assertEqual(summary["downloaded"], 2)
            materialized = load_json(output)
            paths = [attachment["path"] for attachment in materialized["messages"][0]["attachments"]]
            self.assertTrue(all(path.startswith("assets/attachments/") for path in paths))
            self.assertTrue(all((root / path).is_file() for path in paths))

    def test_materialize_media_copies_stickers_custom_emoji_and_embed_media(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            source = root / "source.json"
            archive = deepcopy(self.archive)
            for participant in archive["participants"]:
                participant["avatar_path"] = None
            for item in archive["messages"]:
                item["attachments"] = []
                item["embeds"] = []
            message = archive["messages"][0]
            message["stickers"] = [{
                "id": "sticker-1",
                "name": "wave",
                "url": "https://cdn.discordapp.com/stickers/1/wave.png",
                "mime": "image/png",
            }]
            message["custom_emojis"] = [{
                "id": "emoji-1",
                "name": "spark",
                "url": "https://cdn.discordapp.com/emojis/2/spark.gif",
                "mime": "image/gif",
                "animated": True,
            }]
            message["embeds"] = [{
                "title": "rich media",
                "image_url": "https://images-ext-1.discordapp.net/external/discord-proxy/https/example.invalid/dragon.png?format=webp",
                "thumbnail_url": "https://cdn.discordapp.com/embed/3/thumb.png",
                "video_url": "https://cdn.discordapp.com/embed/3/video.mp4",
                "audio_url": "https://cdn.discordapp.com/embed/3/audio.mp3",
            }]
            self.assertEqual(validate_archive(archive), [])
            source.write_text(json.dumps(archive), encoding="utf-8")
            output = root / "materialized.json"
            original_download = core_module._download_remote_media

            def fake_download(url: str, destination: Path, max_bytes: int = 0) -> int:
                destination.parent.mkdir(parents=True, exist_ok=True)
                payload = f"offline fixture for {url}".encode("utf-8")
                destination.write_bytes(payload)
                return len(payload)

            core_module._download_remote_media = fake_download
            try:
                summary = materialize_remote_media(source, output)
            finally:
                core_module._download_remote_media = original_download
            self.assertEqual(summary["downloaded"], 6)
            materialized = load_json(output)
            materialized_message = materialized["messages"][0]
            self.assertTrue(materialized_message["stickers"][0]["path"].startswith("assets/stickers/"))
            self.assertTrue(materialized_message["custom_emojis"][0]["path"].startswith("assets/emojis/"))
            self.assertTrue(materialized_message["embeds"][0]["image_path"].startswith("assets/embeds/"))
            self.assertTrue(materialized_message["embeds"][0]["thumbnail_path"].startswith("assets/embeds/"))
            self.assertTrue(materialized_message["embeds"][0]["video_path"].startswith("assets/embeds/"))
            self.assertTrue(materialized_message["embeds"][0]["audio_path"].startswith("assets/embeds/"))
            self.assertEqual(build_archive(output, root / "media-view"), [])

    def test_build_copies_rendered_sticker_preview(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            source = root / "source.json"
            archive = deepcopy(self.archive)
            for participant in archive["participants"]:
                participant["avatar_path"] = None
            for item in archive["messages"]:
                item["attachments"] = []
                item["embeds"] = []
                item["stickers"] = []
                item["custom_emojis"] = []
            archive["messages"][0]["stickers"] = [{
                "id": "sticker-1",
                "name": "Wave",
                "format": "lottie",
                "animated": True,
                "mime": "application/json",
                "url": "https://cdn.discordapp.com/stickers/1.json",
                "preview_path": "assets/stickers/wave.png",
            }]
            preview = root / "assets" / "stickers" / "wave.png"
            preview.parent.mkdir(parents=True, exist_ok=True)
            preview.write_bytes(b"rendered sticker preview")
            source.write_text(json.dumps(archive), encoding="utf-8")
            self.assertEqual(validate_archive(load_json(source)), [])
            output = root / "sticker-view"
            self.assertEqual(build_archive(source, output), [])
            self.assertTrue((output / "assets" / "stickers" / "wave.png").is_file())
            built_message = load_json(output / "archive.json")["messages"][0]
            self.assertEqual(built_message["stickers"][0]["preview_path"], "assets/stickers/wave.png")

    def test_materialize_media_derives_and_copies_youtube_thumbnail(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            source = root / "source.json"
            archive = deepcopy(self.archive)
            for item in archive["messages"]:
                item["attachments"] = []
                item["embeds"] = []
            for participant in archive["participants"]:
                avatar_path = participant.get("avatar_path")
                if avatar_path:
                    avatar = root / avatar_path
                    avatar.parent.mkdir(parents=True, exist_ok=True)
                    avatar.write_text("synthetic avatar", encoding="utf-8")
            archive["messages"][0]["embeds"] = [{
                "title": "A captured YouTube video",
                "url": "https://youtu.be/8UsYSf8C3D4?si=fixture",
            }]
            self.assertEqual(validate_archive(archive), [])
            source.write_text(json.dumps(archive), encoding="utf-8")
            output = root / "materialized.json"
            original_download = core_module._download_remote_media
            downloaded_urls: list[str] = []

            def fake_download(url: str, destination: Path, max_bytes: int = 0) -> int:
                downloaded_urls.append(url)
                destination.parent.mkdir(parents=True, exist_ok=True)
                payload = f"offline fixture for {url}".encode("utf-8")
                destination.write_bytes(payload)
                return len(payload)

            core_module._download_remote_media = fake_download
            try:
                summary = materialize_remote_media(source, output)
            finally:
                core_module._download_remote_media = original_download
            self.assertEqual(summary["downloaded"], 1)
            self.assertEqual(downloaded_urls, ["https://i.ytimg.com/vi/8UsYSf8C3D4/hqdefault.jpg"])
            materialized = load_json(output)
            embed = materialized["messages"][0]["embeds"][0]
            self.assertEqual(embed["thumbnail_source"], "derived_youtube_thumbnail")
            self.assertTrue(embed["thumbnail_path"].startswith("assets/embeds/"))
            self.assertEqual(build_archive(output, root / "youtube-view"), [])

    def test_materialize_media_derives_youtube_thumbnail_from_message_content(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            source = root / "source.json"
            archive = deepcopy(self.archive)
            for item in archive["messages"]:
                item["attachments"] = []
                item["embeds"] = []
            for participant in archive["participants"]:
                participant["avatar_path"] = None
            message = archive["messages"][0]
            message["content"] = "https://www.youtube.com/watch?v=HeFhAK-MDs4"
            message["embeds"] = [{
                "title": "A captured YouTube preview",
                "site_name": "YouTube",
                "url": "https://www.youtube.com/",
            }]
            source.write_text(json.dumps(archive), encoding="utf-8")
            output = root / "materialized.json"
            original_download = core_module._download_remote_media
            downloaded_urls: list[str] = []

            def fake_download(url: str, destination: Path, max_bytes: int = 0) -> int:
                downloaded_urls.append(url)
                destination.parent.mkdir(parents=True, exist_ok=True)
                payload = f"offline fixture for {url}".encode("utf-8")
                destination.write_bytes(payload)
                return len(payload)

            core_module._download_remote_media = fake_download
            try:
                summary = materialize_remote_media(source, output)
            finally:
                core_module._download_remote_media = original_download
            self.assertEqual(summary["downloaded"], 1)
            self.assertEqual(downloaded_urls, ["https://i.ytimg.com/vi/HeFhAK-MDs4/hqdefault.jpg"])
            materialized = load_json(output)
            embed = materialized["messages"][0]["embeds"][0]
            self.assertEqual(embed["thumbnail_source"], "derived_youtube_thumbnail")
            self.assertTrue(embed["thumbnail_path"].startswith("assets/embeds/"))

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
                    {
                        "id": "m2",
                        "author_id": "user-1",
                        "timestamp": "2024-01-01T00:01:00Z",
                        "content": "two",
                        "attachments": [{"name": "image.png", "mime": "image/png", "url": "https://cdn.discordapp.com/attachments/1/2/image.png?old-signature"}],
                    },
                ],
                True,
                False,
            )
            repeat = capture(
                "range-001-repeat.json",
                [
                    {"id": "m1", "author_id": "user-1", "timestamp": "2024-01-01T00:00:00Z", "content": "one"},
                    {
                        "id": "m2",
                        "author_id": "user-1",
                        "timestamp": "2024-01-01T00:01:00Z",
                        "content": "two",
                        "attachments": [{"name": "image.png", "mime": "image/png", "url": "https://cdn.discordapp.com/attachments/1/2/image.png?old-signature"}],
                    },
                ],
                False,
                False,
            )
            second = capture(
                "range-002.json",
                [
                    {
                        "id": "m2",
                        "author_id": "user-1",
                        "timestamp": "2024-01-01T00:01:00Z",
                        "content": "two",
                        "source_display": {"label": "Monday, January 1, 2024 at 5:01 PM", "time": "5:01 PM"},
                        "attachments": [{"name": "image.png", "mime": "image/png", "url": "https://cdn.discordapp.com/attachments/1/2/image.png?new-signature"}],
                    },
                    {"id": "m3", "author_id": "user-1", "timestamp": "2024-01-01T00:02:00Z", "content": "three"},
                ],
                False,
                True,
            )
            merged_path = root / "merged.json"
            summary = merge_transcripts([first, repeat, second], merged_path)
            self.assertEqual(summary["messages"], 3)
            self.assertEqual(summary["duplicates"], 3)
            self.assertEqual(summary["conflicts"], 0)
            self.assertEqual(summary["coverage"]["status"], "verified")
            self.assertTrue(summary["coverage"]["complete"])
            self.assertEqual(summary["coverage"]["expected_dates"], [])
            self.assertEqual(summary["coverage"]["missing_expected_dates"], [])
            self.assertIn("No further capture step", summary["coverage"]["next_action"])
            merged_messages = load_json(merged_path)["messages"]
            self.assertEqual([message["id"] for message in merged_messages], ["m1", "m2", "m3"])
            self.assertEqual(merged_messages[1]["source_display"]["time"], "5:01 PM")
            self.assertIn("new-signature", merged_messages[1]["attachments"][0]["url"])
            self.assertEqual(verify_transcript_coverage(merged_path)["status"], "verified")

            archive = import_transcript(merged_path, root / "archive.json")
            self.assertEqual(validate_archive(archive), [])
            self.assertEqual(archive["metadata"]["coverage"]["range_count"], 3)

    def test_merge_accepts_lazy_embed_enrichment_in_overlapping_ranges(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)

            def capture(name: str, message: dict[str, object], at_start: bool, at_end: bool) -> Path:
                path = root / name
                path.write_text(
                    json.dumps(
                        {
                            "metadata": {
                                "kind": "direct_message",
                                "title": "User 1",
                                "channel_id": "channel-1",
                                "capture_range": {
                                    "version": 1,
                                    "message_count": 1,
                                    "oldest_message_id": "m1",
                                    "oldest_timestamp": "2024-01-01T00:00:00Z",
                                    "newest_message_id": "m1",
                                    "newest_timestamp": "2024-01-01T00:00:00Z",
                                    "at_start": at_start,
                                    "at_end": at_end,
                                },
                            },
                            "participants": [{"id": "user-1", "display_name": "User 1", "username": "user-1"}],
                            "messages": [message],
                        }
                    ),
                    encoding="utf-8",
                )
                return path

            first = capture(
                "range-001.json",
                {
                    "id": "m1",
                    "author_id": "user-1",
                    "timestamp": "2024-01-01T00:00:00Z",
                    "content": "https://www.youtube.com/watch?v=example",
                    "grouped": False,
                    "embeds": [{"site_name": "YouTube", "title": "Example", "url": "https://www.youtube.com/watch?v=example"}],
                },
                True,
                False,
            )
            second = capture(
                "range-002.json",
                {
                    "id": "m1",
                    "author_id": "user-1",
                    "timestamp": "2024-01-01T00:00:00Z",
                    "content": "https://www.youtube.com/watch?v=example",
                    "grouped": True,
                    "embeds": [{
                        "site_name": "YouTube",
                        "title": "Example",
                        "url": "https://www.youtube.com/watch?v=example",
                        "image_url": "https://cdn.discordapp.com/embed/example.png?signature=refreshed",
                    }],
                },
                False,
                True,
            )

            merged_path = root / "merged.json"
            summary = merge_transcripts([first, second], merged_path)
            self.assertEqual(summary["messages"], 1)
            self.assertEqual(summary["duplicates"], 1)
            self.assertEqual(summary["conflicts"], 0)
            self.assertTrue(summary["coverage"]["complete"])
            merged_message = load_json(merged_path)["messages"][0]
            self.assertFalse(merged_message["grouped"])
            self.assertIn("image_url", merged_message["embeds"][0])

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

    def test_expected_date_checkpoints_block_false_complete_history(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)

            def write_capture(name: str, messages: list[dict[str, str]], at_start: bool, at_end: bool) -> Path:
                path = root / name
                ordered = sorted(messages, key=lambda item: item["timestamp"])
                path.write_text(
                    json.dumps(
                        {
                            "metadata": {
                                "kind": "direct_message",
                                "title": "Checkpoint test",
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
                            "messages": messages,
                        }
                    ),
                    encoding="utf-8",
                )
                return path

            boundary_capture = write_capture(
                "boundary.json",
                [
                    {"id": "m1", "author_id": "user-1", "timestamp": "2024-01-15T12:00:00Z", "content": "oldest"},
                    {"id": "m4", "author_id": "user-1", "timestamp": "2024-08-20T12:00:00Z", "content": "newest"},
                ],
                True,
                True,
            )
            merged_path = root / "merged.json"
            summary = merge_transcripts(
                [boundary_capture],
                merged_path,
                expected_dates=["2024-01-15", "2024-07-01"],
            )
            coverage = summary["coverage"]
            self.assertEqual(coverage["status"], "partial")
            self.assertFalse(coverage["complete"])
            self.assertEqual(coverage["missing_expected_dates"], ["2024-07-01"])
            self.assertEqual(coverage["checkpoints"], [
                {"date": "2024-01-15", "observed": True, "range_count": 1},
                {"date": "2024-07-01", "observed": False, "range_count": 0},
            ])
            self.assertIn("2024-07-01", coverage["next_action"])

            checkpoint_capture = write_capture(
                "checkpoint.json",
                [
                    {"id": "m1", "author_id": "user-1", "timestamp": "2024-01-15T12:00:00Z", "content": "oldest"},
                    {
                        "id": "m3",
                        "author_id": "user-1",
                        "timestamp": "2024-07-02T02:00:00Z",
                        "content": "checkpoint",
                        "source_display": {"date": "Monday, July 1, 2024", "time": "7:00 PM"},
                    },
                ],
                False,
                False,
            )
            complete = merge_transcripts(
                [boundary_capture, checkpoint_capture],
                merged_path,
                expected_dates=["2024-01-15", "2024-07-01"],
            )
            self.assertEqual(complete["coverage"]["status"], "verified")
            self.assertTrue(complete["coverage"]["complete"])
            self.assertEqual(complete["coverage"]["missing_expected_dates"], [])

    def test_capture_session_tracks_hashes_and_finalizes_coverage(self) -> None:
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
            wrong_channel = capture(
                "wrong-channel.json",
                [
                    {"id": "m4", "author_id": "user-1", "timestamp": "2024-01-01T00:03:00Z", "content": "wrong DM"},
                ],
                False,
                False,
            )
            wrong_channel_data = load_json(wrong_channel)
            wrong_channel_data["metadata"]["channel_id"] = "channel-2"
            wrong_channel.write_text(json.dumps(wrong_channel_data), encoding="utf-8")

            session_path = root / "session.json"
            session = init_capture_session(session_path, expected_dates=["2024-01-01"])
            self.assertEqual(session["captures"], [])
            self.assertEqual(session["expected_dates"], ["2024-01-01"])

            added = add_capture_to_session(session_path, first)
            self.assertEqual(added["coverage"]["status"], "partial")
            self.assertIn("newest", added["coverage"]["next_action"])
            self.assertEqual(added["coverage"]["missing_expected_dates"], [])

            snapshot = capture_session_status(session_path)
            self.assertEqual(snapshot["capture_count"], 1)
            self.assertEqual(snapshot["captures"][0]["path"], "range-001.json")
            self.assertEqual(len(snapshot["captures"][0]["sha256"]), 64)

            add_capture_to_session(session_path, second)
            first.write_text(first.read_text(encoding="utf-8") + "\n", encoding="utf-8")
            with self.assertRaisesRegex(ValueError, "changed after it was added"):
                capture_session_status(session_path)
            first.write_text(first.read_text(encoding="utf-8").rstrip() + "\n", encoding="utf-8")
            session_data = load_json(session_path)
            session_data["captures"][0]["sha256"] = hashlib.sha256(first.read_bytes()).hexdigest()
            session_path.write_text(json.dumps(session_data), encoding="utf-8")
            with self.assertRaisesRegex(ValueError, "does not match session channel ID"):
                add_capture_to_session(session_path, wrong_channel)

            output = root / "merged.json"
            finalized = finalize_capture_session(session_path, output)
            self.assertEqual(finalized["coverage"]["status"], "verified")
            self.assertTrue(finalized["coverage"]["complete"])
            self.assertEqual(load_json(output)["metadata"]["coverage"]["status"], "verified")
            session_data = load_json(session_path)
            self.assertEqual(session_data["finalized_archive"], "merged.json")
            self.assertNotIn(str(root), json.dumps(session_data))

            updated = set_capture_session_checkpoints(session_path, ["2024-02-01"])
            self.assertEqual(updated["expected_dates"], ["2024-01-01", "2024-02-01"])
            self.assertEqual(updated["status"], "partial")
            self.assertEqual(updated["coverage"]["missing_expected_dates"], ["2024-02-01"])

    def test_date_free_session_next_plan_walks_from_newest_to_oldest(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)

            def write_capture(name: str, messages: list[dict[str, str]], at_start: bool, at_end: bool, scroll_top: int) -> Path:
                path = root / name
                ordered = sorted(messages, key=lambda item: item["timestamp"])
                path.write_text(
                    json.dumps(
                        {
                            "metadata": {
                                "kind": "direct_message",
                                "title": "Fresh chat",
                                "channel_id": "fresh-channel",
                                "capture_range": {
                                    "version": 1,
                                    "message_count": len(messages),
                                    "oldest_message_id": ordered[0]["id"],
                                    "oldest_timestamp": ordered[0]["timestamp"],
                                    "newest_message_id": ordered[-1]["id"],
                                    "newest_timestamp": ordered[-1]["timestamp"],
                                    "scroll_top": scroll_top,
                                    "scroll_height": 6000,
                                    "viewport_height": 900,
                                    "at_start": at_start,
                                    "at_end": at_end,
                                },
                            },
                            "participants": [{"id": "u1", "display_name": "User 1"}],
                            "messages": messages,
                        }
                    ),
                    encoding="utf-8",
                )
                return path

            newest = write_capture(
                "newest.json",
                [
                    {"id": "m2", "author_id": "u1", "timestamp": "2025-02-15T17:48:00Z", "content": "overlap"},
                    {"id": "m3", "author_id": "u1", "timestamp": "2025-02-15T17:49:00Z", "content": "latest"},
                ],
                False,
                True,
                5100,
            )
            oldest = write_capture(
                "oldest.json",
                [
                    {"id": "m1", "author_id": "u1", "timestamp": "2025-02-15T17:47:00Z", "content": "oldest"},
                    {"id": "m2", "author_id": "u1", "timestamp": "2025-02-15T17:48:00Z", "content": "overlap"},
                ],
                True,
                False,
                0,
            )
            session_path = root / "fresh-session.json"
            init_capture_session(session_path, title="Fresh chat")
            add_capture_to_session(session_path, newest)
            next_step = capture_session_next(session_path)
            self.assertEqual(next_step["next_step"]["kind"], "capture_older")
            self.assertEqual(next_step["next_step"]["direction"], "older")
            self.assertEqual(next_step["next_step"]["adapter_options"]["previous_scroll_top"], 5100)
            self.assertNotIn("newest", json.dumps(next_step["next_step"]["copy_text"]))

            add_capture_to_session(session_path, oldest)
            completed = capture_session_next(session_path)
            self.assertTrue(completed["complete"])
            self.assertEqual(completed["next_step"]["kind"], "complete")

    def test_capture_dashboard_reports_media_without_message_payloads(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            capture_path = root / "range.json"
            capture_path.write_text(
                json.dumps(
                    {
                        "metadata": {
                            "kind": "direct_message",
                            "title": "Private DM",
                            "channel_id": "channel-1",
                            "capture_range": {
                                "version": 1,
                                "message_count": 1,
                                "oldest_message_id": "m1",
                                "oldest_timestamp": "2024-01-01T00:00:00Z",
                                "newest_message_id": "m1",
                                "newest_timestamp": "2024-01-01T00:00:00Z",
                                "at_start": True,
                                "at_end": True,
                            },
                        },
                        "participants": [
                            {
                                "id": "user-1",
                                "display_name": "Private User",
                                "avatar_ref": "https://cdn.discordapp.com/avatars/user-1/avatar.png",
                                "profile": {
                                    "banner_ref": "https://profile.example.invalid/banner.png",
                                },
                            }
                        ],
                        "messages": [
                            {
                                "id": "m1",
                                "author_id": "user-1",
                                "timestamp": "2024-01-01T00:00:00Z",
                                "content": "private message body must not enter dashboard",
                                "attachments": [{"name": "note.png", "url": "https://cdn.discordapp.com/note.png"}],
                                "embeds": [{"image_url": "https://external.example.invalid/preview.png"}],
                                "reactions": [{"emoji": "👍", "count": 1}],
                                "reply_to": "prior",
                                "call": {"type": "voice", "status": "completed", "duration_label": "2 minutes"},
                            }
                        ],
                    }
                ),
                encoding="utf-8",
            )
            session_path = root / "session.json"
            init_capture_session(session_path, title="Private DM")
            add_capture_to_session(session_path, capture_path)
            status = capture_session_status(session_path)
            self.assertEqual(status["media"]["attachments"], 1)
            self.assertEqual(status["media"]["embeds"], 1)
            self.assertEqual(status["media"]["calls"], 1)
            self.assertEqual(status["media"]["replies"], 1)
            self.assertEqual(status["media"]["unapproved_remote_media"], 2)
            self.assertIn("external.example.invalid", status["media"]["remote_media_hosts"])

            dashboard = root / "dashboard"
            result = build_capture_session_dashboard(session_path, dashboard)
            self.assertEqual(result["status"], "verified")
            self.assertEqual(verify_capture_session_dashboard(dashboard), [])
            dashboard_data = load_json(dashboard / "capture-session.json")
            serialized = json.dumps(dashboard_data)
            self.assertNotIn("private message body", serialized)
            self.assertEqual(dashboard_data["status"]["media"]["calls"], 1)
            self.assertIn("window.__CONCORDANCE_CAPTURE_SESSION__", (dashboard / "capture.js").read_text(encoding="utf-8"))

    def test_capture_evidence_attaches_to_range_and_exports_automatically(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            capture_path = root / "range.json"
            capture_path.write_text(
                json.dumps(
                    {
                        "metadata": {
                            "kind": "direct_message",
                            "title": "Evidence DM",
                            "channel_id": "channel-1",
                            "capture_range": {
                                "version": 1,
                                "message_count": 1,
                                "oldest_message_id": "m1",
                                "oldest_timestamp": "2024-01-01T00:00:00Z",
                                "newest_message_id": "m1",
                                "newest_timestamp": "2024-01-01T00:00:00Z",
                                "at_start": True,
                                "at_end": True,
                            },
                        },
                        "participants": [{"id": "u1", "display_name": "User 1"}],
                        "messages": [{"id": "m1", "author_id": "u1", "timestamp": "2024-01-01T00:00:00Z", "content": "visible"}],
                    }
                ),
                encoding="utf-8",
            )
            session_path = root / "session.json"
            init_capture_session(session_path)
            add_capture_to_session(session_path, capture_path)
            dom_path = root / "rendered.html"
            dom_path.write_text("<main>visible</main>", encoding="utf-8")
            screenshot_path = root / "rendered.png"
            screenshot_path.write_bytes(b"synthetic screenshot")

            attached = attach_capture_evidence(
                session_path,
                capture_path,
                dom_paths=[dom_path],
                screenshot_paths=[screenshot_path],
            )
            self.assertEqual(attached["session"]["evidence"], {"files": 2, "dom": 1, "screenshots": 1, "bytes": dom_path.stat().st_size + screenshot_path.stat().st_size})
            status = capture_session_status(session_path)
            self.assertEqual(len(status["captures"][0]["evidence"]), 2)

            archive_path = root / "archive.json"
            import_transcript(capture_path, archive_path)
            evidence_path = root / "evidence.json"
            evidence = export_evidence(archive_path, evidence_path, session_path=session_path)
            self.assertEqual({item["kind"] for item in evidence["rendered_evidence"]}, {"dom", "screenshots"})
            self.assertEqual(verify_evidence(evidence_path), [])

            attached_screenshot = next(
                item["path"]
                for item in status["captures"][0]["evidence"]
                if item.get("kind") == "screenshots"
            )
            (root / attached_screenshot).write_bytes(b"tampered")
            with self.assertRaisesRegex(ValueError, "capture evidence changed"):
                capture_session_status(session_path)

    def test_media_audit_classifies_offline_and_unresolved_references(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            archive_path = root / "archive.json"
            archive_path.write_text(
                json.dumps(
                    {
                        "schema_version": 1,
                        "metadata": {
                            "kind": "direct_message",
                            "title": "Media audit",
                            "display_timezone": "UTC",
                            "source": {"type": "user_supplied"},
                        },
                        "participants": [{"id": "u1", "display_name": "User 1"}],
                        "messages": [
                            {
                                "id": "m1",
                                "author_id": "u1",
                                "timestamp": "2024-01-01T00:00:00Z",
                                "content": "media",
                                "attachments": [
                                    {"name": "missing.png", "path": "assets/missing.png", "url": "https://cdn.discordapp.com/attachments/1/missing.png"},
                                    {"name": "remote.png", "url": "https://cdn.discordapp.com/attachments/1/remote.png"},
                                    {"name": "external.png", "url": "https://example.invalid/external.png"},
                                ],
                            }
                        ],
                    }
                ),
                encoding="utf-8",
            )
            report_path = root / "media-audit.json"
            report = audit_archive_media(archive_path, report_path)
            self.assertEqual(report["counts"]["missing_local"], 1)
            self.assertEqual(report["counts"]["downloadable"], 1)
            self.assertEqual(report["counts"]["reference_only"], 1)
            self.assertEqual(report["unresolved_count"], 3)
            self.assertNotIn("media", json.dumps(report["references"]))
            self.assertTrue(report_path.is_file())


if __name__ == "__main__":
    unittest.main()
