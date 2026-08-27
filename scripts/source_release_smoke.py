"""Inspect and execute the source-only Concordance release archive."""

from __future__ import annotations

import argparse
import os
import subprocess
import sys
import tempfile
import zipfile
from pathlib import Path, PurePosixPath


ROOT = Path(__file__).resolve().parents[1]
FORBIDDEN_PARTS = {"private-data", "raw", "archives", "output"}
REQUIRED_ENTRIES = {
    "README.md",
    "CHANGELOG.md",
    "LICENSE",
    "DESIGN.md",
    ".gitignore",
    ".github/workflows/ci.yml",
    "pyproject.toml",
    "scripts/release_check.py",
}


def _safe_member(name: str) -> bool:
    path = PurePosixPath(name.replace("\\", "/"))
    return not path.is_absolute() and ".." not in path.parts


def _member_parts(name: str) -> tuple[str, ...]:
    return PurePosixPath(name.replace("\\", "/")).parts


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--archive",
        type=Path,
        default=ROOT / "dist" / "concordance-release.zip",
        help="source-only ZIP to inspect (default: dist/concordance-release.zip)",
    )
    args = parser.parse_args()
    archive_path = args.archive.expanduser().resolve()
    if not archive_path.is_file():
        print(f"release archive does not exist: {archive_path}", file=sys.stderr)
        return 2

    with zipfile.ZipFile(archive_path) as archive:
        names = set(archive.namelist())
        unsafe = [name for name in names if not _safe_member(name)]
        forbidden = [
            name
            for name in names
            if any(part.lower() in FORBIDDEN_PARTS for part in _member_parts(name))
            or "__pycache__" in name.lower()
            or ".egg-info" in name.lower()
            or name.lower().endswith((".pyc", ".pyo"))
        ]
        missing = sorted(REQUIRED_ENTRIES - names)
        if unsafe or forbidden or missing:
            print("Source release smoke: FAIL", file=sys.stderr)
            for name in unsafe:
                print(f"- unsafe ZIP member: {name}", file=sys.stderr)
            for name in forbidden:
                print(f"- forbidden private/generated member: {name}", file=sys.stderr)
            for name in missing:
                print(f"- missing release member: {name}", file=sys.stderr)
            return 1

        with tempfile.TemporaryDirectory(prefix="concordance-source-release-smoke-") as temporary:
            extracted = Path(temporary) / "concordance"
            archive.extractall(extracted)
            environment = None
            if "PYTHONPATH" in os.environ:
                environment = os.environ.copy()
                environment.pop("PYTHONPATH", None)
            command = [sys.executable, "scripts/release_check.py", "--skip-git"]
            result = subprocess.run(command, cwd=extracted, env=environment, text=True)
            if result.returncode:
                return result.returncode
    print("Source release smoke: PASS")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
