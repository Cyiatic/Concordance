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
  selection, deep links, attachment previews, print support, and responsive
  layout;
- a synthetic two-person fixture; and
- a first-pass importer for Discord Data Package message JSON files.

The official Discord Data Package contains messages sent by the requesting
account, not a complete two-sided conversation. The importer preserves that
limitation in its metadata. A future input adapter may accept an explicitly
provided transcript format, but acquisition remains separate from this viewer.

## Build the sample archive

```powershell
$env:PYTHONPATH = "src"
python -m discord_archive validate fixtures/sample/archive.json
python -m discord_archive build fixtures/sample/archive.json --output dist/sample
Start-Process (Resolve-Path dist/sample/index.html)
```

The generated directory is portable. Copy the entire directory if you want the
local avatars and attachments to travel with the transcript.

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
