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
| `GET /api/drama/published-media/{facebook or x}` | One bounded native media-list page, sanitized without temporary CDN URLs; actual available fields/caption/ID, no upload-history echo. |
| `GET /api/drama/items/{id}/delivery-readback` | Match exact journal-attributed provider post URL to a native post ID/caption, canonical source and reviewed payload; decode bounded Facebook CDN video when available. Partial observations only. |
| `GET /api/drama/items/{id}` | Exact state/fingerprint and server evidence/review IDs. |
| `POST /api/drama/items/{id}/inspect` | Verify a complete source-marked canonical archive inventory and actual full decoded Drive bytes. |
| `POST /api/drama/items/{id}/review-preview` | Preview exact canonical episode, current copy, revision/hashes, explicit targets and platform payload; produces acknowledgment digest. |
| `POST /api/drama/items/{id}/review` | Record acknowledgment of that unchanged exact preview. |
| `POST /api/drama/items/{id}/ready` | Reverify live Drive media and predecessors; conditional Review-to-Ready only, without scheduling or activation. |
| `POST /api/drama/plan-preview` | Read-only future alternating-slot preview. |
| `POST /api/drama/plan` | Currently rejects: delivered-media/copy verification is unsupported. No client flag or environment bypass exists. |
| `GET /api/drama/receipts/{operation_id}` | Inspect a committed historical receipt. |
| `POST /api/drama/receipts/{operation_id}/rollback` | Reverse only supported recorded Ready/plan fields when identity, full state, history and plan still match. Never restore a past slot. |

## Separate one-shot EP1 canary

The installed one-shot path is independent of the held automatic plan. Deploying
it creates **zero grants**. Generic manual publishing, Ready, scheduler ticks and
environment flags cannot acquire its dispatch context. Only the existing owner
token can invoke its dedicated operations; root/SEO retain live writer ownership.

| Route | Purpose |
| --- | --- |
| `GET /api/drama/canary/contract` | Installed policy and counts of granted, consumed and revoked operations; never authority to publish. |
| `GET /api/drama/canary/targets?facebook_page_id=...` | Read the specific native Page/access/pin and native X identifier/handle. No account or Page selection write. |
| `POST /api/drama/canary/items/{id}/preview` | Read-only exact proposal using the existing canonical inspect/review/Ready receipts. `authorization:null` permits a preview without inventing rights. |
| `POST /api/drama/canary/items/{id}/grants` | Record the unchanged acknowledged proposal and actual specific owner authorization/rights references. Does not dispatch or edit the queue or schedule. |
| `GET /api/drama/canary/grants/{grant_id}` | Historical grant, source/copy/target/slot binding, revocation/expiry and original dispatch receipt. |
| `POST /api/drama/canary/grants/{grant_id}/revoke` | Append cancellation, including during transport. Stops an unsent destination when checked; cannot recall a request already sent or undo consumption. |
| `POST /api/drama/canary/items/{id}/dispatch` | Explicitly consume that grant once through the existing durable publisher and SQL fence, in its exact time window. |

A proposal binds the full queue fingerprint; exact inspect, review and Ready IDs;
the two canonical series in approved A/B order; A's EP1 source SHA256/Drive/catalog
evidence; reviewed visible Facebook and X payloads and payload SHA256; exact native
Page and X account IDs/handle; and a future 10:00 America/New_York slot within
48 hours. The row must be guarded Ready with no ordinary schedule, pin, Post Next
or extra unbound thumbnail/media. The second series needs its own EP1 evidence.
Connected YouTube remains excluded, including when the stored selection is a
legacy wildcard. Unknown X entitlement still enforces the conservative media/text
bounds above. No copy is shortened automatically.

Grant requests add `acknowledged_fingerprint` from a preview containing the exact
actual authorization statement, plus the authorizer, specific authorization
reference, exact source and public-payload SHA256, rights basis and rights
reference. These are recorded owner statements, not an AI rights certificate.
Broad rollout/deployment approval or a null/blank rights reference cannot grant.
The required decision is:

> I specifically authorize ONE public EP1 canary to the exact reviewed Facebook Page and X account at this future slot, and confirm rights for this exact source and copy

The native read uses the supported read-only [user profile](https://docs.upload-post.com/api/user-profiles/)
and [current Page/pin](https://docs.upload-post.com/api/facebook-page/) endpoints.
Both responses must prove the same requested pin; missing, null or contradictory
provided pin fields reject. The requested Page must be accessible exactly once.
An OAuth person's display name is not the Page, and an X display label/handle
cannot replace the documented stable native account identifier. Evidence gaps
are holds, not assertions that authorization was lost. No pin is changed to make
a request pass. Identity is read again before the atomic batch claim and each
destination. Canonical bytes are downloaded, hashed and reverified before claim.

Dispatch requests supply a new operation UUID, `expected_fingerprint`, `grant_id`
and `grant_sha256`. The five-minute window has no catch-up/automatic renewal.
The atomic transaction consumes the grant, records the batch intent and uses the
existing one-use database permit before provider HTTP. Per-destination request
keys are deterministic for that operation/source/payload/target binding. A fresh
atomic destination check rejects revoked/stale/copy/source/target changes before
its HTTP request. Already accepted or unknown destinations are never resent.
Same-operation replay returns its committed historical receipt without another
provider read or upload, including from another process after restart. Another
dispatch operation cannot spend a consumed grant. A crash after durable claim
retains uncertain evidence rather than assuming permission to send again.

The first grant freezes this server's one-shot scope to that exact source,
series pair and native destinations. An unconsumed grant can be revoked and
replaced only within that frozen scope; a consumed grant cannot be renewed, even
after revocation. This is at most one request to each of Facebook/X, not a route
for additional episodes, destinations or unbounded probes. Native platform
requests already in flight cannot be recalled by a later local revocation.

Inspect original provider/native IDs through the saved dispatch receipt and
`GET /api/queue/{id}/publication-evidence`. Known terminal outcomes return the
canary to Review with a delivery hold; uncertain outcomes retain Posting. Neither
counts as delivered fidelity or EP1 completion. EP2 and the normal eight-slot
automatic plan remain blocked, even when both provider requests report success.

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

The partial reader uses the documented native
[media-list endpoint](https://docs.upload-post.com/api/instagram-media/), one page
of at most25items. It binds exact observed permalink/native ID and caption to a
journal-attributed provider request, immutable canonical source hash/duration and
reviewed platform payload hash. It never substitutes an upload-history caption.
For Facebook it can download one provider-returned HTTPS fbcdn.net playable video
up to256MiB, refuse redirects, fully decode it, measure duration/audio streams and
hash it, then remove the temporary copy. For X the documented video media URL is
only a preview image; it is never fetched or presented as video proof. The
normalized native schema has no separate Facebook title or X video duration/
audio fields. Stream presence alone does not verify audio content. These missing
proofs are reported explicitly and cannot activate or advance a lane.

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
The separate one-shot grant above provides that bounded transport path, subject
to actual source/copy/target/slot-specific owner authorization and rights
confirmation. Those are still absent for the proposed live test. No grant,
Ready transition or public dispatch is performed by this code/test release task.
Do not toggle global activation or generic Ready to run a probe.

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
