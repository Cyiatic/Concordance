# Design direction: Discord-like Offline Reader

## Direction contract

**THESIS:** Make an archived DM immediately legible to someone who knows
Discord, while keeping the viewer visibly read-only and honest about what was
captured.

**OWN-WORLD:** Discord-familiar dark surfaces, a server rail, DM sidebar,
channel header, avatar-led message rows, and restrained archive-blue accents.
This is a familiar shell around a primary-source record, not a pretend live
client.

**STORY:** The reader understands what archive is open, who spoke, when each
message occurred, and where its supporting assets came from. A local-only badge
and provenance drawer make the archive boundary explicit.

**FIRST VIEWPORT:** A slim app rail, DM list, channel header, and message
timeline establish the Discord mental model immediately. The right-hand
details panel reads like a profile drawer but carries archive provenance,
timestamps, IDs, and source notes.

**FORM:** A dark, operational conversation reader; selecting a message reveals
its provenance without leaving the reading flow.

## Durable visual rules

- Mode: Operate. Familiar controls and scanability beat ornament.
- Palette: Discord-like charcoal layers; blurple marks focus and selection,
  green marks local/offline state, and cyan marks links.
- Typography: system sans for all interface and message text; monospace only
  for IDs, timestamps, and technical provenance.
- Layout: narrow app rail, DM sidebar, central transcript, optional right
  provenance drawer; collapse the sidebars structurally on narrow screens.
- Messages use Discord-like rows rather than speech bubbles. Avatars are local
  assets, circular, and always have useful alt text.
- Motion is limited to 150–200ms state transitions and respects reduced motion.
- Every archive must remain legible with missing avatars, media, or rich embeds.

## Settled implementation tokens

- `--server: #1e1f22`, `--sidebar: #2b2d31`, and `--main: #313338` define the
  Discord-like app, DM, and transcript layers.
- `--ink: #f2f3f5` and `--ink-soft: #b5bac1` carry primary and supporting text;
  `--accent: #5865f2` marks selection and focus, while `--green: #23a559`
  marks local-only state.
- The central transcript is a dark reading surface. Date dividers establish
  chronology; message rows carry the avatar, author, timestamp, content, and
  optional evidence below it. Capture adapters may mark Discord-style grouped
  continuations so repeated author chrome collapses while each timestamp stays
  addressable on hover/focus.
- DM identity keeps the visible display title and handle separate from
  participant records. A participant can retain both a local avatar path and
  the original remote avatar reference; the viewer uses only the local path.
- Timestamp rendering uses the archive's captured display timezone by default,
  with explicit local and UTC modes available beside the transcript.
- Clicking a timestamp selects a message, updates `#message=<id>`, and fills
  the provenance rail with displayed time, raw UTC, ID, attachments, and source
  link. Search and author filtering preserve that reading model.
- The generated viewer emits `index.html`, `app.js`, `archive.json`, a
  `manifest.json` integrity record, and local assets so strict offline/browser
  policies do not block the executable viewer and copied archives can be
  checked.
