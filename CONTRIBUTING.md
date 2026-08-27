# Contributing to Concordance

Concordance is a local-first, source-only project. Contributions should keep
real Discord conversations, profile data, downloaded media, screenshots, DOM
evidence, credentials, and encrypted bundles outside Git.

## Development setup

Concordance supports Python 3.11, 3.12, and 3.13. From a source checkout on
Windows PowerShell:

```powershell
python -m pip install -e ".[secure]"
python -m unittest discover -s tests -v
python scripts/release_check.py
```

The viewer and browser adapters are dependency-free JavaScript. Check their
syntax before opening a pull request:

```powershell
node --check viewer/catalog_app.js
node --check viewer/capture_app.js
node --check tools/discord_visible_capture.js
node --check tools/discord_visible_evidence.js
```

## Change guidelines

- Use the synthetic fixture or fully redacted data in tests and documentation.
- Preserve schema versions, provenance, stable IDs, timestamps, and explicit
  missing-data states.
- Keep capture attended and read-only. Do not add token extraction, self-bots,
  unattended crawlers, credential collection, or actions that write to Discord.
- Keep remote media materialization explicit, allowlisted, and limited to URLs
  already present in a user-authorized archive.
- Add or update tests and the changelog for behavior changes.
- Run the release preflight before requesting review.

## Pull requests

Explain the user-visible behavior, the source of any new fixture data, and the
verification performed. Do not include private archives or generated viewer
directories in a pull request. Security-sensitive reports should follow
[SECURITY.md](SECURITY.md) instead of being disclosed in a public issue.
