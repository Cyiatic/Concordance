# Concordance usage guide

Full command reference for building, cataloguing, importing, verifying and sharing archives.
Capturing from Discord Web is covered separately in the [capture guide](CAPTURE.md).

Run commands from the repository root after `python -m pip install -e .`, or with
`$env:PYTHONPATH = "src"` set. Keep real archives under `private-data/`, which Git ignores.

## Viewer output and integrity

The generated directory includes an `evidence.json` provenance record and a
`manifest.json` with SHA-256 hashes for the viewer, archive, evidence record,
and referenced local assets. The reader exposes the compact integrity record in
its archive-details drawer. Run `verify` after copying an archive to confirm
that its files and local assets are intact; run `verify-evidence` directly when
you want the provenance report's detailed diagnostics. Copy the entire
directory if you want the local avatars and attachments to travel with the
transcript.

For large archives, the viewer keeps the active transcript window bounded to
240 messages while search still considers the full archive. Deep links reveal
and select an older message automatically; print/PDF expands the current
filtered view for export.

## Build a local archive catalog

When more than one conversation has been archived, build a private catalog as
the entry point instead of opening viewer folders individually. The catalog is a
static offline launcher: it searches conversation titles, participant names, and
source filenames, shows capture coverage before opening a record, and links to
each generated viewer.

```powershell
$env:PYTHONPATH = "src"
python -m concordance build-catalog `
  --input private-data\conversation-a.json `
  --input private-data\conversation-b.json `
  --output private-data\concordance-library
python -m concordance verify-catalog private-data\concordance-library
Start-Process (Resolve-Path private-data\concordance-library\index.html)
```

The catalog output contains only generated local files and should remain under
an ignored or access-controlled private path. Its `catalog.json` stores titles,
participant display names, message counts, date bounds, coverage status, source
filenames, and source hashes; the message payloads remain inside each linked
archive viewer. The catalog does not merge conversations or connect to Discord.
For cross-conversation message search, opt in explicitly; this adds a private
`message-index.json` containing searchable message text and deep links:

```powershell
python -m concordance build-catalog `
  --input private-data\conversation-a.json `
  --input private-data\conversation-b.json `
  --output private-data\concordance-library `
  --include-message-index
```

Keep that output access-controlled because the optional index contains message
text. The default catalog never copies message bodies.

## Export a private evidence report

When you need to confirm what a normalized archive contains before building a
viewer, or want a standalone report beside it, export a metadata-only evidence
report:

```powershell
$env:PYTHONPATH = "src"
python -m concordance export-evidence `
  --input private-data\conversation.json `
  --output private-data\conversation-evidence.json `
  --session private-data\capture-session.json
python -m concordance verify-evidence private-data\conversation-evidence.json
```

The report records archive and local-asset SHA-256 hashes, message and feature
counts, timestamp bounds, source diagnostics, and capture coverage. Generated
viewers include the same report automatically. An optional
guided capture session adds its own hash and coverage snapshot. It does not
copy message bodies, attachments, credentials, cookies, or absolute paths.
When a permitted browser capture supplies local rendered-DOM and screenshot
files, preserve them explicitly with repeated `--dom` and `--screenshot` flags;
the evidence report copies them into a hashed `evidence/` subdirectory and the
viewer links them from its Integrity record. These files may contain the
conversation, so keep the bundle private.
If the normalized archive was imported from a session's finalized transcript,
the report keeps the session's finalized filename and records whether it is the
same file as the archive being evidenced.
Keep the report private: channel identifiers, message boundary IDs, and source
filenames can still be sensitive. Verification fails when the archive, a
required local asset, or the linked session changes or disappears.

To bind rendered proof to a specific attended range before exporting the report,
attach it to the capture session. The files are copied into the private session
directory, hashed, and automatically included by `export-evidence --session`:

```powershell
python -m concordance capture-session attach-evidence `
  --session private-data\capture-session.json `
  --capture private-data\range-001.json `
  --dom private-data\range-001-rendered.html `
  --screenshot private-data\range-001.png
```

