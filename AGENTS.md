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
python -m discord_archive build-catalog --input private-data\conversation-a.json --input private-data\conversation-b.json --output private-data\concordance-library
python -m discord_archive verify dist/sample
python -m discord_archive verify-catalog private-data\concordance-library
python -m discord_archive export-evidence --input private-data\conversation.json --output private-data\conversation-evidence.json --session private-data\capture-session.json
python -m discord_archive verify-evidence private-data\conversation-evidence.json
python -m discord_archive import-transcript --input C:\path\to\transcript.json --output private-data\conversation.json
python -m discord_archive capture-session init --output private-data\capture-session.json
python -m discord_archive capture-session add --session private-data\capture-session.json --input private-data\range-001.json
python -m discord_archive capture-session status --session private-data\capture-session.json
python -m discord_archive capture-session next --session private-data\capture-session.json
python -m discord_archive capture-session attach-evidence --session private-data\capture-session.json --capture private-data\range-001.json --dom private-data\range-001.html --screenshot private-data\range-001.png
python -m discord_archive audit-media --input private-data\conversation.json --output private-data\conversation-media-audit.json
python -m discord_archive capture-session finalize --session private-data\capture-session.json --output private-data\merged-transcript.json
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
- `build` must emit portable `manifest.json` and `evidence.json` files; keep
  `verify` useful for detecting changed or missing generated files, local
  assets, and evidence records.
- `build-catalog` must emit a local launcher, metadata-only catalog index, and
  integrity manifest; `verify-catalog` must validate every linked viewer.
- `export-evidence` must emit only archive metadata, coverage boundaries,
  relative source references, and hashes; it must not duplicate message bodies
  or expose absolute paths. `verify-evidence` must detect changed or missing
  archive files, required local assets, and linked capture sessions. A session
  may refer to a finalized transcript that was later normalized into a
  different archive filename; preserve and verify that relationship instead of
  rejecting the evidence report.
- Use synthetic or redacted data in documentation and tests.
- CI and release helpers must use only the synthetic fixture; private archives,
  downloaded media, password files, and encrypted bundles stay outside Git.

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
overlapping DOM windows, and runs `merge-transcripts`. The browser-control
workflow may perform one explicitly requested `older` or `newer` viewport step,
wait for the virtualized range to settle, and then invoke the read-only adapter
for that single overlapping window; it must never loop through history
unattended. The merge may report `verified` only
for an overlap-linked chain with oldest/newest boundaries observed or explicitly
attested; it must never become an unattended scroller or background crawler.
Coverage reports must include a concrete next action when they are incomplete,
and the viewer must make partial capture state prominent.
`capture-session next` is a plan-only helper: it may recommend one bounded
older/newer browser step, but it must never scroll, crawl, or capture by itself.
Safe-share helpers must redact before bundling, must not mutate the source
archive, and must keep password material out of command output and Git.
The optional `capture-session` workflow records a private, same-directory manifest
of relative capture paths, SHA-256 hashes, channel consistency, boundaries, and
coverage status. Session manifests must remain under ignored private paths and
must not embed message payloads, absolute source paths, credentials, cookies, or
tokens.
Sessions may also record explicit `expected_dates` checkpoints. These compare
against canonical UTC dates and preserved visible Discord calendar dates; a
checkpoint is satisfied only when a captured message has that date, and missing
checkpoints must keep coverage incomplete. This is an audit aid, not evidence
that unrendered or deleted messages existed.
Rendered DOM and screenshot evidence may be attached to a tracked range. The
session verifies those files and `export-evidence --session` includes them
automatically; because they can contain message content, they remain private.
When Discord exposes a localized visible timestamp label, captures may preserve
it under `message.source_display` alongside the canonical UTC timestamp. The
viewer may use that source label for its default display mode, but must keep raw
UTC and explicit local/UTC rendering available.

Remote media materialization is a separate explicit command, not part of
capture. It requires `--allow-remote`, follows only URLs already present in the
archive, and is restricted to Discord CDN hosts so offline builds receive local
media files without introducing discovery or crawling behavior.
`audit-media` is read-only: it reports missing local paths and remote hosts but
does not download, rewrite, or canonicalize an archive.

Safe-share redaction must never overwrite its source archive. It must remove or
replace message content, source identifiers, links, avatars, and media before a
bundle is exported. Encrypted bundles must use the optional `cryptography`
dependency with an authenticated cipher; passwords must come from a prompt or
private file, never a command-line argument or committed fixture.
