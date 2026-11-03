# ICK future Ready and exact Facebook/X copy

This is a local implementation candidate. Installing it does not approve any
live item, create a schedule, change a destination, or send a provider request.
The existing reviewed ICK candidate set contains 309 unchanged public copies;
all 309 exceed the conservative X 280-weight limit when their exact title,
description and public hashtags are composed. None is made eligible by this
code or by a metadata-only review marker.

## Copy and approval

`GET /api/ick-readiness/items/{id}/copy-preview` reports the exact X composition,
conservative Unicode weight and actionable unsupported-copy errors. It does
not inspect media, certify rights or mutate the queue. The shared adapter
already supplied title and description through `x_title` before this change;
the new behavior is the deterministic queue preview, explicit hashtag union,
variant binding, and guarded future transition.

Reviewed title and description bytes stay intact. Only appended X hashtags
are deduplicated, using Unicode NFC and case folding. The original reviewed
Facebook caption and title are retained. Explicit legacy `#hashtag` strings,
JSON hashtag lists, and a structured object with `public_hashtags` and
`phrase_tags` are supported. Backend keyword phrases remain whole and are
excluded from public copy. Ambiguous bare phrase lists reject; no keyword
guessing, truncation, automatic rewriting or threading occurs.

An over-budget common caption requires a separately owner-reviewed exact
X-only variant through `POST /items/{id}/x-variants/review`. The authenticated
request supplies its exact text hash, reviewed revision, workbook and proposal
hashes, actual approval reference and explicit decision. The immutable receipt
binds the current queue identity, metadata approval and unchanged common copy.
The text digest uses the existing `pending_metadata.digest(text)` canonical-JSON
SHA256 convention; it is not a hash of an arbitrarily reformatted text file.
The separately reviewed ICK X-only variant must retain `#tonightsconversation`
and `#theickroom`. It may omit optional Facebook hashtags under the authorized
editorial optimization; Facebook and backend tags remain unchanged. The default
X composition still preserves all reviewed public hashtags. Agent editorial QA
under the user's existing instruction is valid provenance; the receipt must not
claim the human manually reviewed specific wording or hashes when that did not
occur. The protocol does not require an additional manual approval round.
The statement records supplied owner approval; it does not independently prove
rights or semantic accuracy. Callers must never invent review references.

## Per-item sequence

All routes below require the normal owner access token. There is no bulk Ready
route and no new frontend approval control in this candidate. Refused generic
queue writes do not wake the poster.

1. Obtain the full current pending-metadata fingerprint and the latest exact
   public-copy approval receipt. The item must be unscheduled, never submitted
   ICK Review with no publication history, pin or Post Next assignment.
2. `POST /api/ick-readiness/items/{id}/inspect` with an operation UUID, expected
   fingerprint and approval receipt ID. The server reads the actual retained
   local/Drive bytes, completely decodes the media and records its checksum,
   timeline, codec and thumbnail proof. The baseline is MP4/H264/yuv420p,
   AAC-LC mono/stereo, at most 140 seconds and 512 MiB. An unsupported or
   unavailable source holds; there is no silent transcode. Drive identity,
   version, size and checksum are rechecked. Studio/native sources retain their
   existing workflows.
3. Record an actually reviewed X variant if needed. Its receipt is optional
   only when the unchanged common copy already fits and validates.
4. `POST /items/{id}/preview` with the exact fingerprint, approval/media
   receipts, optional X-variant receipt, explicit supported destination IDs and
   requested Facebook Page ID. Only the existing `TheIckRoom:facebook` and
   `TheIckRoom:x` targets are supported. Current native identities, accessible
   Page, Page pins and connection are read without changing account settings.
5. Review the complete returned document, including exact platform payloads,
   frozen schedule, native identities and concrete destinations. `POST
   /items/{id}/ready` uses the same request plus its exact preview SHA256.
   A stale snapshot, competing reservation or missing evidence rejects.

The Ready transaction holds the publication lease, media claim and shared DB
mutex; it changes only status and scheduled time using full compare-and-swap.
It reserves a cadence-compatible slot strictly in the future, after existing
ICK assignments and retained reservations. It does not change account selection,
timezone, cadence or the existing Ready set. Existing unscheduled ICK Ready
items cause a hold requiring separate reconciliation rather than a hidden
replan. Repeating the exact operation returns its immutable historical receipt;
that replay is not a fresh statement of current eligibility.

## Dispatch and missed slots

The guarded route is outside generic queue-wake middleware and never wakes the
poster. Generic ICK Review-to-Ready approval paths reject with the guarded-route
instruction. Normal scheduler ticks preserve guarded slots and do not allocate
the same slot to another ICK item. A release can dispatch only through the
regular scheduler in its original five-minute window. Manual current-slot
dispatch, catch-up and stale approval/media/identity/schedule are refused.

Before the atomic claim and each per-destination intent, the publisher rechecks
the complete release, actual media, reviewed payload and fresh native target
identity. The transport uses the same frozen fields as the preview; Facebook
keeps its full caption while X receives its selected exact `x_title`. A changed
target between destinations blocks the next request. Accepted, partial and
uncertain attempts retain their publication history and cannot be blindly sent
again. Publication history added after Ready also blocks the initial dispatch.
An unsuccessful atomic claim rolls back its one-use dispatch permit.

`GET /receipts/{operation_id}` exposes an expired reservation as
`expired_no_catchup`. Recovery is explicit: the owner conditionally calls
`POST /items/{id}/withdraw` with the exact release ID, current fingerprint,
actual reason and withdrawal decision. Only an unsent current Ready reservation
can return to Review. History, uncertainty or an active publisher prevents
withdrawal. The old slot remains reserved; a fresh reviewed request and preview
are required to reserve another strictly future slot. No automatic rollover or
silent retry is implemented. Other protected changes require reconciliation.

SQLite and PostgreSQL install an additive status-write fence keyed to retained
ICK queue identity. Old runtimes cannot set such a row to posting without its
one-use permit, even after a pipeline change. Stop publishers before upgrade or
rollback: a SQL fence cannot recall an already in-flight HTTP request or stop
legacy code that sends without a DB status write. Keep the fence and immutable
receipts on rollback. SQLite's millisecond identity comparison can conservatively
hold same-millisecond ID reuse; it cannot relax the original item's fence.

## Validation boundaries

Regression tests use generated small original media, actual decode, actual
SQLite/PostgreSQL transactions and mocked native/provider HTTP. They exercise
atomic Ready, competing slots, stale input, old-publisher fences, missed-slot
withdrawal, exact multipart copy and partial-send refusal. They do not constitute
a real provider upload, live Ready operation, human content review or rights
certificate. No live approval, schedule, publish, deployment or archive deletion
is part of this implementation candidate.
