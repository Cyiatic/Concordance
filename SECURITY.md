# Security policy

Concordance handles potentially sensitive conversation archives and is designed
to remain local-first. The repository contains only source code and synthetic
fixtures; real archives, media, DOM evidence, screenshots, credentials, and
password files must stay outside Git.

## Supported release line

The current `0.1.x` line is an Alpha release and receives security fixes when
the issue affects the supported local archive workflow.

## Reporting a vulnerability

Please do not include Discord tokens, passwords, cookies, private conversation
content, personal profile data, or live exploit material in a public issue or
pull request. Use GitHub's private vulnerability reporting or security advisory
flow for [Cyiatic/Concordance](https://github.com/Cyiatic/Concordance) when it is
available. If that private channel is not enabled, contact the maintainer
through the [Cyiatic GitHub profile](https://github.com/Cyiatic) and request a
private reporting channel.

Include only sanitized details: the affected version or commit, the affected
file or command, reproducible steps using synthetic data, impact, and a
possible mitigation. We will acknowledge valid reports, assess severity, and
coordinate a fix or mitigation before public disclosure when practical.

## Security boundaries

- Acquisition is limited to official exports, user-supplied files, and attended
  captures of a conversation the user has already opened and is authorized to
  access.
- Concordance never needs Discord passwords, user tokens, cookies, browser
  storage, self-bots, unattended crawlers, or Discord write access.
- Remote media download is opt-in, follows only URLs already recorded in an
  archive, and uses an allowlist of approved hosts.
- Safe sharing must redact into a new output and may use authenticated
  encryption; source archives and password material are never overwritten or
  committed.
- Generated viewers are offline-readable and should not load third-party code
  or external assets by default.