For a rendered DOM file from the attended tab, evaluate
`tools\discord_visible_evidence.js` in that same visible Discord tab, save its
returned `html` value under `private-data\`, and attach it with the command
above. The helper reads only the rendered page; it does not inspect browser
storage or download assets.

## Create a safe-share copy

Use the safe-share profile before sending an archive to someone else. It keeps
message count, layout, timestamps, and coverage shape while anonymizing
participants, replacing message text, remapping IDs, and removing links/media:

```powershell
python -m concordance redact `
  --input private-data\conversation.json `
  --output private-data\conversation-safe.json
python -m concordance build `
  private-data\conversation-safe.json `
  --output private-data\conversation-safe-view
python -m concordance export-bundle `
  --input private-data\conversation-safe-view `
  --output private-data\conversation-safe.zip
```

The source archive is never overwritten. For password protection, install the
optional secure extra and use a password prompt or a private password file:

```powershell
python -m pip install -e ".[secure]"
python -m concordance encrypt-bundle `
  --input private-data\conversation-safe-view `
  --output private-data\conversation-safe.concordance.enc
python -m concordance decrypt-bundle `
  --input private-data\conversation-safe.concordance.enc `
  --output private-data\conversation-safe-restored
```

Encrypted bundles use PBKDF2-HMAC-SHA256 and AES-256-GCM. Passwords are never
stored in the bundle or accepted as command-line arguments.

## Migrate an older archive

Run the migration command when a future release introduces a schema change. It
is safe to run against a current archive and writes a separate output file:

```powershell
python -m concordance migrate `
  --input private-data\conversation.json `
  --output private-data\conversation-migrated.json
```

## Import an official data package

```powershell
$env:PYTHONPATH = "src"
python -m concordance import-data-package `
  --input C:\path\to\DiscordDataPackage `
  --output private-data\discord-package.json
python -m concordance build `
  private-data\discord-package.json `
  --output private-data\discord-package-view
```

The resulting archive should remain outside Git. Review and redact it before
sharing it with anyone else.

When present in the source files, the official importer preserves attachments,
reactions, embeds, replies, edit timestamps, message links, and per-record
provenance. Remote media remains a reference; it is never downloaded.

To make already-recorded Discord media and link previews render normally
offline, use the separate attended materialization step. It requires an
explicit acknowledgement, follows only URLs already present in the archive
plus predictable thumbnails for already-recorded YouTube links, and permits
only the approved Discord CDN/proxy and YouTube thumbnail hosts. Other
captured preview links remain reference-only unless a specific adapter is
approved:

```powershell
$env:PYTHONPATH = "src"
python -m concordance materialize-media `
  --input private-data\conversation.json `
  --output private-data\conversation.json `
  --allow-remote
python -m concordance build `
  private-data\conversation.json `
  --output private-data\conversation-view
```

This copies already-recorded Discord CDN attachments (including audio, video,
and PDFs), embedded image/audio/video references, stickers, custom emoji, link
preview thumbnails, and missing avatar references into private local assets
while retaining the original URL as provenance. YouTube links without a
captured preview image receive a predictable `i.ytimg.com` thumbnail reference,
marked as `derived_youtube_thumbnail`, so the viewer can present a real
Discord-like video card without requiring a live page. It does not search
Discord, discover additional conversation media, or access account data.
Unsupported source records remain visible as references rather than being
silently treated as downloaded.

Audit offline readiness without downloading anything:

```powershell
python -m concordance audit-media `
  --input private-data\conversation.json `
  --output private-data\conversation-media-audit.json
```

The audit classifies each recorded reference as offline-ready, explicitly
downloadable, missing locally, reference-only, or metadata-only. A nonzero exit
status means at least one recorded reference still needs review.

Discord stickers rendered as canvas/Lottie media are captured by their visible
sticker ID, name, format, and approved CDN reference. When an attended capture
also preserves a rendered local frame, the normalized sticker may include a
relative `preview_path`; the viewer uses that local preview while retaining the
original CDN reference as provenance.

## Import a user-supplied transcript

Use `import-transcript` when the permitted source is an explicitly provided
transcript rather than an official account data package:

```powershell
$env:PYTHONPATH = "src"
python -m concordance import-transcript `
  --input C:\path\to\transcript.json `
  --output private-data\conversation.json
python -m concordance build `
  private-data\conversation.json `
  --output private-data\conversation-view
```

The JSON input may be a bare message array or an object containing `metadata`,
`participants`, and `messages`. Message records preserve supplied IDs,
timestamps, authors, replies, reactions, embeds, attachments, edits, and source
links. If a message has no ID, the adapter creates a deterministic local ID and
marks it in `provenance.id_generated`; this does not claim to be a source ID.

Local `avatar_path` and attachment `path` values are relative to the transcript
file and are copied into the built archive. HTTP(S) links remain references and
are never fetched. Each imported message records its source filename and input
record index in `provenance`. Absolute source paths are intentionally omitted
from the resulting archive.

The built viewer never treats remote URLs as local assets. Missing local files
are reported by the build command, while remote attachment URLs remain visible
as reference-only links.

## Windows helper scripts

For a repeatable Windows build-and-open loop, use the checked-in PowerShell
helpers:

```powershell
.\scripts\build_archive.ps1 `
  -ArchivePath private-data\conversation.json `
  -OutputPath private-data\conversation-view
.\scripts\open_viewer.ps1 -Path private-data\conversation-view
```

The local launcher accepts an existing viewer/catalog path or builds and opens
a normalized archive in one step:

```powershell
.\scripts\concordance.ps1 -Path private-data\conversation.json -Build -Open
.\scripts\concordance.ps1 -Path private-data\conversation-view
```

For a small dependency-free Windows desktop launcher, double-click
`scripts\Concordance.cmd`, or run it from PowerShell. It keeps the workflow
local-only and provides buttons for choosing a normalized archive, building or
opening a viewer, building a multi-archive catalog, auditing media, creating a
redacted safe-share bundle, and packaging a source-only release. Use `--check`
to validate prerequisites without opening the window:

```powershell
.\scripts\Concordance.cmd --check
python scripts\concordance_launcher.py
```

To build a catalog from every valid normalized archive in a selected folder:

```powershell
.\scripts\build_catalog.ps1 `
  -ArchiveDirectory private-data\archives `
  -OutputPath private-data\concordance-library
```

To create a redacted safe-share bundle, optionally encrypted with a private
password file:

```powershell
.\scripts\share_archive.ps1 `
  -ArchivePath private-data\conversation.json `
  -OutputPath private-data\conversation.safe-share.concordance.zip

.\scripts\share_archive.ps1 `
  -ArchivePath private-data\conversation.json `
  -OutputPath private-data\conversation.safe-share.concordance.enc `
  -Encrypt `
  -PasswordFile private-data\share-password.txt
```

The safe-share helper redacts identities, message text, links, and media before
building the viewer and bundle. It does not mutate the source archive or fetch
additional assets.

To package or restore a viewer in one command:

```powershell
.\scripts\export_bundle.ps1 `
  -ArchivePath private-data\conversation.json `
  -BundlePath private-data\conversation.concordance.zip
.\scripts\import_bundle.ps1 `
  -BundlePath private-data\conversation.concordance.zip `
  -OutputPath private-data\restored-view `
  -Open
```

## Release packaging and CI

For a source-only release package, run the release helper. It validates the
synthetic fixture, builds and verifies a sample viewer, and never copies
`private-data/`:

```powershell
.\scripts\package_release.ps1 -OutputPath dist\concordance-release.zip
python scripts\source_release_smoke.py --archive dist\concordance-release.zip
```

Before publishing a release, run the dependency-free preflight and clean-wheel
smoke test. The preflight checks version alignment, required release files,
tracked private/generated paths, and synthetic viewer verification. The wheel
smoke test installs the built package into a fresh environment and confirms
that the installed CLI can still build an offline viewer:

```powershell
python scripts\release_check.py
python -m pip wheel . --no-deps --wheel-dir dist\wheel
python scripts\wheel_smoke.py --wheel-dir dist\wheel
```

GitHub Actions runs the Python test suite, source compilation, catalog-script
syntax check, and synthetic viewer build/verification on supported Python
versions. The workflow never needs private archive inputs.
