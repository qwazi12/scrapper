# Exact export acceptance

LongForm and Countdowns expose an owner-only `export-review` endpoint. It returns a version of the inspected video, active thumbnail, script, plan, facts/research, shot catalog, source context, creation time and machine inspection. Countdown admission also rechecks its bookends and current segment fingerprints. Reviewing an old screen cannot approve newer files: the submitted version must match current inputs under file claims.

The owner must inspect the complete export and explicitly check captions (or a deliberate treatment without them), visual relevance/repetitions, asset permissions, and narration/facts/packaging. Every measured warning and repeated visual requires its own substantive editorial decision. Black and long silent intervals include timestamps. The rights record describes intended use and permissions; it is an owner attestation, never automatic legal clearance. Editing any displayed decision clears the final acknowledgment. Errors remain blocked; decisions do not override decoding errors or unchecked research.

Approval schema2 stores the exact version, four checks, current warning decisions and rights rationale. Old single-click approvals must be reviewed again. Publishing revalidates the current inspection and acceptance record. Existing retained exports are preserved. The API rejects missing bodies, missing checks, stale versions and stale/missing finding decisions. An approval is a local editorial record; it does not itself publish.

Objective checks do not establish word-level speech alignment, caption OCR/contrast in every final frame, semantic scene identity, compelling narration, licensing or editorial excellence. Those remain explicit inspection requirements. Native campaigns now use the same structured acceptance mechanism with format-appropriate checks. Their fingerprint binds export/campaign identity, native format, complete manifest (including inspection, retained inputs, copy, files and delivery policy), and the check policy. Reused delivered source images require individual repetition decisions. Legacy blanket native approval is rejected. Enqueue, private manual download and publishing revalidate the acceptance record; publishing checks it again under the media claim. Single-image/Reel transport keeps its own copy and media kind. No native approval certifies facts or permissions.

Evidence: `export-acceptance-backend3`593 backend passes/1optional skip; `export-acceptance-pg3`122 PostgreSQL passes; production build2; `export-acceptance-browser4`2 actual browser flows (LongForm and five-segment Top5) with original synthetic media and simulated providers. Ten-minute procedural corpus is an objective duration/decode/span stress case only:600 seconds,2,636,742 bytes, original black frames/generated tone, no source media or paid call. Its manifest explicitly distinguishes that limited purpose from narrative quality.

Current native verification:661 backend passes/1optional skip,185 actual PostgreSQL passes plus native recovery, focused native gates and production build passed. Four actual media UI workflows passed (2.2min), followed by a fresh single-image/manual visual-capture pass (3.7s); exact evidence is retained in the current report/progress ledger. Earlier evidence below remains historical.

Explicit native video intervals retain silent-excerpt/source/range/presentation hashes. A held final frame or loop extending a selected interval produces its own required finding decision. Source excerpts are integrity-checked along with final media; source or selected treatment changes require a fresh version. See NATIVE_CLIPS.md.

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
