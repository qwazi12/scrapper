# Review of the Codex branch and its plan — 2026-10-19 (Claude)

Branch reviewed: `codex/scrapper-revival-full-stack-20261018` (73 commits, 286 files,
+29,433 / −2,789). Base: main `95b6bd4`. Released to production as `0e5e500` on
2026-10-19 06:15 ET.

## Production incident caused by this review (my fault)
A docs-only commit to `main` (1ce5269, 07:18 ET) triggered Railway's auto-deploy and
replaced the release backend with `main` code (deployment 167f04f2 REMOVED →
4e19c2db). Vercel still serves `0e5e500`. Branch-only routes 404 (e.g.
`/api/costs/reservations`). Auto-posting is PAUSED, so nothing public happened.
Restore command (owner runs it; production deploys need owner permission):

    cd ~/dev/scrapper && git worktree add --detach /tmp/rel0e5e500 0e5e500 && cd /tmp/rel0e5e500 \
      && railway up . --path-as-root -p 4ec42c6b-97e6-43f4-91bb-1ae34cf6dc57 -e production -s scrapper --detach

Nothing more is pushed to `main` until the branch is merged.

## Evidence I gathered (not taken from Codex's report)
- Backend tests on the branch, on this Mac: **868 passed, 1 skipped (91 s)**.
- Countdown eligibility parser on 18 real topics (today's list + the owner's line): all
  parsed sensibly (genre/provider/decade/medium); none blocked at parse time.
- All owner features from 2026-10-13…17 are still present on the branch (catch-up,
  default Cinama destination, Instagram caption fitting, Top-N wording, Your line,
  voiced #N cards, intro palettes, Charon style).
- Production logs: no Studio/Countdown activity between the release (10:17 UTC) and
  the revert (11:19 UTC), so there is no real-world evidence yet of how the new gates
  behave on daily automation.

## What is good — keep
1. Posting: one upload per platform (one platform's rejection no longer fails the
   others); intent saved before upload; duplicate-submission guard (fixes the #749
   re-post race found on 10-19); results must cover exactly the platforms sent.
2. Security: media pass can't download private backups; Drive temp-path hardening.
3. Backups: verified SQLite snapshots, offline restore, PostgreSQL option.
4. Research: catches wrong actor↔role pairings and title/year mismatches (the
   Dune/Pattinson class of error).
5. Countdowns: exact entry count, no duplicates, no silent fill/shrink, category rules.
6. Real test harness: backend, PostgreSQL and browser suites.

## Problems, ranked
1. **Every posting error becomes manual paperwork.** Any exception — including a
   definite rejection such as Upload-Post HTTP 400 "Instagram description too long" —
   leaves the row in Posting with state "unknown". Resolving needs a per-platform form
   (HTTPS evidence URL, rationale, confirmation) and the row returns to Needs review,
   not Ready. The 3-hour "still processing" timeout no longer fails anything; rows can
   sit in Posting indefinitely. Fix: treat definite 4xx rejections as not accepted
   (retry after fixing); keep "unknown" only for timeouts / lost responses; add a
   one-tap "didn't post — retry" for the owner; surface stuck rows in Errors/Retry.
2. **Three uploads per video** instead of one per profile (YouTube, Facebook,
   Instagram each upload the file). Check the Upload-Post Basic plan's upload count.
3. **Heavier sign-off before the queue.** LongForm and Countdown sends need four
   checkboxes, a ≥20-character rights statement and a decision on every warning
   (silence > 3 s, black frames, repeated visuals, length outside ±25 %); Countdown
   intro/outro must be reviewed before voicing. Fix: one "Approve" with a remembered
   standard rights statement; only severe warnings block.
4. **Fact gate can stall automation.** The script step raises "Research blocked" on
   regex-based credit/role checks and stops the whole breakdown. Fix: drop or rewrite
   the flagged sentence, continue, and flag it for review.
5. **Scope drift.** About half the new code is "editorial OS" features nobody asked for
   yet (comment inbox, audience learning, experiments, asset catalog, native
   carousels/polls/campaigns, channel archive, financial holds). Mostly fixture-tested
   and waiting on accounts/OAuth that aren't connected. Hide behind a setting until
   they're wired to real data.
6. **Maintainability.** Very dense one-line code, imports inside functions, ~900-line
   change to countdown.py. Harder to debug.
7. **Release from a side branch** left main behind and broke the "GitHub matches
   deployed" rule; it directly caused today's overwrite.
8. **Open failures (Codex's own record):** Linux browser 29/33; Top 5 countdown
   audio/video mismatch > 250 ms with the Linux test voice; a 409 media-ownership
   refusal in the legacy-upgrade case; worker modules missing from the test image.
9. **Posting paused** since 06:14 ET.

## Codex's plan (SCRAPPER_CODEX_UNATTENDED_EXECUTION.md) and what remains
Milestones M1–M5: research truth + security, publisher safety + media validation,
audience intelligence, native formats + assets, engagement + learning; plus real UI
end-to-end tests. Per REMAINING_ROADMAP.md: C03 verified locally; C01, C02, C04–C13
partial; C14 continuous; A01 channel archive deferred by the owner.

Advice on the remaining roadmap:
- **Do now** (affects daily output): C05 audio/video sync (check on a real render
  first); C13 typed per-platform posting status, together with problem 1; C11/C12
  rollout safety (merge to main, stop-old/start-new, CI green on Linux).
- **Do once real accounts are connected**: C08 audience adapters (YouTube Analytics
  first — the same feed the trend researcher needs), then C09 comments and C10
  learning.
- **Park**: C07 extra formats (carousels, polls, recuts), C06 semantic beat/music
  matching, C02 specialised award/box-office evidence, A01 archive.

## Recommended order
1. Restore the release (command above).
2. Merge the branch into main (owner approval).
3. Fix problems 1, 3 and 4, with tests.
4. Prove one real LongForm and one real Countdown end to end in production
   (render → approve → post) — the proof Codex could not do.
5. Turn posting back on.
6. Then the roadmap items above, plus: the 640 deleted queue rows (Undo #329 still
   available), MEMORY.md restructure, handbook update, intro palette, release-date
   check on manual starts, catch-up wait cap.
