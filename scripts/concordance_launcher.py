"""Small dependency-free Windows launcher for local Concordance workflows."""

from __future__ import annotations

import argparse
import os
import shutil
import subprocess
import sys
import threading
from pathlib import Path
from typing import Callable


PROJECT_ROOT = Path(__file__).resolve().parents[1]
PYTHON = sys.executable


def _environment() -> dict[str, str]:
    environment = os.environ.copy()
    environment["PYTHONPATH"] = str(PROJECT_ROOT / "src")
    return environment


def _run_cli(arguments: list[str]) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        [PYTHON, "-m", "concordance", *arguments],
        cwd=PROJECT_ROOT,
        env=_environment(),
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
        check=False,
    )


def _open_local(path: Path) -> None:
    target = path
    if target.is_dir():
        target = target / "index.html"
    if not target.is_file() or target.suffix.lower() != ".html":
        raise ValueError(f"Expected an HTML viewer or a directory containing index.html: {path}")
    if os.name == "nt":
        os.startfile(str(target))  # type: ignore[attr-defined]
        return
    opener = shutil.which("open") or shutil.which("xdg-open")
    if not opener:
        raise RuntimeError("No local file opener is available on this platform")
    subprocess.Popen([opener, str(target)], cwd=PROJECT_ROOT)


def check_environment() -> int:
    required = [
        PROJECT_ROOT / "pyproject.toml",
        PROJECT_ROOT / "src" / "concordance",
        PROJECT_ROOT / "viewer" / "template.html",
        PROJECT_ROOT / "scripts" / "package_release.ps1",
        PROJECT_ROOT / "scripts" / "build_catalog.ps1",
        PROJECT_ROOT / "scripts" / "share_archive.ps1",
    ]
    missing = [str(path.relative_to(PROJECT_ROOT)) for path in required if not path.exists()]
    if missing:
        for path in missing:
            print(f"missing: {path}", file=sys.stderr)
        return 2
    print(f"Concordance launcher ready: {PROJECT_ROOT}")
    print(f"Python: {PYTHON}")
    return 0


