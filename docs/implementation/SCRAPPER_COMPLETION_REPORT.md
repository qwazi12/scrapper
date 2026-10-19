# Screen Central revival — current implementation status, October 19, 2026

**The complete objective is unfinished.** This is a current local evidence index,
not a final completion or production-readiness report. The broad C01–C14 roadmap
and original Phase 0 findings remain the acceptance scope. Work continues on
`codex/scrapper-revival-full-stack-20261018` in the isolated revival worktree;
primary main remains based at `95b6bd45c23644cff2ec4549db4312c2f7d1281e`.

The owner requested continued local work, MEMORY.md and local commits without
checkpoint stops, then authorized GitHub push/deployment/live testing **when
finished**. That condition is unmet. No push, merge, deployment, real public post,
reply, live database/settings/account mutation or paid API call has been made.
External API test spend remains **$0**. The latest instruction defers all Shorts
archive and Drive authentication work. No archive transfer should resume.

[Architecture](ARCHITECTURE_CHECKPOINT.md), [remaining roadmap](REMAINING_ROADMAP.md),
[resume instructions](SCRAPPER_RESUME.md) and the append-only
[progress ledger](SCRAPPER_AUTONOMOUS_PROGRESS.md) supersede older checkpoint
counts and capability descriptions. Git history and root MEMORY.md retain each
local slice. This report does not reset or replace the original product vision.

## Implemented system and practical limits

| Area | Implemented locally | Still required for complete scope |
|---|---|---|
| Research / identity / discovery | Strict canonical IDs/title-year-medium resolution, typed crew roles, claim references, independently retrieved immutable source records, explicit owner semantic/conflict/freshness review, saved-word rechecks and downstream invalidation | Broader typed material-claim coverage, specialized eligibility, full semantic truth/relevance and adversarial corpus; factual quality of real provider research |
| LongForm / Countdowns | Staged attempts, campaign-owned segments, exact counts/unique IDs, bounded identity/category/bookend reviews, actual FFmpeg renders/stitch, retained original versions and unchecked draft recovery | Wider eligibility/legacy saved-list migration and long-form editorial corpus; robust creative quality across real material |
| Media acceptance | Full decode/streams/duration/aspect/skew/loudness/true peak, timed black/silence spans, four explicit editorial checks, substantive per-finding decisions and rights attestation bound to exact inputs/bytes/inspection | Word alignment, final-frame caption/OCR/safe-area validation, semantic shot identity, actual pronunciation/story/rights review and excellent real-content examples |
| Native formats | Original Short/Reel cards, explicit authorized video intervals and background audio, ordered carousel, exact single-image output, distinct format copy, community/poll private JSON/text packages, immutable versions, structured native acceptance and review-only queue gates | Distinct deeper Reel treatment, recuts, semantic music/beat retrieval and musical transitions and broader creative diversity |
| Asset catalog | Bounded authorized still/video/audio import, normalized bytes and original/normalized SHA, decoded previews/difference hashes, owner provenance/tags/caption spans/people labels, immutable metadata/use-permission revisions, original-provenance retention, deduplication and bounded lexical retrieval | Semantic retrieval/identity assistance, deeper audio matching/transitions and preservation masters; labels/captions currently owner-supplied |
| Posting / scheduling | Atomic queue links, exact media/copy gates, per-target durable intent and idempotency keys, strict receipts, unknown-outcome fences, owner evidence review and retained delivered bytes | Real provider contract/account proof, staging/rollout, full typed status/negative matrix; no public delivery proof from fixtures |
| Budgets / workers | Atomic integer USD/vidIQ credit reservations, separate usage estimates/owner billing attestations, uncertain holds, DB-clock renewable generation leases, residential/legacy workers and explicit orphan resolution | Actual pricing/usage/invoice contracts, broader kill/restart/rolling-upgrade cases and operational limits |
| Storage / recovery | Shared readers/exclusive file claims, response-lifetime integrity, reference-aware cleanup, verified remote-copy receipts, retained export/delivery evidence, WAL-aware atomic SQLite snapshots/offline restore/legacy identity CLI | Remaining local temp consumers, managed remote reclamation, real-volume/production recovery/retention/RTO; automatic remote trash stays disabled |
| PostgreSQL | Actual private local behavioral lane and native custom dump/least-privilege transactional restore, exact schema/data/sequence fingerprints, corruption/content-mutation detection and empty rollback after late errors | Production roles/ACL/tablespace/large-object/media scope, volume recovery, actual operational backup policy and mixed-version rollout; automatic app backups remain SQLite-only |
| Audience / cost lineage | Read-only YouTube Analytics adapter for an existing declared grant, processed-day latency/revision/missing-value semantics, immutable owner content mappings and scoped recorded variable-cost evidence | Real private grant/report proof, IG/FB adapters, broader history pagination and refresh/retention lifecycle, legacy/delivery attribution; no verified invoice/profit claim |
| Comments / corrections | Explicit bounded read-only thread/reply pages, separate durable cursors, redacted append-only source evidence, exact-context private drafts/decisions, stale-source invalidation and idempotent exact-claim correction follow-ups | Real provider proof, privacy/source-refresh/deletion lifecycle and deeper semantic reply assistance; no sending/moderation endpoint |
| Learning / autonomy | Same-account/platform/format/age leave-one-out diagnostics, metric-specific missingness, explicit heuristic/sample limits, exact-context private owner decisions and linked manually completed experiments | No causal winner, beneficial-growth proof or execution authority; provider/billing evidence, deeper controlled outcomes and bounded autonomous operation remain |
| Owner UX | Stable navigation/history, script/comment/experiment/native-treatment/asset-metadata browser drafts, stale-save/storage-failure guards, accessible controls, readable bounded typed errors, exact-origin credential cache, confirmed-save/read-refresh separation and retained queue snapshots/edits through outages | Remaining forms, broader platform-status design and full negative/crash/recovery/accessibility coverage |

