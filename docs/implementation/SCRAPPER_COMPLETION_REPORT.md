# Scrapper revival — implementation checkpoint, October 18, 2026

**The M1–M5 roadmap is unfinished. This is a substantial, tested local safety
checkpoint with local editorial/native/learning prototypes, not a production-ready Screen Central OS release.** Work ran in this
desktop session on `codex/scrapper-revival-full-stack-20261018`, isolated at
`/Users/kwasiyeboah/dev/scrapper-worktrees/revival-20261018`.
Base main: `95b6bd45c23644cff2ec4549db4312c2f7d1281e`.

The primary checkout, live databases, accounts, schedules and deployments were
not changed. No public posting, real social queue mutation, live researcher call,
paid API use, push or PR occurred. External test spend: **$0.00**. The Downloads
handoff was absent; the attached text was copied into the feature worktree.
No nested Codex process or recurring automation was launched.

Latest continued checkpoint: **451 backend tests passed /1 optional skip**, production frontend build and TypeScript passed, and **all 10 browser scenarios passed**. Studio/Countdown draft recovery, stale-save rejection and Countdown undo scope are now covered. See the append-only progress ledger for the full sequence and remaining gaps.

## Implemented and verified scope

- Fail-closed checker coverage, verdict types, reference IDs and repaired-sentence
  rechecks. Rejected drafts and owner edits remain inspectable. Saved-script
  verification preserves words; plan/render/enqueue reject unchecked narration.
  Explicit known actor/role contradictions have a Dune/Pattinson regression.
- Typed screenplay/story/novel crew credits. Independently retrieved, immutable
  source records now support explicit owner semantic review with exact context,
  excerpt/hash, conflict and seven-day freshness checks. Generated evidence alone
  cannot authorize narration. Review changes invalidate downstream approval.
  The checker remains storyboard-only; complete autonomous material-claim truth
  verification is still open. See [local contracts](LOCAL_CONTRACTS_20261018.md).
- Atomic integer USD and vidIQ credit reservations precede provider dispatch.
  Uncertain receipts retain holds; known receipts settle transactionally. Missing
  cost bounds fail closed. Worker generation leases fence resumable work,
  Countdowns and native campaigns; Studio writes use separate attempt folders.
  These are locally tested contracts with explicit remaining release gaps.
- Strict canonical title/year/medium/original-title/alias matching, ambiguity
  rejection and supplied-ID checks in research resolution. Provider availability
  replaces conflicting model copy and carries US/date scope. Unknown availability
  stays unknown. Public countdown descriptions omit unverified stats/default scores;
  AI ranking notes are no longer citable factual narration.
- New countdowns require exact format cardinality and unique IDs on confirmation.
  Countdown segments have campaign ownership; an existing LongForm or another
  ranking cannot silently supply or receive its narration/context.
- Backup files require owner-header authentication; media passes/path signatures
  cannot retrieve them. The UI downloads authenticated blobs.
- Drive metadata cannot choose filesystem paths. Exclusive generated staging,
  no-replace final creation, collision avoidance and interruption cleanup preserve
  other files. Uploaded videos are size-bounded, probed and atomically promoted;
  empty/invalid/oversized replacements preserve previous media.
- Queue submission claim and intent are one DB transaction, with a media/metadata
  fingerprint and comparison against the selected row values. Each platform gets
  its own durable intent/result. Crashes/timeouts remain unresolved; elapsed time
  cannot prove failure. Known provider receipts survive partial uncertainty.
  Unresolved rows reject ordinary edits/deletes/reapproval. Project-to-queue
  creation uses an atomic pointer claim to prevent two enqueue rows.
- Final LongForm/countdown files undergo full decode, stream/duration/aspect/skew,
  integrated loudness, true peak, silence and black-span inspection. Missing audio
  or hard export errors block release. Owner approval binds the exact file,
  thumbnail, script, plan, facts/research/shots; countdowns also bind segment hashes.
  Upstream edits invalidate downstream readiness and retain undo snapshots.
- Actual browser testing found and repaired two additional UI defects: a saved
  check failed to refresh/poll its result; the first generated plan changed React
  hook order and crashed the page. Main-tab hash navigation, honest disk capacity,
  API connection labels and pending catch-up logging were improved.
- Missing vidIQ test mocks are fixed. Backend tests block external sockets and
  join fixture background jobs before removing mocks. Next.js is locked to 15.5.28;
  PostCSS 8.5.30 override and other supported lockfile updates produce zero npm
  advisory matches. File logs rotate at 10 MiB with three backups; all queue-
  referenced clips/compilations survive generic retention. Tracked generated logs
  and buildinfo were removed. Legacy scrape.py no longer stages/commits/pushes.
- Removed the documented borrowed intro WAV. The replacement uses original seeded
  noise bursts plus existing synthesized whoosh/riser/impact, with no external
  audio file. A real audio export and the final Top5 export exercised it.

