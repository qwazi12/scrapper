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
publication media, Countdown render sources/current exports/Drive/thumbnail edits,
legacy compilation inputs, Drive download staging and LRU
cache eviction. Studio stages also pin shared motion/audio/TTS cache directories.
Retention and owner clip/compilation deletion check saved queue, Studio, export,
asset and compilation references. A pending Studio worker handoff is a reference.
Operational log retention defaults to 30 days and 100,000 rows, separately from
append-only financial and publication evidence.

Production FileResponse paths now hold shared ownership through the final ASGI
body, including Range/HEAD/cancellation and expired-generation rejection. Automatic
Drive media and database-backup deletion paths are disabled: age, folder membership
and a newer upload do not establish ownership or another copy of older bytes.
Managed remote reclamation and remaining local backup/temp ownership still need work. A lease controls cooperating application processes;
it cannot stop a person or unrelated process changing files on disk. Ordinary
byte hashes do not establish media rights, relevance or editorial quality.

## Verified copies and restoration

Drive upload completion now requires a separate metadata read whose remote MD5
and byte count match the local content. The saved receipt records local SHA-256,
remote MD5, bytes, remote ID and verification time. A changed local file or missing
remote proof fails closed. Existing `saved` labels without proof cannot authorize
Studio archive/footage deletion or Countdown video reclamation; the owner must save and verify the current file.
Queue-linked previous remote copies are retained, and only a queue item pointing
to this exact local video may receive its new Drive link.

Before footage deletion, source hashes and lengths are retained. Restoration
fully decodes a newly downloaded file and compares it with the original hash
before promotion. Changed or unverifiable legacy sources require owner review;
no incompatible asset silently inherits the old shot plan. A real Drive account
was never accessed. SDK/remote compatibility still requires an isolated provider
lane approved separately from this local work.

Countdown saves retain the same video proof and use a renewable generation lease.
A stale transfer cannot attach its result to a replaced render or another queued
video. Render output directories use random attempt IDs, avoiding same-second
collisions. Thumbnail refresh stages privately and checks ownership before
promotion; queued/shared thumbnail files cannot be overwritten. Owner deletion
refuses unresolved submissions and foreign references. Scheduled cleanup skips
busy media and requires a matching verified backup for the current video. Its
own archived queue row must also carry that backup link before local bytes go.

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

- `artifacts/countdown-backend7.txt/xml`: **466 passed, one optional skip**, 40.48s;
  thread exceptions are failures. Existing 32 warnings are deprecations.
- `artifacts/countdown-lifecycle5.txt`: 52 focused lifecycle/mutation/countdown
  tests pass before the last stricter backup-link case; the final full suite
  includes that additional case (15 new Countdown lifecycle cases in total).
- `artifacts/countdown-build4.txt`: production frontend build passes.
- `artifacts/e2e/countdown-browser5.txt`: **10/10 passed, 2.6min**, including
  actual thumbnail refresh before approval and queued-thumbnail rejection.
- `artifacts/e2e/countdown-isolation5.json`: zero external attempts, no scheduler
  threads, six fake publication receipts, $0 external spend. Owned servers stopped.

The last stricter archive-link comparison is backend-tested; safe browser mode
suppresses archive scheduling. PostgreSQL, clock skew, crash-injected rolling
upgrades, real providers, production-length media, retained original-studio export
history, every stream/trash consumer and the full UI/rights matrix remain open.


## Retained original exports and delivered content

Studio/Countdown exports retain independent content-addressed media and saved-input/approval snapshots. Database guards make the records append-only. Upstream invalidation and project deletion retain the old bytes; restore compares the current project version/creation identity and restores only an unchecked draft. Thumbnail options have unique paths rather than overwriting older selected bytes.

Settled queue-row reclamation first captures exact available local files, content, targets and delivery observations in DeliveryArchive, plus a publication-journal reference. A missing/legacy file remains explicitly unavailable. Shared readers may finish; an exclusive mutation or uncertain submission keeps the queue row. API history survives numeric-ID reuse, is owner-private, and verifies hashes before streaming. Unknown file types download as attachments with nosniff. The owner Settings history remains reachable when schedule reads fail.

Drive creation IDs are retained even if checksum verification or content ownership later fails. Independently verified transfer receipts and kept current/superseded version records are append-only. Prior remote versions are retained rather than assuming the new render replaces their bytes. These are local mocked-provider contracts; no Drive account was contacted. Remote garbage collection is deliberately unavailable until managed-copy/reference proof exists.
