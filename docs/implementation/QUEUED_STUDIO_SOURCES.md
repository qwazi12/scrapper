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
