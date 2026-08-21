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
python -m unittest discover -s tests -v
```

## Viewer rules

- The generated archive must work without a network connection.
- Keep all runtime code dependency-free and accessible.
- Preserve the normalized schema and provenance fields when adding importers.
- Use synthetic or redacted data in documentation and tests.