Native exact acceptance and image/Reel publishing-field corrections are locally
verified and committed in304b5ae. They replace blanket native approval, bind the full
manifest/inspection/check policy and require re-review of legacy approvals.
Actual provider transports remain mocked. Structured approval never itself
publishes or certifies facts/permissions.

## Latest exact verification

All paths below are relative to this worktree's ignored `artifacts/`. Evidence
must be preserved/exported before removing the worktree; Git commits do not
contain the ignored media, database snapshots or traces.

| Lane | Verified result | Exact evidence |
|---|---|---|
| Full backend after current-facts admission | **714 passed /1 optional skip**,73.34s,1 reported TestClient/httpx deprecation | `live-research-admission-backend1.txt`, `.xml` |
| Actual PostgreSQL behavior | **238 passed**,32.15s,1 reported warning | `live-research-admission-pg1/pytest.txt`, `.xml` |
| Actual private PostgreSQL recovery | **Passed**,1.306s;185951bytes; SHA256`5c3fad737bd57080a93a88f6f60a7fb985e7c5c217a7d2542605cd46b34bc8ea`; source unchanged | `live-research-admission-pg1/restore-drill.txt`, `restore-*/restore-manifest.json`, archive/fingerprints/logs |
| Selected clip contracts | **34 passed**,11.32s, actual hold/loop frame/audio checks, strict ranges/source claims | `native-clips-focused2.txt` |
| Native acceptance focused | **22 passed**,2.24s, then all5 new contracts including actual generated-tone Reel render **passed**,2.63s | `native-acceptance-focused2.txt`, `native-acceptance-focused3.txt` |
| Frontend production build | Passed after older selected-source recovery; explicit tsc passed | `native-source-recovery-build3.txt`, `native-source-recovery-tsc2.txt` |
| Frontend error contracts | **6 passed**,114.49ms; typeless Node module warning reported | `api-errors-unit3.txt` |
| Current still/source review UI | **2 passed**,12.0s; current source includes publisher cleanup and still integrity; actual duplicate409, exact recovery and revised-native render/acceptance | `e2e/history/still-integrity1/` |
| Reviewed asset decisions focused UI | **1 passed**,11.1s; real source/import, private draft recovery, stale competing decision, block/re-permit history and revised native source render/acceptance; fresh full24 regression also passed | `e2e/history/asset-annotations1/` |
| Current-facts admission UI | **3 passed**,1.1min; fresh capture refinement **1 passed9.0s**; cached historical green is blocked on read/forced plan before rechecking; frozen source SHA`62791e5349e0b1dbf7b3c244cf088037ee86d319eba0e43064a32bc4eb6442b9` | `e2e/history/live-research-admission1/`, `live-research-admission2/` |
| Earlier known-role subject UI | **2 passed**,1.0min; actual LongForm/media path and seven saved credit inputs including an unlisted performer; frozen source SHA`f2ef27617e48ab75c609e4113e7fd80d4d3780ac2fa79ddc51f493dfde23ff29` | `e2e/history/unlisted-performer1/` |
| Earlier typed credit-context UI | **2 existing baseline cases passed**, then corrected fresh new case **1 passed27.2s**; six saved inputs/blocked readiness/name preservation; frozen backend/scripts SHA`6164fd9c9fcd266a40b5ac9720379f94a3211dae74f0ef663a48b28299d2cce4` | `e2e/history/material-credit-context1/`, `material-credit-context2/` |
| Latest full browser regression (before credit-context slice) | **26 passed**,4.2min; includes still duplicate integrity and older exact-source recovery beyond the real100-row picker, reviewed metadata/use decisions, native sources and prior media/safety cases; frozen backend/scripts SHA`1764093d2253b4c9849188cec1be7dbe9f52f0aeba679488a32c6ba35c05e8fa` | `e2e/history/native-source-recovery4/` report/traces/screenshots/actual media/verified DB snapshot |
| Earlier full browser before background-audio slice | **22 passed**,3.9min; frozen backend/scripts SHA`87cae4a6a3b3d498a4509c0c9b4deb79994e9e80ad459c1c99fe5bde39a47cc2`; complete media and recovery cases | `e2e/history/native-drafts3/` report/traces/screenshots/actual media/verified DB snapshot |
| Earlier full browser before native slice | **20 passed**,3.9min; frozen backend/scripts SHA`e6214eab0d40e4d396422f5af813f17962cb09a205467e514b2a8e9deef410e0` | `e2e/history/owner-recovery5/stack.json`, report, traces/screenshots and verified DB snapshot |
| Browser after native acceptance | **4 passed**,2.2min; fresh image/manual visual capture **1 passed**,3.7s. Frozen backend/scripts SHA`d1e6c1a4eff1c46fb56594de496936f9ee7b437c8a7f546ba48be4f526105e25` | `e2e/history/native-acceptance1/` and `native-acceptance3/` reports/traces/screenshots/verified DB snapshots |
| Selected-video browser | Three existing native/asset workflows passed; corrected fresh selected-video flow **passed,8.0s**, including actual playback/seek. One preceding ambiguous-alert selector failure retained | `e2e/history/native-clips1/` and `native-clips3/`, frozen SHA`54a84d361118738f2565ef4548a5add4e475b11e6e1fce928f2e0654f2dd5d41` |
| Native template recovery | Fresh focused UI **passed,3.5s**: all-copy/clip recovery, stale research, failed requests, late responses, storage failure and malformed cache. Expanded full22-browser regression passed (3.9min) | `e2e/history/native-drafts2/` |
| Later background-audio UI | Three native/manual/recovery cases passed; corrected fresh catalog+combined video/audio UI **2 passed,10.3s**; real render/playback/seek/dual finding decisions/review-only queue | `e2e/history/native-audio1/` and `native-audio2/`, frozen SHA`0d1bc77c485bb79d66222829505b1fc6d502566754d1d3928e751ab8c66a99c6` |
| Objective long-duration media | Actual600s procedural black/generated-tone corpus passes objective checks;2,636,742bytes,SHA`3d5486c63d64b2fd08d65140d21c59f66f562b41847930b25ca59f4fbddbbde2` | `export-acceptance-corpus/manifest.json` and source media |
| Linux/container/CI | Prepared; **not executed** | `.github/workflows/quality.yml`, `scripts/e2e/Dockerfile` |
| Real provider/staging/deployment | **Unverified; not executed** | No deployment/provider success receipt exists |

