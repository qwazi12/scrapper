# Structured cast credit and title context

The deterministic narration guard keeps complete canonical performer/character
names before interpreting suffix prose. Periods in names, character names such as
“The Man in the Wall” and parenthetical voice labels do not change identity.
Explicit `in/from <title>` qualifiers must name the current canonical title (or an
explicit original title if present), with the canonical primary year when a year
is supplied. Film/series wrappers must agree with a known medium. Complete titles
are compared before stripping wrappers/year/alias separators, so a literal title
beginning “The Movie”, containing a parenthesized number or slash is preserved.

Every offered slash/aka role must match the canonical roles. One correct role
cannot conceal an additional invented role. Forward and reversed known-performer
credit patterns use the same bounded name/context decision. The guard reports a
role/performer and title-context conflict, retains owner words, invalidates stale
plan/render data and blocks downstream readiness even if the storyboard checker
returned `ok`. It does not rewrite the sentence automatically.

This is a bounded typed-credit guard, not a complete natural-language fact engine.
Existing following-clause treatment remains; comprehensive claim coverage,
fully unseen-entity expressions, unlisted writing credits, more prose/alias/language forms and external
source truth/freshness remain C01 work. TMDB fixture agreement is not independent
real-world verification. Web findings still require retained, fresh exact-context
owner-reviewed source records; domain labels/model votes alone do not authorize R.

Evidence: new synthetic corpus reproduced9 failures/2passes before repair. Focused
67passed1.11s after repair, including actual owner PATCH/GET and persisted readiness
round trips. Full backend699passed/1optional skip78.22s; actualPG223passed34.43s plus
native transactional restore1.323s/source unchanged. First browser run: two existing
LongForm/source-review flows passed; new test observed an old done status before
its run request completed. Saved DB later showed the correct completed result.
Corrected fresh case waits for accepted run HTTP before completion:1passed27.2s,
six exact saved inputs cover valid names/context, wrong title/year/extra role,
visible blocked voice/render gates and repaired readiness. Actual UI/HTTP/workers/
DB run with synthetic canonical credits and an always-approving fixture checker.
No real actor fact, provider or paid-network proof is claimed. Evidence is under
artifacts/e2e/history/material-credit-context1 and material-credit-context2.

Known-role forward subjects now also bind the canonical performer set. An unlisted
performer, even with the right role words or a different named film, cannot inherit
that role through a model-approved statement. Longest complete role names win
before suffix interpretation. Bounded actor/actress/performer wrappers and an
explicit canonical locative prefix preserve known names. This is not a general
entity recognizer: unseen roles/prose and unlisted screenplay writers remain work.
New corpus reproduced4failures/4baseline passes, expanded focused77passed1.05s.
Full backend709passed/1skip79.93s; actualPG233passed34.44s/native restore0.990s.
Actual current browser2passed1.0min, full LongForm media plus seven saved credit
states including unknown forward subject/repaired readiness. Evidence
artifacts/e2e/history/unlisted-performer1; no real-world cast or paid proof.

## Current-facts admission, including cached legacy checks

`script_issues` and `require_checked` now require the caller's current facts and
re-run deterministic conflicts in addition to the retained storyboard/source
checks. All production readiness, narration plan, render, saved verification,
owner export approval, Studio enqueue and Countdown segment/bookend validation
call sites pass the actual project facts. A historical cached model `ok` with no
recorded issues cannot bypass a newer deterministic rule. Readiness calculation
is read-only: old checks/words/history remain retained, while new media/release
admission blocks until the owner repairs/rechecks. This does not independently
verify every provider fact or solve detached older queue provenance.

Five regressions reproduced false readiness or the wrong later failure for a
synthetic historical green. Focused68passed1.20s; full backend714passed/1skip73.34s;
actualPG238passed32.15s plus native restore1.306s/source unchanged. Actual UI3passed
1.1min (LongForm media, credit variants, cached green read/forced plan/refusal and
word-preserving repair). Fresh screenshot refinement1passed9s waits for the loaded
exact words/block after reload; the prior pre-load capture is retained separately.
Evidence history/live-research-admission1/2, source
62791e5349e0b1dbf7b3c244cf088037ee86d319eba0e43064a32bc4eb6442b9.

Next critical source boundary: existing original Studio queue rows contain summary
metadata, with no immutable script/facts/export reference enforced by dispatch.
New source bindings and fail-closed legacy review are required before calling
current factual admission complete through publication. Unlisted writing credits
also remain a confirmed separate C01 gap.
