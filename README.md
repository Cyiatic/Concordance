# Discord Archive

A local-first tool for turning permitted conversation exports into searchable,
offline-readable archives. The first milestone is a normalized JSON schema and
an **Archive Ledger** viewer that renders message text, timestamps, avatars,
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

## Build the sample archive

```powershell
$env:PYTHONPATH = "src"
python -m discord_archive validate fixtures/sample/archive.json
python -m discord_archive build fixtures/sample/archive.json --output dist/sample
python -m discord_archive verify dist/sample
Start-Process (Resolve-Path dist/sample/index.html)
```

The generated directory includes a `manifest.json` with SHA-256 hashes for the
viewer, archive, and referenced local assets. Run `verify` after copying an
archive to confirm that its files and local assets are intact. Copy the entire
directory if you want the local avatars and attachments to travel with the
transcript.

For large archives, the viewer keeps the active transcript window bounded to
240 messages while search still considers the full archive. Deep links reveal
and select an older message automatically; print/PDF expands the current
filtered view for export.

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

To make already-recorded Discord image references render as normal offline
images, use the separate attended materialization step. It requires an explicit
acknowledgement, follows only URLs already present in the archive, and permits
only Discord CDN hosts:

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

This copies image attachments, embedded images, and missing avatar references
into private local assets while retaining the original URL as provenance. It
does not search Discord, discover additional media, or access account data.

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
both rendered authors, message IDs, UTC timestamps, replies, reactions, embeds,
attachments, source links, and remote avatar/media references. Because Discord
virtualizes long histories, a capture is explicitly scoped to the currently
rendered range; the user can load another range and capture it separately for a
later merge. Keep the resulting JSON under `private-data/`.

The first live smoke run in this checkout used the open DM with channel ID
`1224842431787307097`, produced a private 20-message transcript with two
participants, and passed archive validation, viewer build, manifest verification,
and JavaScript syntax checks. No live data is stored in the repository.

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
  --reached-end
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

## Privacy model

The repository is for code and synthetic fixtures, not personal conversation
records. Keep real archives in an encrypted or otherwise access-controlled
location. Do not place Discord passwords, account tokens, or raw private chats
in source control.