The current full browser26 includes actual LongForm and five independent Countdown
segment pipelines/stitch, native formats and private local downloads, independent
source review, local worker handoff, simulated delivery/uncertainty/financial
reconciliation, retained-media/history recovery, asset catalog, content lineage,
comment/correction review, diagnostics→manual experiment, browser draft failures,
API-origin credential recovery, real limiter recovery, explicit mixed video/audio playback/seek, native treatment recovery, asset metadata/caption recovery, duplicate-provenance receipts, stale search protection, immutable metadata/use decisions, stale recovered review words explicit revised-source native rendering/acceptance, still integrity refusals/exact recovery and older selected-source read failure/cancellation/version recovery. Rate exhaustion is
fixture-seeded; the test waits the actual60-second window. It is not a600-request
load benchmark. Independent fixture request buckets are reset between cases;
production authorization/limits remain unchanged. Earlier failed runs and their
fixes are retained in the progress ledger and uniquely named artifacts.

Safe E2E freezes backend/scripts source and uses a fresh marked database/token/
asset root, original procedural media, installed offline speech, fixture provider
receipts, blocked external sockets and OS sandboxing on macOS. No schedulers or
production recovery workers run. PostgreSQL has a separate private marked local
cluster/Unix socket, no TCP listener, no live URL and least-privilege restore role.
The shared Python environment is not modified to suppress its remaining warning.

