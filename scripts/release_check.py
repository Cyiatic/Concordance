"""Dependency-free release preflight for the Concordance repository."""

from __future__ import annotations

import argparse
import json
import os
import re
import subprocess
import sys
import tempfile
import tomllib
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
SRC = ROOT / "src"
sys.path.insert(0, str(SRC))

from concordance import __version__  # noqa: E402
from concordance.core import (  # noqa: E402
    build_archive,
    load_json,
    validate_archive,
    verify_build,
)


FORBIDDEN_PATH_PARTS = {"private-data", "raw", "archives", "dist", "output"}
SECRET_PATTERNS = (
    ("GitHub token", re.compile(r"gh[pousr]_[A-Za-z0-9_]{20,}|github_pat_[A-Za-z0-9_]{20,}")),
    ("AWS access key", re.compile(r"AKIA[0-9A-Z]{16}")),
    ("OpenAI-style API key", re.compile(r"sk-[A-Za-z0-9]{20,}")),
    ("Discord token", re.compile(r"[A-Za-z0-9_-]{24}\.[A-Za-z0-9_-]{6}\.[A-Za-z0-9_-]{20,}")),
    ("private key", re.compile(r"-----BEGIN [A-Z ]*PRIVATE KEY-----")),
    ("credentialed URL", re.compile(r"https?://[^/\\s:@]+:[^@\\s/]+@")),
    ("bearer token", re.compile(r"(?i)bearer\s+[A-Za-z0-9._-]{20,}")),
    ("secret assignment", re.compile(r"(?i)(?:api[_-]?key|client[_-]?secret|access[_-]?token|refresh[_-]?token|password)\s*[:=]\s*['\"][A-Za-z0-9+/=_-]{16,}['\"]")),
)
REQUIRED_FILES = (
    "README.md",
    "CHANGELOG.md",
    "LICENSE",
    "AGENTS.md",
    "PRODUCT.md",
    "DESIGN.md",
    ".gitignore",
    "pyproject.toml",
    ".github/workflows/ci.yml",
    "src/concordance/__init__.py",
    "CONTRIBUTING.md",
    "SECURITY.md",
    "viewer/template.html",
    "viewer/catalog_template.html",
    "viewer/capture_template.html",
    "viewer/assets/concordance-mark.png",
    "plugins/concordance/.codex-plugin/plugin.json",
    "plugins/concordance/skills/concordance-archive/SKILL.md",
    "plugins/concordance/assets/concordance-mark.png",
    "scripts/release_check.py",
    "scripts/source_release_smoke.py",
    "scripts/wheel_smoke.py",
)


def _check_required_files(errors: list[str]) -> None:
    for relative in REQUIRED_FILES:
        if not (ROOT / relative).is_file():
            errors.append(f"missing required release file: {relative}")


def _check_metadata(errors: list[str]) -> None:
    try:
        project = tomllib.loads((ROOT / "pyproject.toml").read_text(encoding="utf-8"))["project"]
    except (OSError, KeyError, tomllib.TOMLDecodeError) as error:
        errors.append(f"cannot read project metadata: {error}")
        return

    version = project.get("version")
    if project.get("name") != "concordance":
        errors.append(f"project name must be concordance, got {project.get('name')!r}")
    if version != __version__:
        errors.append(f"version mismatch: pyproject={version!r}, package={__version__!r}")
    if project.get("readme") != "README.md":
        errors.append("project readme must point to README.md")
    license_value = project.get("license")
    if not isinstance(license_value, dict) or license_value.get("file") != "LICENSE":
        errors.append("project license must point to LICENSE")
    scripts = project.get("scripts")
    if not isinstance(scripts, dict) or scripts.get("concordance") != "concordance.cli:main":
        errors.append("project script must expose concordance=concordance.cli:main")

    manifest_path = ROOT / "plugins" / "concordance" / ".codex-plugin" / "plugin.json"
    try:
        manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as error:
        errors.append(f"cannot read plugin manifest: {error}")
        return
    if manifest.get("version") != version:
        errors.append(f"version mismatch: plugin={manifest.get('version')!r}, project={version!r}")
    interface = manifest.get("interface")
    if not isinstance(interface, dict) or interface.get("displayName") != "Concordance":
        errors.append("plugin interface displayName must be Concordance")
        return
    plugin_root = manifest_path.parents[1]
    for key in ("composerIcon", "logo", "logoDark"):
        value = interface.get(key)
        if not isinstance(value, str) or not value.startswith("./"):
            errors.append(f"plugin interface {key} must be a relative asset path")
            continue
        candidate = (plugin_root / value[2:]).resolve()
        if plugin_root.resolve() not in candidate.parents or not candidate.is_file():
            errors.append(f"plugin interface {key} points to a missing or unsafe asset: {value}")


def _check_repository_boundary(errors: list[str]) -> None:
    try:
        result = subprocess.run(
            ["git", "ls-files", "-z", "--cached", "--others", "--exclude-standard"],
            cwd=ROOT,
            capture_output=True,
            check=False,
        )
    except OSError as error:
        errors.append(f"cannot inspect tracked files: {error}")
        return
    if result.returncode:
        errors.append("git ls-files failed while checking the release boundary")
        return
    for raw_path in result.stdout.split(b"\0"):
        if not raw_path:
            continue
        relative = Path(os.fsdecode(raw_path))
        forbidden = {
            part.lower()
            for part in relative.parts
            if part.lower() in FORBIDDEN_PATH_PARTS
        }
        if forbidden:
            errors.append(f"forbidden private/generated path is tracked: {relative}")
            continue
        path = ROOT / relative
        try:
            content = path.read_text(encoding="utf-8")
        except (OSError, UnicodeDecodeError):
            continue
        for label, pattern in SECRET_PATTERNS:
            if pattern.search(content):
                errors.append(f"possible {label} in repository file: {relative}")
                break


def _check_synthetic_build(errors: list[str]) -> None:
    fixture = ROOT / "fixtures" / "sample" / "archive.json"
    try:
        archive = load_json(fixture)
        validation_errors = validate_archive(archive)
    except (OSError, ValueError, json.JSONDecodeError) as error:
        errors.append(f"synthetic fixture cannot be loaded: {error}")
        return
    if validation_errors:
        errors.extend(f"synthetic fixture: {error}" for error in validation_errors)
        return
    with tempfile.TemporaryDirectory(prefix="concordance-release-check-") as temporary:
        output = Path(temporary) / "viewer"
        missing = build_archive(fixture, output)
        errors.extend(f"synthetic build missing asset: {path}" for path in missing)
        errors.extend(f"synthetic viewer: {error}" for error in verify_build(output))


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--skip-git",
        action="store_true",
        help="skip the tracked-file privacy boundary check",
    )
    args = parser.parse_args()

    errors: list[str] = []
    _check_required_files(errors)
    _check_metadata(errors)
    if not args.skip_git:
        _check_repository_boundary(errors)
    _check_synthetic_build(errors)

    if errors:
        print("Release preflight: FAIL", file=sys.stderr)
        for error in errors:
            print(f"- {error}", file=sys.stderr)
        return 1
    print("Release preflight: PASS")
    print(f"Version: {__version__}")
    if args.skip_git:
        print("Repository private/generated paths: not checked")
    else:
        print("Repository private/generated paths and high-signal secrets: none")
    print("Synthetic archive build and verification: passed")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
