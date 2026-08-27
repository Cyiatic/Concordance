<h1 align="left"><img src="viewer/assets/concordance-mark.png" alt="" width="64" height="64" align="absmiddle"> Concordance</h1>

Concordance is a local-first tool for turning permitted conversation exports into searchable,
offline-readable archives. The first milestone is a normalized JSON schema and
a **Concordance** viewer that renders message text, timestamps, avatars,
replies, reactions, embeds, attachments, provenance, and offline status without
loading Discord or any external asset.

## Current status

The MVP currently includes:

- a versioned normalized archive schema;
- validation with actionable error messages;
- a static HTML viewer with search, author filtering, timestamp modes, message
  selection, deep links, local image/audio/video previews, local-file links,
  print support, and responsive layout;
- a synthetic two-person fixture;
- a first-pass importer for Discord Data Package message JSON files; and
- a network-free adapter for user-supplied transcript JSON files.

The official Discord Data Package contains messages sent by the requesting
account, not a complete two-sided conversation. The importer preserves that
limitation in its metadata and records import diagnostics under
`metadata.source.import_summary` when files or records are skipped. A separate
user-supplied transcript adapter accepts explicitly provided JSON, while
acquisition remains separate from normalization and viewing.

## Codex plugin package

This repository includes `plugins/concordance/`, a skills-only Codex plugin for
the authorized capture, offline archive, coverage verification, media audit,
and safe-sharing workflow. It is designed to be packaged or installed through
Codex without bundling any conversation data. The plugin does not extract
Discord tokens, run self-bots, crawl unattended, collect credentials, or write
back to Discord.

Its workflow is named `concordance-archive`; invoke `$concordance-archive` after
the plugin is installed in Codex.

Suggested positioning:

> Your Discord memories, readable offline—captured with permission, preserved
> locally.

Use “offline snapshot” or “portable archive,” not “rebuild your Discord
profile”: Concordance recreates the permitted viewing experience locally and
preserves available profile metadata, but it is not an account clone or a
Discord restore tool.

## Build a sample Concordance archive

```powershell
$env:PYTHONPATH = "src"
python -m discord_archive validate fixtures/sample/archive.json
python -m discord_archive build fixtures/sample/archive.json --output dist/sample
python -m discord_archive verify dist/sample
Start-Process (Resolve-Path dist/sample/index.html)
```

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
python -m discord_archive build-catalog `
  --input private-data\conversation-a.json `
  --input private-data\conversation-b.json `
  --output private-data\concordance-library
python -m discord_archive verify-catalog private-data\concordance-library
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
python -m discord_archive build-catalog `
  --input private-data\conversation-a.json `
  --input private-data\conversation-b.json `
  --output private-data\concordance-library `
  --include-message-index
```

Keep that output access-controlled because the optional index contains message
text. The default catalog never copies message bodies.

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

GitHub Actions runs the Python test suite, source compilation, catalog-script
syntax check, and synthetic viewer build/verification on supported Python
versions. The workflow never needs private archive inputs.

### Export a private evidence report

When you need to confirm what a normalized archive contains before building a
viewer, or want a standalone report beside it, export a metadata-only evidence
report:

```powershell
$env:PYTHONPATH = "src"
python -m discord_archive export-evidence `
  --input private-data\conversation.json `
  --output private-data\conversation-evidence.json `
  --session private-data\capture-session.json
python -m discord_archive verify-evidence private-data\conversation-evidence.json
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
python -m discord_archive capture-session attach-evidence `
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

### Create a safe-share copy

Use the safe-share profile before sending an archive to someone else. It keeps
message count, layout, timestamps, and coverage shape while anonymizing
participants, replacing message text, remapping IDs, and removing links/media:

```powershell
python -m discord_archive redact `
  --input private-data\conversation.json `
  --output private-data\conversation-safe.json
python -m discord_archive build `
  private-data\conversation-safe.json `
  --output private-data\conversation-safe-view
python -m discord_archive export-bundle `
  --input private-data\conversation-safe-view `
  --output private-data\conversation-safe.zip
```

The source archive is never overwritten. For password protection, install the
optional secure extra and use a password prompt or a private password file:

```powershell
python -m pip install -e ".[secure]"
python -m discord_archive encrypt-bundle `
  --input private-data\conversation-safe-view `
  --output private-data\conversation-safe.concordance.enc
python -m discord_archive decrypt-bundle `
  --input private-data\conversation-safe.concordance.enc `
  --output private-data\conversation-safe-restored
```

Encrypted bundles use PBKDF2-HMAC-SHA256 and AES-256-GCM. Passwords are never
stored in the bundle or accepted as command-line arguments.

### Migrate an older archive

Run the migration command when a future release introduces a schema change. It
is safe to run against a current archive and writes a separate output file:

```powershell
python -m discord_archive migrate `
  --input private-data\conversation.json `
  --output private-data\conversation-migrated.json
