from __future__ import annotations

import json
import sys
import tempfile
import unittest
from pathlib import Path


PROJECT_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(PROJECT_ROOT / "src"))

from discord_archive.core import build_archive, import_data_package, load_json, validate_archive  # noqa: E402


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
            self.assertTrue((output / "assets" / "avatars" / "mara.svg").is_file())
            self.assertTrue((output / "assets" / "attachments" / "note.svg").is_file())
            html = (output / "index.html").read_text(encoding="utf-8")
            self.assertIn("Archive Ledger", html)
            self.assertIn('src="app.js"', html)
            app = (output / "app.js").read_text(encoding="utf-8")
            self.assertIn("Good one", app)
            self.assertIn("window.__ARCHIVE_DATA__", app)

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
                        }
                    ]
                ),
                encoding="utf-8",
            )
            output = Path(temporary) / "imported.json"
            archive = import_data_package(package, output)
            self.assertTrue(output.is_file())
            self.assertEqual(validate_archive(archive), [])
            self.assertEqual(archive["messages"][0]["content"], "Imported text")
            self.assertEqual(archive["messages"][0]["id"], "discord-message-1")


if __name__ == "__main__":
    unittest.main()
