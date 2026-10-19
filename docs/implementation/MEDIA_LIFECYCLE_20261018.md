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

## Narration cache ownership verified

Each TTS cache key, unique provider temporary file and destination is protected
by a bounded cancelable database writer claim. Concurrent misses recheck after
ownership and share the completed generation; cache hits copy before releasing
ownership. LRU cannot remove a live source/partial response. Generation loss
blocks promotion. A busy error from the consumer body never repeats that work.
The120s wait is bounded; an unusually long provider attempt can leave a later
consumer with an explicit busy/retry error. No extra request is made by waiting.

Broad TTS directory reader claims were removed from Studio/Countdown wrappers to
avoid reader-to-writer deadlock between projects. Protection now lives in the
common synthesizer, including native and compilation callers. Shared motion/audio
directory readers remain for their separate lifecycle. These contracts do not
prove real speech-provider billing, perfect audio or every process crash scenario.

Four corrected independent failures reproduced cache-reader/temp-write eviction
and duplicate generation both directly and through actual Studio runners.
Focused33passed1.99s includes cancellation/boundedwait, one-body execution and
revoked generation. Full backend853passed/1optional skip98.13s; realPG375passed
40.16s/native restore1.572s219372bytes/source unchanged/SHA
0fe85a4c7ca067f494dd6257dd86ea18084ad6077394db9a5303f9a3acbca39b.
Production frontend unchanged from durationbuild2; explicit tsc1passed.
Fresh scoped3 browser passed2.3min: actual LongForm,Top5 and nativeShort/carousel
now exercise productiontts.synth/cache, replacing only the provider with installed
offline speech. Five independent segments share one common-phrase generation;
14completed offline provider receipts atTop5 review have exact matching cached
bytes and zero cloud calls. Root fake-speech ledger labels its simulated boundary.
This is actual common-cache integration, not cloud speech/billing proof.
SourceSHAf7c58e31ebd365c3bb696d0601b30f6a199089af34d2568d667e100d3402f770,
evidence history/tts-cache-ownership1; full reports/traces/media/source/runtime
ledgers/fakeDrive/verified snapshot retained; owned stack stopped. Prior full32
is separate pre-cache source5fc. The wider C01-C14 roadmap remains unfinished.
No archive/auth/paid/public/account/push/deploy action.
