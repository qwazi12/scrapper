# Retained Studio sources at queue admission

LongForm and Countdowns send operations retain content-addressed media and the
exact script, facts, inputs, measurements and owner acceptance. The queue uses
those retained files. Replacing a working render cannot silently replace the
queued video. A new append-only QueueSourceBinding links queue creation identity
and revision to its retained main export, Countdown segments, policy and exact
transport copy. There is no title-based or numeric-ID-only adoption.

Dispatch checks current typed factual rules against the retained source,
accepted export fingerprint, complete Countdown segment fingerprints/category
evidence and media hashes before recording intent. It holds the queued media
claims through submission. The atomic SQL claim compares creation identity,
binding revision, source, pipeline, research, copy, media and selected accounts.
A concurrent revision or changed source metadata rejects admission before a
provider call. Intent, per-account results and late receipt evidence carry the
captured binding ID even if publication ownership is revoked during transport.
Existing unknown-result/late-receipt recovery takes precedence over retries.

Original-Studio and Native queue copy, AI rewrites and cover changes require a
revised source and a new explicit send. Destinations, scheduling and private
notes remain editable. Native keeps its existing exact campaign contract.
Ordinary manual/imported media receives no new research-certification claim.

Unbound legacy Studio rows cannot dispatch. Explicit send from a currently
reviewed source preserves the old row's words, moves a recognized legacy Studio
row to review, and creates a bound replacement. A stale link to unrelated manual
content is detached without editing that content. Active or unresolved sends
remain protected. Existing posted/archived-source resend behavior is unchanged
and still needs its separate campaign-version workflow.

The owner-only `GET /api/queue/{id}/source-context` accepts the exact queue
creation timestamp and rejects reused identities. It performs read-only current
checks and returns retained narration, facts, acceptance and media hashes; blob
paths are omitted from asset summaries. Retained facts retain their original
context. SocialPilot's **Queued source evidence** dialog exposes those
records. Read outages preserve the last retrieved snapshot with an unconfirmed
warning; selection, closing, retry and 15-second deadlines fence stale reads.
Opening the current draft is offered only for matching source creation identity
and explicitly warns that a draft can differ from the retained export.

These are local typed consistency and owner-evidence checks. They do not prove
complete factual truth, legal permission, semantic shot relevance or a real
provider's publication result. Evidence status must retain that distinction.

## Verification and rollout limits

`test_queue_source_binding.py` uses real generated local media and actual owner
acceptance endpoints, with fake external account/transport/Drive boundaries. Its
PostgreSQL wrapper runs those same contracts on the disposable native database.
The existing orchestration-only Studio/Countdown tests explicitly mock the
binding boundary because their media stubs are opaque or nonexistent; they are
not retained-source proof. Real UI scenarios cover generated media, acceptance,
send, source inspection and simulated delivery separately.

Older worker binaries ignore this new binding table. Passing new-worker CAS
tests does not establish downgrade or mixed-version publication safety. A later
version-named database write fence now rejects unmarked posting-status writes;
see DATABASE_RECOVERY.md for its exact verified scope and limits. It cannot recall
in-flight requests or stop arbitrary old code that submits without a DB write.
Stop all old publishers and reconcile receipts before any upgrade/rollback.
Mixed-version publishing and production rollout remain unverified.

## Requested/delivered length and permanent acknowledgment verified

Requested length is independent of the mux timeline. Studio final review shows
its saved approximate export target; Countdown review retains the exact requested
per-title/total segment length, excluding added bookends. Actual measured length
outside a25% band requires an explicit editorial decision. This is a review
heuristic, not a quality score or hard length requirement. No words are padded or
rewritten. Exact fingerprints and immutable export inputs retain the target;
custom Countdown length changes preserve old bytes/intent and revoke current
render/acceptance. Normal36s Top15 children remain valid. Explicit invalid0/False
historical settings remain unconfirmed rather than inheriting a default.
Retained delivery refuses missing/malformed/future target proof; it never borrows
a changed live draft's target. Owner source inspection displays retained intent.

A real browser regression also reproduced acknowledgment returning silently when
an edited decision was restored to identical text. Every checklist/decision/rights
edit now clears acknowledgment permanently until the owner checks it again.
Actual browser tests cover clearing and restoring each field, required deviation
decisions, requested/delivered display, retained main/child targets and ordinary
LongForm/Top5/legacy/native delivery boundaries.

Initial duration corpus6failed/3passed; extra subminute/invalidsaved cases3failed,
then focused16passed1.87s and compatibility74passed1.83s. Final backend
846passed/1optional skip82.87s; actualPG368passed39.55s/native restore1.163s,
219072bytes/source unchanged/SHA
a45b7ef4e6ad9a88d509d4133448ad39b2c693d785676dea361be9f5c7d4ec05.
Production build2/tsc2passed. Fresh full browser32passed6.6min, sourceSHA
5fc2a05f88416d1b16eb9ce7080e02e59e4a584ed372914e108c9d00cd4efadd,
evidence history/requested-duration2; verified snapshotSHA
fe98a80ede53b756b04f38dbdd24eb3ba1dfacde3dd29693aafe1ad689f64ed4.
All media/runtime/frozen source/reports/traces/exact spec/snapshot preserved and
owned stack stopped. Length-review screenshot inspected. The first55.4s failed
acknowledgment repro remains separately in history/requested-duration1.
Original synthetic footage/offline speech actual; provider/cloud simulated;
externalspend0. These6s/81s fixtures do not prove excellent3min/8min narratives.
Wider semantic/caption/editorial/media/provider/operational work remains.
Next shared-TTS-cache changes are separate and not included in this full32 source.
No archive/auth/paid/public/account/push/deploy action.
