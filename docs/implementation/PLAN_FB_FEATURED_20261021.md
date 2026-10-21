# Plan — 2026-10-21 — Facebook favourites (Doctor Who + The Rookie) and no FB Stories — AWAITING OWNER APPROVAL

Owner request: post DoctorWhoShorts and RockMovie777 Shorts (especially The Rookie) to Facebook 2–3 a day;
stop Shorts posted to Facebook also appearing in Stories.

## Part A — Stop Facebook Stories (small, low risk)
- Cause: `upload_post.upload_video` (backend/app/social/upload_post.py) never tells Upload-Post anything about
  Stories. Upload-Post documents a `facebook_no_story` flag; without it the Reel is also shared to the Page story.
- Fix: send `facebook_no_story=true` (and `facebook_media_type=REELS` explicitly) on every upload that includes Facebook.
- Test: unit test that the Facebook form fields contain both; Instagram/YouTube fields unchanged.
- Verify: the next Facebook Short posts as a Reel and does not show in the Page's Stories.
- If a story still appears: it is Meta's Page setting "Share your reels to your story" (Meta Business Suite →
  Settings → Reels); the owner turns it off there (I can't change account settings).

## Part B — Facebook favourites queue (Doctor Who + The Rookie)
0. Measure (read-only): how many Ready Shorts come from DoctorWhoShorts and RockMovie777, how many are The Rookie,
   and how many already went to Facebook. Tells us how many days the 3/day lasts.
1. New queue group "FB Favourites": any Short whose source channel is DoctorWhoShorts or RockMovie777 posts under it
   (one rule in `pipeline_group`, next to the existing "Movie Clips /" rule; channel list in settings, not hard-coded).
2. Schedule (America/New_York), in the gaps between today's Facebook Shorts/LongForm/Countdown times:
   Facebook 10:00, 14:00, 18:00 → 3 a day. Order: The Rookie, Doctor Who, The Rookie (Rookie first, then alternate).
3. Destinations — owner decision E2 (below).
4. Regular Shorts queue is unchanged (still FB/IG 8:00, 11:00, 15:00, 19:00; YT 9:00, 17:00) — it just no longer
   contains these two channels. Facebook Shorts per day goes from 4 to 7.
5. Tests: grouping rule, the 3 FB times, Rookie-first ordering, regular queue untouched.
6. Verify live: the queue page shows "FB Favourites · 3/day" with next times; first post goes out at 10:00/14:00/18:00.
7. Rollback: `git revert` + remove the group from Settings → schedule; videos fall back into the regular Shorts queue.

## Owner decisions
- E1 — How many a day: 3 (10:00, 14:00, 18:00) recommended, or 2 (11:30, 18:00)?
- E2 — Where these videos go:
  - (B, recommended) Facebook only — YouTube/Instagram are removed from these two channels' rows (they would
    otherwise use up the 2 YouTube slots a day); or
  - (A) Facebook 3/day, plus YouTube and Instagram once a day each from this queue.
- E3 — Rookie weighting: 2 Rookie + 1 Doctor Who a day (recommended, "especially the Rookie"), or equal.

## Order
Part A first (one commit, deploy, check next FB post), then Part B (one commit), each pushed only when Studio is idle.
