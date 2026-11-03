# Drama controls and Facebook/X delivery

This release installs guarded operations; it does not approve live copy, make
queue items Ready, apply a schedule, change targets, or submit a canary.
The SEO coordinator owns those live operations. Other pipelines retain their
existing schedules and approval/source requirements.

## Delivery fixes

Video uploads now explicitly send `facebook_media_type=VIDEO`,
`facebook_title`, and `facebook_description`. This selects the provider's normal
Page-video route rather than its default Reels route. Original file bytes are
uploaded intact. Existing published posts are never edited or reposted.

X receives the visible title, description and public hashtags through `x_title`.
It does not rely on the shared description field, which the provider ignores
for X. Unknown entitlement uses a conservative 280-weight caption baseline;
URLs the provider would strip and unsupported control characters are held
before a per-platform intent or request. No automatic shortening, hashtag
removal, threading, or Premium assumption occurs. A guarded Drama review can
bind a separately approved exact X variant while preserving public hashtags.
Drama media beyond the unverified 140-second/512-MiB baseline also requires a
separate verified entitlement contract. These conservative holds are not claims
that every account or posting path has those media limits.

New transport receipts bind the actual visible platform fields. Reported
provider changes/warnings hold the delivery for review without resubmitting an
already accepted destination. A transcode flag alone is not proof of trimming.
This new handling does not reinterpret historical unmarked receipts.

Contracts: [provider upload parameters](https://docs.upload-post.com/api/upload-video/),
[visible-text limits and URL stripping](https://docs.upload-post.com/resources/character-limits/).

## Guarded owner operations

All routes below require the existing application token. Operation IDs are UUIDs;
receipts are immutable and replay returns the historical saved result without a
second edit. Changed requests with the same ID reject. Queue fingerprints cover
public and protected state, including immutable source binding.

| Route | Purpose |
| --- | --- |
| `GET /api/drama/contract` | Current capabilities, limits and activation blocker. |
| `GET /api/drama/cutover-status` | Read-only current rows, live publication leases, late receipts and retained-history inventory. Not a release certificate. |
| `GET /api/drama/items/{id}` | Exact state/fingerprint and server evidence/review IDs. |
| `POST /api/drama/items/{id}/inspect` | Verify a complete source-marked canonical archive inventory and actual full decoded Drive bytes. |
| `POST /api/drama/items/{id}/review-preview` | Preview exact canonical episode, current copy, revision/hashes, explicit targets and platform payload; produces acknowledgment digest. |
| `POST /api/drama/items/{id}/review` | Record acknowledgment of that unchanged exact preview. |
| `POST /api/drama/items/{id}/ready` | Reverify live Drive media and predecessors; conditional Review-to-Ready only, without scheduling or activation. |
| `POST /api/drama/plan-preview` | Read-only future alternating-slot preview. |
| `POST /api/drama/plan` | Currently rejects: delivered-media/copy verification is unsupported. No client flag or environment bypass exists. |
| `GET /api/drama/receipts/{operation_id}` | Inspect a committed historical receipt. |
| `POST /api/drama/receipts/{operation_id}/rollback` | Reverse only supported recorded Ready/plan fields when identity, full state, history and plan still match. Never restore a past slot. |

The review names an approved revision, workbook hash and exact proposal hash;
technical inspection does not certify rights. Original Studio/native/export
gates remain applicable. Metadata approval context does not widen authority for
an immutable source-bound title: revise through its genuine original workflow.

Two canonical series have independent lanes, four new episodes each per local
day, eight total. Future slots alternate A/B at 10:00, 11:30, 13:00, 14:30,
16:00, 17:30, 19:00, 20:30 America/New_York. Start with EP1 and require every
canonical predecessor confirmed on both reviewed destinations with delivered
fidelity before the next episode. No missing slots, later islands, automatic
third-series rollover, quota transfer, catch-up bursts or silent day refills.
Retries/callbacks retain the same canonical episode's quota unit across midnight
and restart. Unknown outcomes hold; confirmed failures retry only the owed
destination. A blocked lane does not spend the healthy lane's quota.

Existing `Dramarama:*` selections remain stored unchanged. An explicit new review
can freeze only Facebook and X from a fresh connection read; its acknowledgment
binds that pair. A newly connected YouTube account is excluded. Legacy wildcard
rows without this reviewed target snapshot cannot dispatch. Connection alone
does not approve a new destination.

## Remaining delivery proof and cutover

`activation.supported=false` and `delivery_fidelity_verified=false` are deliberate
server-side holds. A provider accepted/completed result, public URL, echoed
caption or synthetic transport test does not establish actual delivered media
and visible copy. There is currently **no supported deployed positive fidelity
receipt/verifier**. Do not claim an operational autonomous Drama schedule.

Before enabling it, implement and test an independently read proof binding:
queue/canonical episode identity, exact source hash/duration/audio, reviewed
payload/hash and target identity, provider request and native post IDs, observed
published duration and complete visible copy, checked time, and the supported
readback method. Unknown, shortened, missing-audio or changed-copy results must
hold the lane without resubmitting already accepted posts. A client checkbox or
provider copy echo must never substitute for that proof.

Root/SEO own any separately coordinated bounded live canary: one approved first
episode, one submission per approved Facebook/X destination with stable request
IDs, approved exact platform copy, verified Page/pinned target and X identity.
Use >90-second source media when eligible to test the affected Facebook path;
keep X within verified account media/text entitlements. Record provider/native
IDs once, then independently inspect the actual served beginning/middle/tail,
duration, audio and expanded public text. Preserve failures and uncertain
receipts; never automatically delete/repost, thread or advance the series.
If either readback is inaccessible, record it as unknown and retain the hold.
A canary alone is not an implementation of per-episode verification.

Cutover must reconcile full retained history and exact live publication leases,
and quiesce old Drama publishers. A database one-use permit atomically binds new
Drama intent and claim. Keep that built-in SQL fence on source rollback; an old
v1 runtime can continue unrelated brands but cannot claim Drama without it.
No fence can recall a provider request already in flight. Do not restore an old
database over newer receipts or drop guards to make rollback appear successful.

## Verification evidence

Tests use real original synthetic H264/AAC media and full FFmpeg decode with
fake Drive/provider transport only. The 150-second multipart test verifies
complete bytes/duration/audio at our HTTP boundary, not a real platform upload.
The suite covers canonical gaps/aliases/season identity, stale/external edits,
same-operation replay, independent process ownership, parallel reconcilers,
partial failure/uncertainty, independent lanes, quota, midnight/DST/restart,
conditional rollback, retained publication history and old-source cross-brand
compatibility. Production capability holds are explicitly tested separately
from synthetic internal state-machine tests that replace that boundary.

Private workbooks, queue IDs, account credentials, real copy and source evidence
remain outside Git. CI artifacts and private release receipts identify the exact
source tested. No fixture success is a live delivery certificate.
