# Capturing a Discord DM

How to capture a DM you have opened yourself in Discord Web, track a multi-range
capture session, and merge virtualized ranges into one verified transcript. Then
follow the [usage guide](USAGE.md) to build and share the archive.

## Capture one open DM

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

## Track a guided capture session

For a long or virtualized DM, a private capture session makes the range workflow
repeatable. Initialize it beside the capture JSON files, then add each range after
you have visibly scrolled the open DM yourself. Dates are optional: when no dates
are known, completeness is established from an overlap-linked chain plus both
observed scroll boundaries. If dates are known, add them as extra checkpoints;
they can only prevent a false complete result, not prove that unrendered or
deleted messages existed:

```powershell
$env:PYTHONPATH = "src"
python -m concordance capture-session init `
  --output private-data\conversation-session.json `
  --title "Conversation title" `
  --expect-date 2024-01-15 `
  --expect-date 2024-06-30

# In the already-open Discord DM, scroll to a rendered range and run the
# read-only tools/discord_visible_capture.js adapter in that visible context.
python -m concordance capture-session add `
  --session private-data\conversation-session.json `
  --input private-data\range-001.json
python -m concordance capture-session status `
  --session private-data\conversation-session.json
python -m concordance capture-session next `
  --session private-data\conversation-session.json

# Build a message-free local guide with the next action, overlap ledger,
# boundary attestations, expected dates, and media-host diagnostics.
python -m concordance capture-session dashboard `
  --session private-data\conversation-session.json `
  --output private-data\conversation-capture-guide
python -m concordance capture-session verify-dashboard `
  private-data\conversation-capture-guide
Start-Process (Resolve-Path private-data\conversation-capture-guide\index.html)

# Add checkpoints later without restarting the session. Use --replace to
# replace the existing list, or --replace with no dates to clear it.
python -m concordance capture-session checkpoints `
  --session private-data\conversation-session.json `
  --expect-date 2024-01-15 `
  --expect-date 2024-06-30

python -m concordance capture-session finalize `
  --session private-data\conversation-session.json `
  --output private-data\merged-transcript.json `
  --reached-start `
  --reached-end
python -m concordance import-transcript `
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

## Capture a virtualized DM in ranges

Discord may keep only a window of a long conversation in the DOM. For complete
coverage, capture several overlapping ranges while you scroll the open DM
yourself. Each capture records its oldest/newest message IDs and whether the
Discord message scroller was at its oldest or newest boundary:

```powershell
$env:PYTHONPATH = "src"
python -m concordance merge-transcripts `
  --input private-data\range-001.json `
  --input private-data\range-002.json `
  --input private-data\range-003.json `
  --output private-data\merged-transcript.json `
  --reached-start `
  --reached-end `
  --expect-date 2024-01-15 `
  --expect-date 2024-07-01
python -m concordance verify-coverage private-data\merged-transcript.json
python -m concordance import-transcript `
  --input private-data\merged-transcript.json `
  --output private-data\conversation.json
python -m concordance build private-data\conversation.json --output private-data\conversation-view
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