The procedural films have literal synthetic narration and controlled visuals.
They establish orchestration, file integrity and safety contracts; they do not
establish movie/character recognition, entertainment value, human speech quality,
license validity, factual excellence or audience growth. The600s black/tone case
is duration/span stress evidence, not a600s editorial production sample.

## Remaining sequence and release conditions

Continue C01–C14 without routine approval stops. Native acceptance and its completed browser evidence are saved; continue compatible media/editorial
quality, broader factual/category contracts, source/provider refresh lifecycle,
remaining UX/lifecycle cases and current documentation. The roadmap table gives
specific residual scope; do not convert a partial milestone into completion.

Before the already authorized eventual push/deployment/live test: finish required
implementation, preserve ignored evidence/media, inspect the final diff and full
required checks, verify upgrade/backup/restore and old queue/journal compatibility,
complete the missing environment/provider lanes, and have an evidence-backed
rollout/rollback plan. Production Ready rows cannot silently gain new acceptance.
Legacy native and Studio single-click approvals require explicit current review.
Unknown provider outcomes remain durable through upgrade and rollback; never
restore automatic ambiguous retries or discard intent/late-receipt evidence.

No automatic remote reclamation or public audience action should be enabled to
make tests look complete. Rollout remains one controlled worker/replica first;
real unknown receipts must be reconciled before retry. Main, production and live
accounts remain untouched while independent local work continues. No additional
routine implementation permission is pending.

## October19 retained-source and current-credit verification

Latest complete UI generation: **29passed5.3min**, frozen backend/scripts
ee302aca8fc87bf0221dc184b3c27fd1b8bb7c9401d1d4d7234ee6dc3fdc308f.
Source bindings now retain accepted LongForm/Countdown script/facts/media/copy;
new queue admission checks current facts and bytes, revision/identity CAS, legacy
review and receipt lineage. Owner inspection shows plain narration plus retained
acceptance/hashes with honest failed-read recovery. All previous actual local
worker/media/native/recovery/audience/comment/learning flows passed alongside
13 synthetic credit cases, including complete joint writers and original authors.
The full backend for this generation passed760/1optional skip; actualPG284 plus
restore1.350s/source unchanged. Build2/tsc2passed. One dependency deprecation warning
per Python lane remains. Evidence: artifacts/e2e/history/queue-source-binding3.
Earlier failed test assumptions and refinements are retained in history1/2; they
are not represented as passes. No real external provider or public delivery was
used. Queued-source contract: QUEUED_STUDIO_SOURCES.md.

A later runtime database-write protocol fence is being verified separately;
full29 does not include it. Broader C01–C14 and external/operational proof remain
incomplete. Continue from MEMORY.md rather than stopping at this evidence index.

## Legacy Countdown recovery and direct worker verification

Latest complete UI generation is **30passed6.6min**, source
a1baef1e7ef85060be5412598c4a8dc23c58633cf698bafb21c5e5d9e1096518.
Legacy upgrade preserves words/ranking/old media/history and requires current
review, new identity-bound children and new export acceptance. Direct worker and
scheduler admission enforce the parent; automation does not fabricate missing
editorial support or silently change the requested format. Backend784/1optional
skip and actualPG306/restore1.110s passed, build2/tsc1passed.
Evidence history/countdown-legacy-upgrade2; COUNTDOWN_SAVED_CONTRACTS.md describes
limits. The corrected test waits actual publish HTTP/queue persistence; the prior
failed immediate-read trace/final committed state are preserved separately.
Root simulated provider/photo ledgers and fake-Drive copies are retained under
root-runtime (also backfilled from stopped marked roots for the preceding five
archives). Actual Drive/public transfers remain unexecuted/deferred.

Later DB-clock category admission is separately verified; this full30 is not its
source generation. C01–C14 remain unfinished, including broader specialized
eligibility, unknown legacy formats and factual/editorial/provider/operational proof.

## Supported Countdown formats and exact acceptance verified

Only Top5/Top10/Top15 admit current work, with the exact requested entry count.
Unsupported saved formats remain readable with original words/media/history;
they cannot be silently converted or promoted through legacy recovery. A live
parent lease is preserved before stopped-work recovery. Immutable export inputs
and the final fingerprint retain requested format. Acceptance records that exact
format; retained dispatch requires explicit format/count/acceptance proof and
never infers it from a changed live draft. Older formatless exports need current
review/resend. The interface displays the format and explains supported recovery.

