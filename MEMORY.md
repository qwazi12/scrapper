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
- **Local worker (the YouTube fix):** `scripts/local_worker.py` polls `GET /api/clips/blocked`, downloads on the home connection, and uploads via `POST /api/clips/{id}/upload`. `scripts/install_local_worker.sh` runs it under launchd. Blocked URLs fail fast (no retries) and wait in that queue. **2026-09-29, three bugs that meant nothing ever arrived:** (1) rescan (every startup + ↻ button) pruned failed rows with no file — the whole blocked queue; `rescan_library()` now skips them. (2) The worker sent the full yt-dlp info.json as the `meta` form field; YouTube's is >1 MB and Starlette 400s any field over 1 MB, so it now sends only the fields the server reads. (3) launchd ran it with `--browser safari`, which needs Full Disk Access and failed every time with EPERM — dropped; no cookies are needed from the home IP. Verified end-to-end: a 283 MB YouTube video downloaded at home and uploaded to Railway. PO tokens don't help (the block is by IP). The launchd wrapper is `~/scrapper-worker/run.sh` (python `-u` so `~/scrapper-worker/worker.log` isn't empty). (4) launchd's PATH is only `/usr/bin:/bin:/usr/sbin:/sbin`, so the worker failed with `No such file or directory: 'yt-dlp'` (and couldn't find node/ffmpeg either) — `run.sh` now exports a full PATH. Manual runs hid this because they inherit the shell's PATH: **test the worker through launchd, not by hand.** Verified under launchd 2026-09-29 (clip #2). `run.sh` lives outside the repo and isn't version-controlled.
- **UI & local worker resilience (2026-09-29):** (1) Fixed confusing failure messaging: when Railway is bot-blocked by YouTube, instead of `[scrape_fail] 0 ok, 1 failed`, it logs `[queued_for_local_worker] Railway blocked by YouTube — transferred to your Mac worker for residential download.` and `[ingest_done] job X: 1 queued for Mac worker`. (2) Frontend clip table shows `● queued for mac` in yellow instead of `● failed`. (3) `scripts/local_worker.py` now logs timestamps, emits periodic heartbeat (`checked queue: 0 blocked URLs`) so logs aren't mysteriously silent, and retries uploads 3x with backoff to survive Railway restarts without re-downloading. Verified end-to-end with 606 MB video #3 (`EavKQdjU4p8`).
- **Future-proofing upgrades (P1–P3, 2026-09-29):**
  - **P1 (Optimal 1080p Format Sorting):** Added `--format-sort "res:1080"` to both `backend/core/scraper.py` and `scripts/local_worker.py`. Caps video height/width to 1080p, preventing bloated 4K/8K downloads, cutting file transfer sizes by ~75% while perfectly matching Scrapper's 1080p compilation canvas (both portrait 1080x1920 and landscape 1920x1080).
  - **P2 (Fast 15-second Polling):** Dropped worker queue polling interval from 120s to 15s in `scripts/local_worker.py` and `~/scrapper-worker/run.sh`. Queued clips are now picked up within ~15s instead of 2 minutes. Empty queue log is throttled to 2m heartbeats to keep logs clean.
  - **P3 (CI/CD & Deployment Synchronization):** Added `.github/workflows/deploy-frontend.yml` and `scripts/deploy_frontend.sh` to ensure Vercel frontend and Railway backend stay in sync on every push to `main`.
- **Social Media Publishing via Outstand (2026-09-30):**
  - **Decoupled from Google Cloud:** Eliminated Cloud Run, Google Drive, and Google Sheets completely. Scrapper runs entirely on Railway (backend + volume storage) + Vercel (frontend) + Outstand (unified social publishing).
  - **Provider Migration (Blotato -> Outstand):** Switched to Outstand API (`https://api.outstand.so/v1`) using `OUTSTAND_API_KEY` stored securely in Railway env vars.
  - **3-step Presigned Video Upload:** In `backend/app/social/outstand.py`, compilation videos are uploaded directly from Railway disk to Outstand (`/v1/media/upload` -> `PUT upload_url` -> `/v1/media/{id}/confirm`).
  - **Multi-Platform Support:** Supports posting and scheduling to TikTok, YouTube, Instagram, X, Facebook, LinkedIn, Threads, and Bluesky via `POST /v1/posts/`.
  - **AI Metadata Generator:** In `backend/app/social/metadata.py`, uses Gemini REST API to generate viral titles, hooks, captions, and hashtags from compilation clip titles, with a deterministic template fallback if `GEMINI_API_KEY` is omitted.
  - **Interactive Publishing UI:** Added `PublishModal.tsx` in frontend with dynamic connected accounts list, 1-click AI caption generation, datetime scheduling picker (up to 30 days), and real-time status feedback.
- **Posting Queue & Content Calendar (Replacing Google Sheets, 2026-09-30):**
  - **Full Google Sheets Schema Brought In:** Replaced the fragile Google Sheets queue from `socialpilot_Ai` with a database-backed `QueueItem` model matching all 9 columns: `ID | Video Name | Drive Link | Title | Description | Tags | Status | Notes | Source`.
  - **Status Lifecycle:** `Review` (new video, needs review/approval) -> `Ready to post` (approved & scheduled) -> `Posting` (active Outstand upload) -> `Posted` / `Retry` / `Error` -> `Archived`.
  - **Ops Dashboard (`QueuePanel.tsx`):** Added a dedicated command-center table with filter pills (Needs Review, Ready to Post, Scheduled, Posted Archive, Errors), pipeline dropdown (Movie Clips, Abyss Declassified, The ICK Room, Default), bulk actions (Approve, Delete, Archive), and inline row edit modals.
  - **1-Click Queue Additions:** Added `+ Queue` buttons to both Storyboard clips and Export compilations.
  - **Background Scheduler Daemon:** In `backend/app/social/queue_manager.py`, a background thread polls every 30s for due `ready` items and executes Outstand publishing automatically without human intervention.
  - **Google Drive Integration Clarification:** Videos are stored locally on Railway's persistent volume (`/data/downloads` and `/data/compilations`) and uploaded directly to Outstand via binary presigned S3/R2 flow. Google Drive is no longer required as a posting middleman, but `drive_link` is retained in each queue row for optional cloud drive referencing.
- **Repo location:** `~/dev/scrapper`. It was moved off `~/Desktop` because iCloud evicts files there (`dataless`), which made python/git hang and stopped launchd from exec'ing scripts (TCC "Operation not permitted").
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

---

## Architecture Update — 2026-09-30

### 1. Reactive Studio Sidebar & Dedicated Views
- Replaced the stacked single-page feed with a responsive left navigation sidebar featuring live item badges:
  - 📋 **Posting Queue & Schedule** (`X items`) — Dedicated full-width Google Sheets spreadsheet view.
  - 🎬 **Storyboard & Clips** (`X clips`) — Trimming, aspect ratio selection, video player.
  - 📥 **Ingest & Scraper** — Ingestion queue, Mac worker residential status.
  - 🎞️ **Compilations & Exports** (`X comps`) — Multi-clip stitcher, transitions, audio tracks.
  - ☁️ **Google Drive Sync** — Direct Google Drive folder ingestion and channel mapping.
  - 📜 **Live Activity Logs** — Real-time SSE worker feed.
  - ⚙️ **Settings & Channels** — Outstand connected accounts (Flamingo Remix, etc.) and volume storage health.
  - 🌟 **All-in-One Studio** — Stacked overview.

### 2. Google Sheets Replacement in Scrapper
- 9-column data model (`id`, `video_name`, `drive_link`, `title`, `description`, `tags`, `status`, `notes`, `source`).
- Lifecycle status tracking: `review` ➔ `ready` ➔ `posting` ➔ `posted` / `retry` / `error` ➔ `archived`.
- Automated background publishing daemon running in FastAPI backend every 30s.
- Multi-channel filtering: `Movie Clips`, `Abyss Declassified`, `The ICK Room`, `Default`.

### 3. Google Drive Ingestion
- UI Panel: `frontend/app/DrivePanel.tsx` allows entering Google Drive folder URL/ID and destination channel.
- CLI Tool: `scripts/sync_drive.py` allows Mac worker or automation to scan and queue videos from Google Drive folders directly into Scrapper.

### 4. Landing Page Core Experience & Direct Device Download
- Landing Page (`/`): Pure original Scrapper view. User pastes link(s), clicks Scrape, and the clips table shows the video with a direct **⬇ Download** button to immediately download the MP4 file to their device.
- Direct Clip Download API: `GET /api/clips/{clip_id}/download` added to FastAPI backend, streaming the raw MP4 file directly to the user's browser with the video title as filename.
- Sidebar Navigation: All other secondary workflows (Posting Queue, Compilations, Google Drive Ingestion, Live Logs, Settings) are housed cleanly in the sidebar as separate views.

### 5. SocialPilot Retention Exemption & System Separation
- **Strict Retention Exemption:** In `backend/app/cleanup.py`, any clip or compilation referenced in `QueueItem` (status: `review`, `ready`, `posting`, `posted`, `retry`) is explicitly filtered out and protected from the 30-minute rolling retention sweep (`RETENTION_DAYS=5`). Scheduled content will never be deleted before or during posting.
- **Dedicated Sidebar Tab:** Isolated SocialPilot into its own dedicated **"🚀 SocialPilot AI"** tab containing the Google Sheets posting queue and Google Drive channel ingestion tools.
- **Permanent Cloud Archive:** Videos in SocialPilot reside in Google Drive as the permanent repository; Scrapper reads and distributes them to Outstand without clogging Railway volume disk space.