class Launcher:
    def __init__(self, tk, ttk, filedialog, messagebox) -> None:
        self.tk = tk
        self.ttk = ttk
        self.filedialog = filedialog
        self.messagebox = messagebox
        self.root = tk.Tk()
        self.root.title("Concordance · Offline workspace")
        self.root.geometry("720x510")
        self.root.minsize(620, 430)
        self.root.configure(bg="#313338")
        self.source = tk.StringVar(value=str(PROJECT_ROOT / "fixtures" / "sample" / "archive.json"))
        self.status = tk.StringVar(value="Local-only · ready")
        self._buttons: list[object] = []
        self._build()

    def _build(self) -> None:
        root = self.root
        style = self.ttk.Style(root)
        try:
            style.theme_use("clam")
        except self.tk.TclError:
            pass
        style.configure("TFrame", background="#313338")
        style.configure("Panel.TFrame", background="#2b2d31")
        style.configure("TLabel", background="#313338", foreground="#f2f3f5", font=("Segoe UI", 10))
        style.configure("Title.TLabel", background="#2b2d31", foreground="#f2f3f5", font=("Segoe UI", 18, "bold"))
        style.configure("Muted.TLabel", background="#2b2d31", foreground="#b5bac1", font=("Segoe UI", 9))
        style.configure("TButton", padding=(10, 7), background="#5865f2", foreground="#ffffff", font=("Segoe UI", 9, "bold"))
        style.map("TButton", background=[("active", "#4752c4"), ("disabled", "#454850")])
        style.configure("TEntry", fieldbackground="#1e1f22", foreground="#f2f3f5", insertcolor="#f2f3f5")

        header = self.ttk.Frame(root, style="Panel.TFrame", padding=(24, 20, 24, 18))
        header.pack(fill="x")
        self.ttk.Label(header, text="Concordance", style="Title.TLabel").pack(anchor="w")
        self.ttk.Label(header, text="OFFLINE WORKSPACE · OPERATOR LAUNCHER", style="Muted.TLabel").pack(anchor="w", pady=(3, 0))

        content = self.ttk.Frame(root, padding=24)
        content.pack(fill="both", expand=True)
        self.ttk.Label(content, text="Choose a local archive, viewer, or catalog").pack(anchor="w")
        row = self.ttk.Frame(content)
        row.pack(fill="x", pady=(8, 18))
        entry = self.ttk.Entry(row, textvariable=self.source)
        entry.pack(side="left", fill="x", expand=True)
        self.ttk.Button(row, text="Choose archive", command=self._choose_archive).pack(side="left", padx=(8, 0))
        self.ttk.Button(row, text="Choose viewer", command=self._choose_viewer).pack(side="left", padx=(8, 0))

        actions = self.ttk.Frame(content, style="Panel.TFrame", padding=16)
        actions.pack(fill="x")
        self.ttk.Label(actions, text="LOCAL ACTIONS", style="Muted.TLabel").pack(anchor="w")
        button_row = self.ttk.Frame(actions, style="Panel.TFrame")
        button_row.pack(fill="x", pady=(10, 0))
        for column in range(3):
            button_row.columnconfigure(column, weight=1)
        for index, (label, command) in enumerate((
            ("Build & open viewer", self._build_and_open),
            ("Open selected", self._open_selected),
            ("Build catalog", self._build_catalog),
            ("Audit media", self._audit_media),
            ("Safe-share archive", self._safe_share),
            ("Package release", self._package_release),
        )):
            button = self.ttk.Button(button_row, text=label, command=command)
            button.grid(row=index // 3, column=index % 3, sticky="ew", padx=(0 if index % 3 == 0 else 4, 4 if index % 3 < 2 else 0), pady=(0 if index < 3 else 7, 0))
            self._buttons.append(button)

        self.ttk.Label(content, textvariable=self.status).pack(anchor="w", pady=(18, 5))
        self.log = self.tk.Text(
            content,
            height=12,
            wrap="word",
            state="disabled",
            bg="#1e1f22",
            fg="#b5bac1",
            insertbackground="#f2f3f5",
            relief="flat",
            padx=10,
            pady=10,
        )
        self.log.pack(fill="both", expand=True)
        self._append("No live connection. Concordance only reads files you select.")

    def _append(self, value: str) -> None:
        self.log.configure(state="normal")
        self.log.insert("end", value.rstrip() + "\n")
        self.log.see("end")
        self.log.configure(state="disabled")

    def _set_busy(self, busy: bool, label: str = "") -> None:
        for button in self._buttons:
            button.configure(state="disabled" if busy else "normal")
        self.status.set(label or ("Local-only · ready" if not busy else "Working locally…"))

    def _choose_archive(self) -> None:
        selected = self.filedialog.askopenfilename(
            title="Choose normalized archive",
            initialdir=str(PROJECT_ROOT),
            filetypes=[("Archive JSON", "*.json"), ("All files", "*.*")],
        )
        if selected:
            self.source.set(selected)

    def _choose_viewer(self) -> None:
        selected = self.filedialog.askdirectory(title="Choose viewer or catalog directory", initialdir=str(PROJECT_ROOT))
        if selected:
            self.source.set(selected)

    def _selected(self) -> Path:
        selected = Path(self.source.get()).expanduser().resolve()
        if not selected.exists():
            raise FileNotFoundError(f"Selected path does not exist: {selected}")
        return selected

    def _run_background(self, label: str, operation: Callable[[], tuple[int, str]]) -> None:
        self._set_busy(True, label)

        def worker() -> None:
            try:
                code, output = operation()
            except Exception as error:  # pragma: no cover - exercised by the GUI
                code, output = 1, str(error)

            def finish() -> None:
                self._set_busy(False, "Local-only · ready" if code == 0 else "Local-only · needs attention")
                self._append(output)
                if code != 0:
                    self.messagebox.showerror("Concordance", output)

            self.root.after(0, finish)

        threading.Thread(target=worker, daemon=True).start()

    def _build_and_open(self) -> None:
        try:
            archive = self._selected()
            if archive.suffix.lower() != ".json":
                raise ValueError("Build & open requires a normalized archive JSON file")
            output = archive.parent / f"{archive.stem}-view"
        except Exception as error:
            self.messagebox.showerror("Concordance", str(error))
            return

        def operation() -> tuple[int, str]:
            result = _run_cli(["build", str(archive), "--output", str(output)])
            output_text = (result.stdout or "") + (result.stderr or "")
            if result.returncode == 0:
                _open_local(output)
            return result.returncode, output_text or f"Viewer ready: {output}"

        self._run_background("Building local viewer…", operation)

    def _open_selected(self) -> None:
        try:
            _open_local(self._selected())
            self.status.set("Local-only · viewer opened")
        except Exception as error:
            self.messagebox.showerror("Concordance", str(error))

    def _audit_media(self) -> None:
        try:
            archive = self._selected()
            if archive.suffix.lower() != ".json":
                raise ValueError("Media audit requires a normalized archive JSON file")
        except Exception as error:
            self.messagebox.showerror("Concordance", str(error))
            return

        def operation() -> tuple[int, str]:
            result = _run_cli(["audit-media", "--input", str(archive)])
            return result.returncode, (result.stdout or "") + (result.stderr or "")

        self._run_background("Auditing local media…", operation)

    def _powershell(self) -> str:
        powershell = shutil.which("pwsh") or shutil.which("powershell")
        if not powershell:
            raise RuntimeError("PowerShell is required for this launcher action.")
        return powershell

    def _build_catalog(self) -> None:
        archive_directory = self.filedialog.askdirectory(
            title="Choose folder containing normalized archive JSON files",
            initialdir=str(PROJECT_ROOT),
        )
        if not archive_directory:
            return
        output = self.filedialog.askdirectory(
            title="Choose catalog output folder",
            initialdir=str(PROJECT_ROOT / "dist"),
        )
        if not output:
            return
        script = PROJECT_ROOT / "scripts" / "build_catalog.ps1"

        def operation() -> tuple[int, str]:
            result = subprocess.run(
                [self._powershell(), "-NoProfile", "-File", str(script), "-ArchiveDirectory", archive_directory, "-OutputPath", output],
                cwd=PROJECT_ROOT,
                capture_output=True,
                text=True,
                encoding="utf-8",
                errors="replace",
                check=False,
            )
            output_text = (result.stdout or "") + (result.stderr or "")
            if result.returncode == 0:
                _open_local(Path(output))
            return result.returncode, output_text or f"Catalog ready: {output}"

        self._run_background("Building local catalog…", operation)

    def _safe_share(self) -> None:
        try:
            archive = self._selected()
            if archive.suffix.lower() != ".json":
                raise ValueError("Safe-share requires a normalized archive JSON file")
            powershell = self._powershell()
        except Exception as error:
            self.messagebox.showerror("Concordance", str(error))
            return

        destination = self.filedialog.asksaveasfilename(
            title="Create safe-share bundle",
            initialdir=str(PROJECT_ROOT / "private-data"),
            initialfile=f"{archive.stem}.safe-share.concordance.zip",
            defaultextension=".zip",
            filetypes=[("Concordance bundle", "*.zip"), ("All files", "*.*")],
        )
        if not destination:
            return
        encrypt = self.messagebox.askyesno(
            "Encrypt safe-share bundle?",
            "Encrypt the redacted bundle with AES-GCM? Choose a private password file next.",
        )
        password_file = None
        if encrypt:
            password_file = self.filedialog.askopenfilename(
                title="Choose private password file",
                initialdir=str(PROJECT_ROOT / "private-data"),
                filetypes=[("Password text file", "*.txt"), ("All files", "*.*")],
            )
            if not password_file:
                return
            if not destination.lower().endswith(".enc"):
                destination = f"{destination}.enc"
        script = PROJECT_ROOT / "scripts" / "share_archive.ps1"

        def operation() -> tuple[int, str]:
            arguments = [powershell, "-NoProfile", "-File", str(script), "-ArchivePath", str(archive), "-OutputPath", destination]
            if encrypt:
                arguments.append("-Encrypt")
                arguments.extend(["-PasswordFile", str(password_file)])
            result = subprocess.run(
                arguments,
                cwd=PROJECT_ROOT,
                capture_output=True,
                text=True,
                encoding="utf-8",
                errors="replace",
                check=False,
            )
            return result.returncode, (result.stdout or "") + (result.stderr or "")

        self._run_background("Creating private safe-share bundle…", operation)

    def _package_release(self) -> None:
        destination = self.filedialog.asksaveasfilename(
            title="Create source-only release ZIP",
            initialdir=str(PROJECT_ROOT / "dist"),
            initialfile="concordance-release.zip",
            defaultextension=".zip",
            filetypes=[("ZIP archive", "*.zip")],
        )
        if not destination:
            return
        try:
            powershell = self._powershell()
        except Exception as error:
            self.messagebox.showerror("Concordance", str(error))
            return
        script = PROJECT_ROOT / "scripts" / "package_release.ps1"

        def operation() -> tuple[int, str]:
            result = subprocess.run(
                [powershell, "-NoProfile", "-File", str(script), "-OutputPath", destination],
                cwd=PROJECT_ROOT,
                capture_output=True,
                text=True,
                encoding="utf-8",
                errors="replace",
                check=False,
            )
            return result.returncode, (result.stdout or "") + (result.stderr or "")

        self._run_background("Packaging source-only release…", operation)

    def run(self) -> int:
        self.root.mainloop()
        return 0


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Open Concordance's local Windows launcher")
    parser.add_argument("--check", action="store_true", help="validate launcher prerequisites without opening a window")
    args = parser.parse_args(argv)
    if args.check:
        return check_environment()
    try:
        import tkinter as tk
        from tkinter import filedialog, messagebox, ttk
    except ImportError as error:
        print(f"Concordance launcher requires Tkinter: {error}", file=sys.stderr)
        return 2
    return Launcher(tk, ttk, filedialog, messagebox).run()


if __name__ == "__main__":
    raise SystemExit(main())
