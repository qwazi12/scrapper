# Local media lifecycle checkpoint

This extends the isolated revival branch. It is not a production deployment or a
claim that the Screen Central roadmap is complete. All executed tests use local
synthetic media and mocked remote boundaries; external spend is zero.

## Ownership and deletion

`file_use.pin` admits shared readers and exclusive mutations under the database
mutex. Claims cover descendant paths, renew while their invocation lives and
fence controlled subprocesses and database writes after expiry. Nested claims
from the same invocation are allowed. Expired workers cannot revive a claim.

Current integrations cover Studio stages and edits, restore/archive/Drive saves,
publication media, legacy compilation inputs, Drive download staging and LRU
cache eviction. Studio stages also pin shared motion/audio/TTS cache directories.
Retention and owner clip/compilation deletion check saved queue, Studio, export,
asset and compilation references. A pending Studio worker handoff is a reference.
Operational log retention defaults to 30 days and 100,000 rows, separately from
append-only financial and publication evidence.

These contracts do not yet cover every media response stream or every legacy
Countdown/Drive trash path. A lease controls cooperating application processes;
it cannot stop a person or unrelated process changing files on disk. Ordinary
byte hashes do not establish media rights, relevance or editorial quality.

## Verified copies and restoration

Drive upload completion now requires a separate metadata read whose remote MD5
and byte count match the local content. The saved receipt records local SHA-256,
remote MD5, bytes, remote ID and verification time. A changed local file or missing
remote proof fails closed. Existing `saved` labels without proof cannot authorize
Studio archive/footage deletion; the owner must save and verify the current file.
Queue-linked previous remote copies are retained, and only a queue item pointing
to this exact local video may receive its new Drive link.

Before footage deletion, source hashes and lengths are retained. Restoration
fully decodes a newly downloaded file and compares it with the original hash
before promotion. Changed or unverifiable legacy sources require owner review;
no incompatible asset silently inherits the old shot plan. A real Drive account
was never accessed. SDK/remote compatibility still requires an isolated provider
lane approved separately from this local work.

## Uploads, mutation and undo

Video uploads and Drive downloads share a bounded probe/full-decode gate. Cookie
exports must be valid bounded Netscape text and are atomically replaced with
private file permissions. Queue image uploads have byte/pixel bounds and unique
files; selected Studio thumbnails are copied for queue use. Failed/stale uploads
preserve the previous file. Manual Studio trailers use distinct filenames.

Queue edits refresh under the writer mutex and refuse live/uncertain publication.
AI metadata results are version-checked after provider I/O; bulk rewriting also
refuses submitted/native items before dispatch. Undo refuses submitted evidence
and a snapshot from a reused numeric row identity. Undoing narration after an
upstream evidence change invalidates its checks and downstream media.

New SQLite schemas use monotonic IDs for the main content tables. This does not
rebuild existing SQLite tables. Existing databases need an explicit backed-up
migration and dangling-reference review before depending on that guarantee. No
owner database migration was run. Drain old workers before any later rollout;
pre-v2 residential clients must be upgraded with the server.

## Evidence

- `artifacts/lifecycle-backend10.txt/xml`: 444 passed, one optional skip; thread
  exceptions are test failures. Subsequent waiting-checkpoint regressions pass.
- `artifacts/lifecycle-new9.txt`: 21 mutation, ownership, checksum, corruption,
  cookie, restore and retention tests pass.
- `artifacts/lifecycle-resume11.txt`: 9 lease/resume tests pass, including explicit
  fresh invocations superseding queued, budget-paused and worker-wait checkpoints.
- Browser evidence and latest full-suite results are recorded in the progress
  ledger when their runs finish. Do not infer an unrun lane passed.

PostgreSQL, clock skew, crash-injected rolling upgrades, real providers, long
production media, and the complete UI/rights/retention matrix remain unproven.
