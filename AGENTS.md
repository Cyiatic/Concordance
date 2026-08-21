# Discord Archive project guidance

## Scope

This repository contains the archive schema, importers, offline viewer, and
synthetic fixtures. Real conversation archives, attachments, avatars, account
exports, and credentials stay outside Git and are never used as test fixtures.

## Safety boundary

Do not add user-token extraction, self-bot behavior, unattended Discord crawling,
or code that submits actions to a Discord account. Import only files the user
has explicitly supplied or sources that are authorized for the workflow.

## Development commands

From this directory on Windows PowerShell:

```powershell
$env:PYTHONPATH = "src"
python -m discord_archive validate fixtures/sample/archive.json
python -m discord_archive build fixtures/sample/archive.json --output dist/sample
python -m discord_archive verify dist/sample
python -m discord_archive import-transcript --input C:\path\to\transcript.json --output private-data\conversation.json
python -m discord_archive merge-transcripts --input private-data\range-001.json --input private-data\range-002.json --output private-data\merged-transcript.json
python -m discord_archive verify-coverage private-data\merged-transcript.json
python -m unittest discover -s tests -v
```

## Viewer rules

- The generated archive must work without a network connection.
- Keep all runtime code dependency-free and accessible.
- Preserve the normalized schema and provenance fields when adding importers.
- Official package imports expose skipped-record and unreadable-file diagnostics
  in `metadata.source.import_summary`; do not silently discard source issues.
- Keep transcript rendering bounded for large archives, while preserving
  deep-link selection and full current-view print/PDF output.
- `build` must emit a portable `manifest.json`; keep `verify` useful for
  detecting changed or missing generated files and local assets.
- Use synthetic or redacted data in documentation and tests.

## Attended browser capture

The repository may include a read-only visible-DOM adapter for a user-opened
direct message when the user explicitly controls both the source and the
capture. Such an adapter must remain foreground/user-triggered and scoped to
the current `/channels/@me/<channel-id>` conversation. It must not automate
login, search arbitrary accounts, inspect cookies/tokens, send messages, call
Discord's private API, download remote media, or run as a background crawler.
Capture output is private data and belongs under an ignored path such as
`private-data/`.

Long DM capture is range-based: the user scrolls the open conversation, captures
overlapping DOM windows, and runs `merge-transcripts`. The merge may report
`verified` only for an overlap-linked chain with oldest/newest boundaries
observed or explicitly attested; it must never become an unattended scroller or
background crawler. Coverage reports must include a concrete next action when
they are incomplete, and the viewer must make partial capture state prominent.

Remote image materialization is a separate explicit command, not part of
capture. It requires `--allow-remote`, follows only URLs already present in the
archive, and is restricted to Discord CDN hosts so offline builds receive local
image files without introducing discovery or crawling behavior.