```

## Import an official data package

```powershell
$env:PYTHONPATH = "src"
python -m discord_archive import-data-package `
  --input C:\path\to\DiscordDataPackage `
  --output private-data\discord-package.json
python -m discord_archive build `
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
python -m discord_archive materialize-media `
  --input private-data\conversation.json `
  --output private-data\conversation.json `
  --allow-remote
python -m discord_archive build `
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
python -m discord_archive audit-media `
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
python -m discord_archive import-transcript `
  --input C:\path\to\transcript.json `
  --output private-data\conversation.json
python -m discord_archive build `
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

## Capture one open DM from Discord Web

For a two-sided archive owned by the user, `tools/discord_visible_capture.js`
is a read-only, user-triggered browser adapter. Open the intended DM yourself
in Discord Web, then invoke the adapter in that visible browser context. It
accepts only a URL shaped like `/channels/@me/<channel-id>` and reads the
messages Discord has rendered in that open conversation. It does not search for
users, automate login, inspect cookies or tokens, send messages, call Discord's
private API, or download media.

The adapter returns transcript JSON accepted by `import-transcript`. It keeps
both rendered authors, message IDs, canonical UTC timestamps, the source UI's
visible date/time label when Discord exposes it, replies, reactions, embeds,
attachments, source links, and remote avatar/media references. The viewer uses
that source display label in its default mode while retaining Local and UTC
switches. Because Discord virtualizes long histories, a capture is explicitly
scoped to the currently rendered range; the user can load another range and
capture it separately for a later merge. Keep the resulting JSON under
`private-data/`.

Image-only link previews are retained from the rendered Discord accessory even
when no embed title is present. Their displayed Discord image-proxy URL is
preserved so the explicit `materialize-media --allow-remote` step can copy the
visible image into the offline viewer while retaining the original source link.

When the corresponding Discord profile card is visibly open, the same attended
step also records optional profile metadata: the participant avatar, username,
pronouns, observed presence, visible badges, mutual-friend labels, member-since
text, banner/avatar-decoration references, custom status, and visible activity
labels. Concordance does not open profiles, infer absent fields, or treat a
missing profile card as evidence that a field is empty. `materialize-media`
can explicitly copy the already-recorded avatar, badge, and mutual-friend image
references, plus captured profile banners and avatar decorations, into the
private archive for offline rendering.

The adapter can label one externally performed navigation step. The browser
control performs the scroll; the page adapter remains read-only and captures
only after the virtualized DOM has settled:

```javascript
// Get the message-list node id from the visible DOM snapshot first.
const beforeScrollTop = 2400
await tab.dom_cua.scroll({ node_id: "<message-list-node-id>", x: 0, y: -500 })
await tab.playwright.waitForTimeout(900)
await tab.playwright.evaluate(
  `(${captureSource})({ direction: "older", previous_scroll_top: ${beforeScrollTop} })`,
)
```

Use a negative scroll delta for older messages and a positive delta for newer
messages. Repeat the explicit scroll-and-capture action with an overlap until
the returned `capture_range.at_start` or `capture_range.at_end` is true, then
merge the ranges. This is deliberately a bounded, user-triggered step rather
than an automatic history crawler.

The local smoke run for an open DM wrote its transcript only beneath the ignored
`private-data\` path and passed archive validation, viewer build, manifest
verification, and JavaScript syntax checks. No live data or account identifiers
are stored in the repository.

### Track a guided capture session

For a long or virtualized DM, a private capture session makes the range workflow
repeatable. Initialize it beside the capture JSON files, then add each range after
you have visibly scrolled the open DM yourself. Dates are optional: when no dates
are known, completeness is established from an overlap-linked chain plus both
observed scroll boundaries. If dates are known, add them as extra checkpoints;
they can only prevent a false complete result, not prove that unrendered or
deleted messages existed:

```powershell
$env:PYTHONPATH = "src"
python -m discord_archive capture-session init `
  --output private-data\conversation-session.json `
  --title "Conversation title" `
  --expect-date 2024-01-15 `
  --expect-date 2024-06-30

# In the already-open Discord DM, scroll to a rendered range and run the
# read-only tools/discord_visible_capture.js adapter in that visible context.
python -m discord_archive capture-session add `
  --session private-data\conversation-session.json `
  --input private-data\range-001.json
python -m discord_archive capture-session status `
  --session private-data\conversation-session.json
python -m discord_archive capture-session next `
  --session private-data\conversation-session.json