Full backend **799passed/1optional skip77.86s**; actual PostgreSQL **321passed37.23s**,
native restore **1.233s**,214393bytes,source unchanged,SHA
0d5e4639e11b7e3762d2279e1b08302ad56d1d2c5943acf5bd0b0948a876384c.
Production build2/explicit tsc1 passed. Pre-final-live-owner-guard full browser
**31passed6.6min** is retained separately in history/countdown-format-admission1.
Final source scoped browser **2passed1.3min** covers actual Top5 media/render,
explicit requested-format acceptance and immutable queue source, plus unsupported
format readable/no-conversion/disabled-work behavior. SourceSHA
5e8c1277ccfc172d4661e674eba8a733e01583dea4e5d0432ac380a847fdef09;
evidence artifacts/e2e/history/countdown-format-admission2; verified WAL-aware
snapshotSHA96990334c1acca28b8d924bf0d618a706c53f0bd52d74805da0c104dd640eeab.
Format acceptance screenshot inspected. Original synthetic assets/offline speech
are actual; provider/cloud services simulated; external spend$0. Owned stack
stopped after preserving media/source/runtime ledgers/reports/traces/snapshot.
These results do not close specialized category/editorial/semantic or C01–C14.
No archive/auth/paid/public/account/push/deploy action.

## Local category Boolean meaning verified

Category criteria version2 keeps genre, production-country and provider and/or
lists independent. An unrelated provider alternative cannot relax required
genres; an explicit country conjunction needs both production countries.
Recognized longest aliases and explicit inclusive year ranges/single
before/after/since/until bounds are supported. Mixed or cross-filter alternatives,
compound/disjoint temporal conditions and unsupported exclusions require
rephrasing/independent proof before retrieval or child creation. This is a bounded
parser, not general language or complete category truth. Saved v1 proofs cannot
borrow v2 meaning at parent, direct-child or retained delivery admission; original
words/media/history are preserved and current confirmation is required.

Initial independent corpus14failed/8passed; expanded focused67passed1.16s.
Full backend830passed/1optional skip91.75s; actualPG352passed38.52s/native restore
1.188s214730bytes/source unchanged/SHA
42670c658f79f99220e649368d6f345bf5f0e27a8be991c8047efd4a5a17b034.
Production build1/tsc1passed. Full32 browser run:31existing flows passed7.3min,
new category case failed an incorrect dialog expectation after actual expected409
and inline message. The corrected category case then passed5.3s (run6.2s) on an
identical indexed backend source, with actual local-filter display and unchanged
ranking/no segments after refusal. These are separate runs, not an all-green
single full32 execution. Initial nested-details locator failure is also retained.
Evidence history/countdown-category-logic1/2/3; sourceSHA
a03e7d93ac2614ed58a7230a6789d361d4d0c0ef4708a816958bd49c54aa2253;
latest verified snapshotSHA5d97b8d3f938033c78f1ddd7fc7bf0856afa7922e30b45e539a31b2a481834bf.
Reports/traces/exact executed specs/original generated media/frozen source/runtime
ledgers/fakeDrive/snapshots retained; owned stacks stopped. Blocked-category
screenshot inspected. Providers/cloud simulated, externalspend0. Subsequent
requested-duration work is a separate unstaged source; no proof inferred for it.
Specialized categories/semantic/editorial quality and the wider roadmap remain.
No archive/auth/paid/public/account/push/deploy action.

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

## Actual process-boundary cache recovery verified

Two separate Python worker PIDs, sharing the actual database, now have direct
regression coverage for one-key/one-generation cache reuse. A separate case
terminates an owned provider worker after a partial response, confirms process
death, injects expiry using database time, then starts a new worker. The partial
is never a cache hit or promoted output; new complete bytes reach the consumer,
and ordinary LRU removes only the abandoned partial afterward. A stopped waiter
also exits before entering generation. Provider requests remain offline fixtures.
Forced expiry after actual death is not a real75sTTL timing observation or proof
of uncertain cloud billing/exactly-once provider requests.

No production code changed from558a68e. Initial focused8passed/2test-harness
failures exposed /var versus /private path normalization in the test query and
the ControlJob context's expected cancellation rethrow; corrected focused36passed
3.44s. Full backend856passed/1optional skip83.87s; actualPG378passed41.77s,
native restore1.244s219626bytes/source unchanged/SHA
2b642d6931856f3589b0e7ecf9b1f4ec5498e0a1bfc289db9f2b9e49ac27ae1e.
Existing TTS scoped3 UI remains the separately identified integration proof for
identical production code. Wider kill/restart, stop-all rollout/mixed versions,
real provider invoices and operational recovery remain open. No new browser,
production, billing or deployment claim follows from these process tests.
