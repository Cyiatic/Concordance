"""Install a built wheel into a clean venv and exercise the offline CLI."""

from __future__ import annotations

import argparse
import os
import subprocess
import sys
import tempfile
import venv
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
FIXTURE = ROOT / "fixtures" / "sample" / "archive.json"


def _run(command: list[str], cwd: Path, env: dict[str, str] | None = None) -> None:
    print("$ " + " ".join(command))
    subprocess.run(command, cwd=cwd, env=env, check=True)


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--wheel-dir",
        type=Path,
        default=ROOT / "dist" / "wheel",
        help="directory containing exactly one wheel (default: dist/wheel)",
    )
    args = parser.parse_args()
    wheel_dir = args.wheel_dir.expanduser().resolve()
    wheels = sorted(wheel_dir.glob("*.whl"))
    if len(wheels) != 1:
        print(f"expected exactly one wheel in {wheel_dir}, found {len(wheels)}", file=sys.stderr)
        return 2

    with tempfile.TemporaryDirectory(prefix="concordance-wheel-smoke-") as temporary:
        temp = Path(temporary)
        venv_dir = temp / "venv"
        venv.EnvBuilder(with_pip=True, clear=True).create(venv_dir)
        python = venv_dir / ("Scripts/python.exe" if os.name == "nt" else "bin/python")
        _run([str(python), "-m", "pip", "install", "--no-index", "--no-deps", str(wheels[0])], temp)

        clean_environment = os.environ.copy()
        clean_environment.pop("PYTHONPATH", None)
        output = temp / "viewer"
        commands = (
            [str(python), "-m", "discord_archive", "validate", str(FIXTURE)],
            [str(python), "-m", "discord_archive", "build", str(FIXTURE), "--output", str(output)],
            [str(python), "-m", "discord_archive", "verify", str(output)],
        )
        for command in commands:
            _run(command, temp, clean_environment)
    print("Wheel smoke: PASS")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