# Build a message-free local guide with the next action, overlap ledger,
# boundary attestations, expected dates, and media-host diagnostics.
python -m discord_archive capture-session dashboard `
  --session private-data\conversation-session.json `
  --output private-data\conversation-capture-guide
python -m discord_archive capture-session verify-dashboard `
  private-data\conversation-capture-guide
Start-Process (Resolve-Path private-data\conversation-capture-guide\index.html)

# Add checkpoints later without restarting the session. Use --replace to
# replace the existing list, or --replace with no dates to clear it.
python -m discord_archive capture-session checkpoints `
  --session private-data\conversation-session.json `
  --expect-date 2024-01-15 `
  --expect-date 2024-06-30

python -m discord_archive capture-session finalize `
  --session private-data\conversation-session.json `
  --output private-data\merged-transcript.json `
  --reached-start `
  --reached-end
python -m discord_archive import-transcript `
  --input private-data\merged-transcript.json `
  --output private-data\conversation.json
```

The session stores only relative capture filenames, SHA-256 hashes, the DM channel
ID, observed boundaries, coverage status, and aggregate feature/media diagnostics.
It does not copy message payloads, absolute paths, credentials, cookies, or
tokens. `status` gives the next capture action when the observed ranges are
incomplete. The dashboard is a static snapshot and intentionally contains no
message bodies; refresh it after adding a range. The same-directory requirement
keeps the existing portable merge behavior predictable; keep the session manifest
and its source ranges together under an ignored private path.

`capture-session next` turns that status into a bounded browser-step plan. It
reports only a direction, a safe scroll baseline when one was recorded, the
required overlap, and the command to add the next saved range. It never scrolls
or captures on its own.

Optional `--expect-date YYYY-MM-DD` checkpoints make known history landmarks
explicit. The dates are compared with each message's canonical UTC timestamp
and, when available, its preserved visible Discord calendar date; the session
remains incomplete until every checkpoint appears in at least one captured
message. This is useful for auditing known calendar checkpoints without
pretending that a date checkpoint proves messages that Discord did not render.

### Capture a virtualized DM in ranges

Discord may keep only a window of a long conversation in the DOM. For complete
coverage, capture several overlapping ranges while you scroll the open DM
yourself. Each capture records its oldest/newest message IDs and whether the
Discord message scroller was at its oldest or newest boundary:

```powershell
$env:PYTHONPATH = "src"
python -m discord_archive merge-transcripts `
  --input private-data\range-001.json `
  --input private-data\range-002.json `
  --input private-data\range-003.json `
  --output private-data\merged-transcript.json `
  --reached-start `
  --reached-end `
  --expect-date 2024-01-15 `
  --expect-date 2024-07-01
python -m discord_archive verify-coverage private-data\merged-transcript.json
python -m discord_archive import-transcript `
  --input private-data\merged-transcript.json `
  --output private-data\conversation.json
python -m discord_archive build private-data\conversation.json --output private-data\conversation-view
```

The merge deduplicates overlapping message IDs and marks coverage `verified`
only when the ranges are overlap-linked, both boundaries are observed or
explicitly attested, and no conflicting duplicate records exist. A successful
coverage report still means “verified from rendered ranges”; it cannot prove
messages Discord failed to render or messages deleted before capture.

The CLI prints a concrete `Next action` whenever coverage is incomplete, and
the offline viewer repeats that instruction in a prominent coverage banner.
Partial captures are labeled “Archive is incomplete” so they cannot be
mistaken for a complete conversation. Its capture ledger lists each range's
date span, overlap count, and boundary markers so gaps are visible in the
archive itself.

## Archive format

The canonical file has this shape:

```json
{
  "schema_version": 1,
  "metadata": {
    "kind": "direct_message",
    "title": "Conversation title",
    "channel_id": "...",
    "display_timezone": "America/Phoenix",
    "source": {"type": "user_supplied"}
  },
  "participants": [
    {"id": "user-1", "display_name": "User 1", "avatar_path": "assets/avatars/user-1.svg"}
  ],
  "messages": [
    {
      "id": "message-1",
      "author_id": "user-1",
      "timestamp": "2018-11-30T06:52:00Z",
      "content": "Good one"
    }
  ]
}
```

## AI Assistance Disclosure

This project was developed with assistance from OpenAI Codex for implementation,
archive-schema and viewer work, capture tooling, security hardening, release
checks, and documentation. The repository owner directed the work, reviewed and
tested the resulting changes, and remains responsible for the project's
security, accuracy, licensing, and final decisions.

## Privacy model

The repository is for code and synthetic fixtures, not personal conversation
records. Keep real archives in an encrypted or otherwise access-controlled
location. Do not place Discord passwords, account tokens, or raw private chats
in source control.
