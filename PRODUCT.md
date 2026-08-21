# Product

<!-- impeccable:product-schema 1 -->

## Platform

web

## Users

The primary user is the owner of personal conversation archives. They use the
tool on a desktop computer to inspect a private conversation after leaving the
original chat application, including when working offline.

This audience and workflow are inferred from the project kickoff and remain
open to refinement.

## Product Purpose

Turn a user-supplied conversation transcript or account data export into a
portable, searchable, offline-readable archive that preserves message text,
timestamps, participants, profile images, attachments, and other available
metadata.

Success means an archive can be opened without the original chat application or
an internet connection and still feels faithful to the source conversation.

## Positioning

The tool separates acquisition from presentation: it produces a deterministic
local archive and viewer from permitted input files instead of making the
viewer depend on a live service, external assets, or a particular chat client.

## Operating Context

- Archives contain sensitive personal conversations and should remain local or
  encrypted. Source conversations are not project fixtures.
- The initial implementation runs on Windows from a local repository and
  produces static files that can be opened or served locally.
- The first fixture is synthetic. Real Discord data will be handled as an
  explicitly supplied input rather than embedded in the repository.

## Capabilities and Constraints

- The archive model must support direct messages and conversation-like threads.
- Messages may include authors, display names, profile images, timestamps,
  edits, replies, reactions, embeds, links, and attachments.
- Timestamps are stored in UTC and rendered in a selectable local timezone.
- All assets needed for offline viewing should be copied into the archive when
  the input provides them; external CDN URLs are provenance, not a runtime
  dependency.
- The viewer must not require Discord, a network connection, a token, or a
  background service.
- Capture is intentionally separated from the archive engine. The project
  will not depend on extracting account tokens or running an unattended
  user-account crawler. Full two-sided capture remains an open input-source
  question.

## Brand Commitments

None established. The first viewer may establish a durable visual language,
subject to later user review.

## Evidence on Hand

The kickoff example establishes the desired basic reading unit:

    [PFP] User1 11/29/2018 11:52PM
    Good one

No real conversation data or production brand assets have been provided.

## Product Principles

1. Preserve source truth and provenance.
2. Make private archives portable without making them public.
3. Keep the viewer calm, fast, searchable, and readable at conversation scale.
4. Degrade gracefully when an input lacks avatars, media, or rich metadata.
5. Treat acquisition as replaceable so the archive format remains useful.

## Accessibility & Inclusion

The viewer should support keyboard navigation, visible focus, readable contrast,
reduced motion, semantic landmarks, alt text for avatars and attachments, and a
layout that remains usable on narrow screens. These are implementation
requirements inferred from the offline viewer brief.
