# Scrapper Memory

Persistent log of everything this scraper has downloaded. Appended
automatically on every run of `scrape.py`, committed to git so history
survives across machines.

- **Repo:** https://github.com/qwazi12/scrapper
- **Engine:** [yt-dlp](https://github.com/yt-dlp/yt-dlp) (handles X/Twitter, YouTube, TikTok, IG, ~1800 sites)
- **Videos live in:** `downloads/` (local only, gitignored)
- **Dedup ledger:** `archive.txt` — delete a line to allow re-download
- **Full logs:** `logs/scraper.log` (every yt-dlp line) and `logs/manifest.jsonl` (one JSON record per video)
- **Web app — LIVE:** [`BUILD_PLAN.md`](BUILD_PLAN.md) — storyboard web app deployed. **App: https://scrapper.nodepilot.dev** (Vercel frontend) ↔ backend https://scrapper-production-d348.up.railway.app (Railway, FastAPI + volume). Enter the access token (in `data/access_token.txt`, gitignored) once via the ⚙ Connection panel. Verified end-to-end in production: X scrape → compile → download.
- **Reliability:** all 5 anti-blocking layers implemented — nightly yt-dlp self-update, burner cookies upload, retries+backoff, proxy support, standalone hybrid worker — plus observability (per-source success tracking + alert webhook). See BUILD_PLAN §10.
- **Bulk actions:** Storyboard has a select-all checkbox + "🗑 Delete N" (deletes checked clips + their files); Export has per-row checkboxes, select-all, and "🗑 Delete N".
- **Anti-ban pacing (platform-aware):** `-t sleep` is ON by default (`YTDLP_SLEEP_PRESET`) for X/TikTok — 0.75s between requests + randomized 10–20s between downloads, ~40s/clip. **YouTube must NOT get the 10–20s pre-download sleep**: its media URLs return `HTTP 403` when fetched that long after extraction (verified by isolating the flags). YouTube gets `--sleep-requests 0.75` only. See `pacing_args()` in `backend/core/scraper.py`.
- **YouTube on Railway is blocked by IP, not by login.** The datacenter IP gets "Sign in to confirm you're not a bot"; the same URL succeeds from a residential IP with no cookies at all. Cookies do NOT fix an IP block, and pushing account cookies through a datacenter IP is what gets Google accounts flagged — so route around it with the local worker instead.
- **Local worker (the YouTube fix):** `scripts/local_worker.py` polls `GET /api/clips/blocked`, downloads on the home connection, and uploads via `POST /api/clips/{id}/upload`. `scripts/install_local_worker.sh` runs it under launchd. Blocked URLs fail fast (no retries) and wait in that queue.
- **Gotchas that cost real debugging time:** yt-dlp writes typographic quotes (`you’re`, U+2019) so error matching must fold curly quotes; FastAPI needs `Form(...)` on multipart text fields or it reads them as query params and silently drops them; macOS python.org builds have no CA store, so scripts need a certifi SSL context.
- **Cookies:** `--cookies-from-browser` (`COOKIES_FROM_BROWSER`) only works where a browser profile exists (your Mac / hybrid worker), never on Railway. For the cloud use `scripts/sync_cookies.py` (+ `install_cookie_sync.sh` for a daily launchd job) to push browser cookies to `/api/cookies`. Use a BURNER account — a cookie jar is a live credential.
- **Retention:** `RETENTION_DAYS=5` — per-item rolling TTL from each row's `created_at` (scraped Thursday → expires Tuesday), swept every 30m by the worker, skipping queued/running items. `0` disables. UI shows an "expires" countdown.
- **Self-healing:** a "↻ Rescan library" button + startup reconcile rebuilds the DB from video files on the volume and keeps the DB / files / `archive.txt` in sync. Deleting a clip now also clears its id from `archive.txt` so it can be re-scraped (no more phantom "skipped"). Auth also accepts `?token=` for download/thumbnail/SSE (browser GETs can't send headers).

## How to use

```bash
# add links to urls.txt, then:
python3 scrape.py

# or download one-off links directly:
python3 scrape.py https://x.com/user/status/123...
```

---

## Run — 2026-07-20 18:08

- ✅ **polo_man404** — PoloMan - If Studs wanna act like men their azzes gonna get treated like men. L... (`polo_man404_2077949789837111296_PoloMan - If Studs wanna act like men their azzes gonna get .mp4`)
- ✅ **omoelerinjare1** — NOLLY - New to the neighborhood and already trying to be HOA President😂 (`omoelerinjare1_2079200500738899968_NOLLY - New to the neighborhood and already trying to be HOA.mp4`)
- ✅ **Angrycomicgirl** — Angrycomicgirl - Not all hero’s wear capes 🫡 (`Angrycomicgirl_2079078704110596096_Angrycomicgirl - Not all hero’s wear capes 🫡.mp4`)
- ✅ **rizzolosophy** — The Rizz Bible - Don't let this happen to you. You're suppose to disengage as soon as ... (`rizzolosophy_2079215895717507072_The Rizz Bible - Don't let this happen to you. You're suppos.mp4`)
- ✅ **Daveay7** — DaveAY - Kevin Samuel said "Winter is coming" (`Daveay7_2079156994741555200_DaveAY - Kevin Samuel said ＂Winter is coming＂.mp4`)
- ✅ **Daveay7** — DaveAY - More reasons men don't want to approach. (`Daveay7_2079176754871087104_DaveAY - More reasons men don't want to approach..mp4`)
- ✅ **susylicious2023** — Susan Abel - My wife left me for an "alpha male". !..now she wants me back! (`susylicious2023_2079254965273477121_Susan Abel - My wife left me for an ＂alpha male＂. !..now she.mp4`)
- ✅ **susylicious2023** — Susan Abel - I caught my serious girlfriend trying to baby trap me (`susylicious2023_2078463070091784193_Susan Abel - I caught my serious girlfriend trying to baby t.mp4`)
- ✅ **Cat5SMASHICANE** — Johnny B. Good - How about a good laugh for the morning? Family Fued never disappoints... (`Cat5SMASHICANE_2078831224911486976_Johnny B. Good - How about a good laugh for the morning？ Fam.mp4`)