- Explicit awaiting-worker state suspends the auto-chain, preserves one handoff,
  and leaves missing trailer media incomplete. Repeated offline collection does
  not create new clips or rerun upstream downloads. Legacy direct compilation
  submission returns 410; the UI creates a durable review-queue item instead.
  Legacy topic GET reads cache only; refresh is POST. Schedule GET reads only;
  an explicit configuration PUT still replans.
- M3 prototype: private opportunities, source excerpts, original angle, format
  choices, nullable sourced demand, editable calendar and hash-bound brief review.
  Authorized performance observations retain source/latency, reject conflicting
  duplicates and keep missing measures null. Cohorts separate platform, format
  and publication age (±10%, one snapshot/content/window). Private live analytics
  adapters remain unavailable; imports do not imply OAuth authorization.
- M4 prototype: approved research -> native campaign -> original Short/Reel beat
  cards or portrait carousel. Hook/body/payoff, source reference bounds, caption
  line length/safe areas, reading duration and full final media checks are enforced.
  Optional still imports are bounded, decoded, metadata-stripped, deduplicated and
  tagged, with owner rights attestations. These are not independently certified.
  Immutable export versions bind research, asset provenance, exact media, cover
  and platform copy. Queue approval remains separate. Photo multipart/async and
  idempotency contracts follow [official photo docs](https://docs.upload-post.com/api/upload-photo/)
  and [video docs](https://docs.upload-post.com/api/upload-video/) checked October 18;
  only mocked HTTP and fixture transport were executed, never a live submission.
- M5 prototype: authorized comment-text import, common-contact redaction, heuristic
  questions/corrections/debate/spam labels, editable private reply drafts, manual
  review and linked follow-up opportunities. No reply-sending endpoint exists.
  Experiments record controls/hypotheses/metric/publication age, require comparable
  samples, label retrospective data and never declare a causal or automatic winner.

## Evidence and test limits

| Lane | Actual result | Evidence under ignored artifacts/ |
|---|---|---|
| UNIT_ONLY / API_ONLY, full backend | **447 passed, 1 optional motion test skipped**, 37.60 s; 32 deprecation warnings; worker thread exceptions are errors | lifecycle-backend12.txt/xml |
| Publisher regression after final portable JSON comparison | **90 passed**, 1.17 s | publisher-final.txt |
| Frontend | Production build and explicit tsc passed | roadmap-build5.txt; roadmap-tsc.txt |
| Dependency scanner | **0 npm advisories** at scan time; not exploitability/security assurance | frontend-final-audit6.json |
| UI_E2E_LOCAL | **6 browser tests passed**, 2.4 min; all format flows, navigation/history, evidence review and deterministic Countdown refresh recovery | e2e/roadmap-browser3.txt; playwright-report.json, browser/**/trace.zip |
| MEDIA_E2E, LongForm | Real 1080p export, 6.103 s, AAC, −14.1 LUFS, −1.2 dBFS true peak, no black spans/hard errors | e2e/longform-final.mp4, longform-probe.json, longform-quality.json |
| MEDIA_E2E, Top5 | Five complete six-stage segment pipelines and real stitch; 82.296 s, AAC, −13.9 LUFS, −2.2 dBFS true peak | e2e/countdown-top5-final.mp4, countdown-probe.json, countdown-quality.json |
| MEDIA_E2E, native | Real 1080×1920 Short, 12.708s, AAC, −14.1 LUFS, −3.8dBFS true peak, no hard errors/warnings; three decoded 1080×1350 carousel cards | e2e/native-short-final.mp4, native-short-probe.json, native-short-manifest.json, native-carousel-*.png |
| Isolation | Zero attempted external network escapes; no scheduler/worker threads; explicit fixture submissions only | e2e/roadmap-final-isolation.json and uniquely named server logs |
| CI / Linux container | Prepared, **NOT RUN**; local Docker daemon unavailable | .github/workflows/quality.yml; scripts/e2e/Dockerfile |
| Real provider / staging | **BLOCKED**: no isolated metered provider-test lane or isolated staging configured; no external generation attempted | $0 spend throughout |

The backend baseline was 301 passed / 3 missing-mock failures / 1 skipped. The
earlier audit's 18 passing probes asserted unsafe old behavior; they were not
safety passes. New acceptance regressions now cover many of those boundaries.
Not every audit probe or requested negative path is closed. Earlier browser
failures were diagnosed and rerun; they are not represented as passing evidence.

The LongForm UI test drives search/create, all stages, unsupported/malformed
checker failures, saved edits, recheck/reload, installed offline speech, actual
render, exact-export approval, enqueue, queue UI fake submission, duplicate API
rejection, UI reconciliation and archive display. The Top5 UI test drives
research, five unique titles, confirmation/manual start, 30 segment stages,
stitch, final inspection, approval and enqueue. The native UI test also drives opportunity creation/review, still import, selected
Short/carousel treatments, actual exports, premature-enqueue rejection, exact
approval, two simulated deliveries, reconciliation, metric import, redacted
comments, reviewed private reply, an undersampled experiment and linked follow-up.
The Countdown test additionally injects one stale done response and proves its
finished view recovers through idle polling. The new source-review test exercises
independent fixture retrieval, explicit owner verdict, narration invalidation,
recheck and durable reload. The project-history test injects one stale interstage HTTP response,
then verifies recovery, same-studio fragment changes, browser back/forward and
reload. Other application responses in the primary media flows remain real.
The navigation test samples all
primary pages, same-document tab changes and 390px mobile presentation; it does
not exhaust every form/button or certify accessibility.

TMDB/research/text/vision, Drive and publishing are deterministic fixture adapters.
Speech uses the installed Samantha voice. Actual stage orchestration, database,
HTTP handlers, scene detection, plan construction and FFmpeg are real. No evidence
here proves real Gemini/vidIQ/IMDb/Drive/Upload-Post compatibility, long-duration
production scale, factual entertainment research quality or audience growth.

## Independent editorial/media inspection

The sampled final frames show original colored shapes, Screen Central overlays,
rank cards, and the end card. Their literal synthetic narration matches this
fixture premise. The generated films deliberately share the same asset corpus;
they do not demonstrate novelty, film identity recognition or excellent creative
storytelling. LongForm holds its end card for two seconds **in the fixture only**;
the production renderer's default remains unchanged.

The final countdown detector flags five dark rank-card spans (~2.57–2.64 s),
which contain visible text in the inspected contact sheet. They remain visible
review warnings, not silently waived measurements. Replacing the silent intro
fallback with original sound removed the prior >3 s silence warning; final
detected silence spans are at most ~2.74 s. Native cards were separately inspected for safe margins and readable text.
Visual QA also corrected an overly specific synthetic excerpt color description;
that fixture wording is not proof of automated semantic verification.
Actual speech pronunciation was not auditioned. Captions, semantic shot alignment, rights of future real assets,
packaging honesty and narrative satisfaction still need editorial validation.

## Finding/task matrix and remaining release blockers

| Audit ID | Status in this checkpoint | Remaining work |
|---|---|---|
| R01 | Retrieval + explicit semantic review workflow; mechanical gates repaired | Autonomous material-claim coverage, cross-source conflicts and broader adversarial phrasing |
| R02 | Typed crew roles; independent source record/review UI | Integrate every structured credit into the material-claim verification contract |
| R03 | Unsafe refs/domain-only admission blocked | Real safe retrieval, redirects/dead pages, semantic support, claim-specific freshness/territory workflow |
| R04 | Partial: new exact counts/duplicates, no citable AI notes, provider conflicts and public stats contained | Category eligibility, supported rationale/trivia and checked intro/outro; manual canonical identity now rechecked |
| R05 | Strict resolver repaired and tested | Category/country criteria and broader adversarial identity corpus; new-list confirmation now revalidates manual edits |
| R06 | Queue intent/claim/uncertainty and enqueue race repaired in local fixtures | Append-only history, explicit owner uncertainty workflow and fenced dispatch locally implemented/tested; external provider proof and stronger in-flight file fencing still needed |
| R07 | Scoped backup access repaired | Broader auth penetration testing remains outside proof |
| R08 | Unsafe Drive path mechanism repaired | Shared/exclusive file claims and Drive/download decode/hash verification implemented locally; every legacy transfer/trash path and stale-temp matrix remain |
| R09 | Partial: bounded atomic video uploads/probes | Cookie/image caps and full-decode corruption corpus now locally tested; full request-ingress/negative UI matrix remains |
| R10 | Partial: invalidation, undo and export fingerprints | Native versions immutable; original studios still need retained export history, every mutation/undo path and active queue file replacement protection |
| R11 | Partial: final-file QA + exact approval integrated in both studios | Captions/relevance/duplicate/rights validators, intentional-dark/silence policy, production-length corpus |
| R12 | PARTIAL: editorial/calendar/imported cohort evidence with API/UI tests | Live authorized analytics adapters, historical content mapping and richer outcome lineage |
| R13 | PARTIAL: actual native Short/carousel UI/media E2E and community draft | Broader creative treatments, single-image/poll flow, recut semantics, production Reel proof and real cleared-content editorial QA |
| R14 | Partial: atomic USD/credit admission and uncertain holds; TTS paths accounted | Orphan USD/credit receipt recovery and Settings UI now locally tested; provider price/bound validation and invoice matching remain |
| R15 | Partial: resumable/Studio/Countdown/native generation leases and output fencing | Legacy scrape/compile and residential protocol v2 now locally integrated/tested; PostgreSQL and clock-skew corpus remain |
| R16 | REPAIRED locally: worker wait stops chain and retry collects same handoff | Actual residential client over localhost HTTP + UI resume passed with synthetic download; real residential/provider lane remains staging work |
| R17 | Core reuse/DTO defects repaired | File-aware lookup, asset reuse without narrative reuse, further campaign-version tests |
| R18 | Partial: frontend scanner clean, PR CI authored | Execute Linux/CI lane, backend pip/pytest advisory triage, image/runtime locks, owner branch-protection/release policy |
| R19 | REPAIRED locally: legacy/modern topic GETs and schedule GET read only | Broader safe-method audit remains useful |
| R20 | Borrowed bundled audio removed/replaced, actual export tested | Rights manifest for every future real media asset and permissions review |
| R21 | Partial: bounded file logs and all queue references protected | DB log retention, cache/transfer claims and Studio checksum-verified deletion added; legacy Countdown/trash/stream consumers still require coverage |
| R22 | Several misleading labels repaired | Recovered same-job failures now disappear after success; full typed platform status UI and deployed severity validation remain |
| R23 | CLI auto-Git removed, generated files untracked, current runbook added | Larger module refactors/current architecture docs and historical documentation reconciliation |
| R24 | Main tabs tested; same-studio history synchronization added | Identity-scoped Studio/Countdown draft recovery, stale-save conflicts and storage-failure navigation passed in the expanded ten-scenario suite; other forms and the full negative matrix remain |
| UI-01/UI-02/UI-03 | Saved-check refresh, plan hook-order crash and interstage polling freeze repaired | Regression browser suite now covers their triggers |

M1–M5 all remain **partial**. M3–M5 now have a usable local prototype with
fixture UI evidence, rather than empty placeholder screens. The live account
adapters, complete creative/asset engine, automatic feedback ingestion and full
acceptance corpus remain unfinished authorized local work. No new permission is
needed to continue local implementation. Production/account/public actions still
require fresh owner approval.

## Rollout, migration, rollback and next work

Do not deploy this checkpoint directly. Review the branch, run the container/CI
lane, finish open P1 contracts, exercise all missing negative UI/recovery paths,
and inspect cleared real-content samples first. Additive schema changes create editorial, observation, campaign, export and asset
tables, plus queue media_kind/media_files and compilation queue_item_id columns.
create_all/_migrate were exercised only with disposable SQLite; PostgreSQL
migration, rollback and real backup/restore have not been verified.
JSON evidence/quality/approval fields remain additive. New countdown cardinality
rules are versioned; legacy records are readable. Old exports have no new QA/hash
approval and must not be silently marked verified. Existing production Ready rows
are not retroactively validated by these enqueue gates.

Before any later owner-approved release: verify backup/restore on a disposable
copy, assess old queue/journal records, preserve superseded artifacts, test one
worker/replica and document upgrade holds. Rolling back after real submissions
must preserve intent/provider receipts and reconcile unknown outcomes before any
retry; never revert to automatic ambiguous retries. Current rollback is simply
discarding the feature branch/worktree after review: main and production are
unchanged. Preserve the ignored artifacts before deleting a worktree.

Next priority: extend M1 material-claim/category/manual-edit contracts; finish M2
publication attempt resolution, immutable storage lifecycle, orphan budget-hold
recovery and legacy worker fencing. Extend negative UI tests, archive/restore
and recovery. Extend M3 live audience lineage, M4 creative quality/assets and M5
comment ingestion/controlled learning beyond the tested local prototype. Use SAFE_LOCAL_TESTING.md and SCRAPPER_RESUME.md; regular
local implementation approval is already granted.

artifacts/test-manifest.md and evidence-manifest.json give exact local proof
paths, conditions, hashes and costs. The accompanying output package includes a
copy of this report, selected synthetic exports/screenshots and the committed diff.


## Continued roadmap checkpoint

The owner requested independent fact verification, then atomic budgets and worker
leases. This local slice implements independent retrieval plus owner review,
transactional reservations and generation fencing for the scoped workers. It does
not complete M1–M5. [LOCAL_CONTRACTS_20261018.md](LOCAL_CONTRACTS_20261018.md) records
architecture, exact behavior, configuration, test coverage and remaining limits.

Next release work: full material-claim coverage and cross-source conflicts;
verified provider price/usage contracts and orphan/credit hold reconciliation;
legacy scrape/compile and residential-worker lease integration; append-only
publication attempts and explicit unknown-delivery resolution; immutable storage
lifecycle/restore guarantees. Real-provider tests, PostgreSQL, CI/container and
production-scale verification remain unrun. Keep this work local unless the owner
later changes that constraint. No implementation approval question is pending.
