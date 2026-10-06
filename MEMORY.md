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
  - **SocialPilot AI Retention Exemption:** Videos and records in the SocialPilot posting queue and compilations are **strictly exempt from the 5-day retention sweep** (`backend/app/cleanup.py`) — until posted: see the 4-day archive cleanup (2026-09-30 audit below).
  - **Multi-Channel Google Drive Ingestion & Organization (2026-09-30):**
    - Connected Google Drive parent folder `1kuOKRQQRL0ws5aOVqwkdUzdnfj5KQGjo` (`Movie Clips`) using service account `omnistream-bot@manhwa-engine.iam.gserviceaccount.com` (configured on Railway as `GOOGLE_SERVICE_ACCOUNT_JSON`).
    - Multi-channel engine in `backend/app/drive_sync.py` and `scripts/sync_drive.py` scans subfolders as distinct channel handles (`@VynixAE`, `@PixelDrift-f3c`, `@SolarrEditss`, `@AlphaReels-1`, `@EditAetheris`, `@CoruscateCuts`, `@FrameLegion`, `@roebutt`, `@TheUsJournal17`, `@SceneVale`, `@QianaLucy`, `@clipscav`, `@hanganhoang3071`, `@comet-cinema`).
    - Synced 700 rows — **but Drive actually holds 690**: 10 `@SolarrEditss` videos were imported twice (deleted 2026-09-30; a unique index on `drive_link` now blocks repeats). Originally synced 700 ready-to-post short videos into Railway PostgreSQL with direct Google Drive view links, extracted hashtags, and cleaned titles.
    - Updated folder tracking so parent folder `Movie Clips` includes all 700 items across all 14 channels, while each row displays its exact folder breadcrumb (e.g. `Movie Clips / @PixelDrift-f3c`).
  - **Mix & Shuffle Suite (`/api/queue/shuffle`):**
    - `Round-Robin Mix`: Interleaves items across all 14 channels so consecutive posts alternate creators and maintain channel diversity (matching `mix_videos_roundrobin.js` from `socialpilot_Ai`).
    - `Full Random Shuffle`: Global Fisher-Yates random ordering.
    - `By-Channel Shuffle`: Shuffles within each channel bucket.
  - **Mass Select & Status Change (`/api/queue/bulk-action`):**
    - Select all or visible items, mass change status to `Ready to Post`, `Needs Review`, `Posted`, or `Archived`.
    - Mass assign target Outstand social accounts (e.g. YouTube, TikTok, IG, FB).
  - **Social Media Platform Wiring via Outstand:**
    - Table includes `Target Social Accounts` column displaying platform badges (e.g. `▶️ @flamingoremix` (YouTube)).
    - Automatic on-demand streaming from Google Drive for posting: when a video from Drive is published, `queue_manager.py` downloads it temporarily, uploads to Outstand, and immediately cleans up the local file, keeping disk usage zero.
  - **Bulk Channel Scraper & Ingest (`ChannelIngestPanel.tsx` & `/api/social/ingest-channel`):**
    - Dedicated tab inside SocialPilot AI to paste a channel shorts URL (e.g. `https://www.youtube.com/@Channel/shorts`).
    - Automatically discovers or creates the channel subfolder under `Movie Clips` in Google Drive, uploads the 1080p MP4 directly, deletes local temp files, and adds to queue.
  - **Error & Retry Inspector:**
    - Filter tab `⚠️ Errors / Retry` shows error logs and failure reasons with a one-click `↻ Retry All Failed` button.
- **Gemini 3.8 Flash Text-to-Speech (TTS, 2026-10-02):**
  - **Models:** `gemini-3.8-flash-tts` (creative fidelity, acting, nuanced prosody) and `gemini-3.8-flash-lite-tts` (fast bulk shorts hooks).
  - **Shared Key:** Runs natively on the existing `GEMINI_API_KEY` via Google Interactions API (`POST /v1beta/interactions`) with zero extra SaaS subscriptions.
  - **Emotional Styling & Inline Vocal Tags:** Separates verbatim transcript from turn-level `speech_metadata.style` (e.g. "urgent and dramatic build-up", "whispered urgently") and native inline vocal bursts (`<gasp>`, `<sigh>`, `<short pause>`, `<chuckle>`, `<throat-clearing>`).
  - **Multi-Speaker Cadence & Overlaps:** Native support for 2-speaker conversational dialogue with pipe backchannels (`|oh really?|`, `|reaction|`) for natural overlapping speech.
  - **Studio Voices:** 30 prebuilt curated voices (`Puck`, `Kore`, `Fenrir`, `Charon`, `Aoede`, `Zephyr`, `Algenib`, etc.).
  - **App Integration:**
    - Backend: `backend/app/social/tts.py` provides `synthesize_speech()`, `synthesize_dialogue()`, and `generate_hook_voiceover()`.
    - API endpoints: `GET /api/tts/voices`, `POST /api/tts/generate`, `GET /api/tts/audio/{filename}`, `POST /api/tts/check`, `POST /api/queue/{id}/generate-voiceover`.
    - Web UI: Added **🎙️ Gemini 3.8 Flash Voice Studio** in `SettingsOverview.tsx` for instant interactive voice synthesis, testing, and downloads, plus 1-click `🎙️ Voice` hook generation in `QueuePanel.tsx` with inline audio player.
- **Repo location:** `~/Desktop/scrapper` & `~/dev/scrapper` (mapped to `qwazi12/scrapper`).

- **Gotchas that cost real debugging time:** yt-dlp writes typographic quotes (`you’re`, U+2019) so error matching must fold curly quotes; FastAPI needs `Form(...)` on multipart text fields or it reads them as query params and silently drops them; macOS python.org builds have no CA store, so scripts need a certifi SSL context.
- **Cookies:** `--cookies-from-browser` (`COOKIES_FROM_BROWSER`) only works where a browser profile exists (your Mac / hybrid worker), never on Railway. For the cloud use `scripts/sync_cookies.py` (+ `install_cookie_sync.sh` for a daily launchd job) to push browser cookies to `/api/cookies`. Use a BURNER account — a cookie jar is a live credential.
- **Retention:** `RETENTION_DAYS=5` — per-item rolling TTL from each row's `created_at` (scraped Thursday → expires Tuesday), swept every 30m by the worker, skipping queued/running items. `0` disables. UI shows an "expires" countdown. SocialPilot queue items are preserved until posted; posted/archived items are removed `ARCHIVE_DELETE_DAYS` (4) after posting, Drive file moved to trash.
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


## Log — 2026-09-30 — SocialPilot audit & fixes

- **Mix & Shuffle did nothing visible:** it computed an order but never stored it (list sorted by `id` desc) and overwrote every row's Notes with "Shuffle sequence #N". Now writes `QueueItem.position` (new column; idempotent migration in `db._migrate()`), keeps Notes, and the list returns posting order.
- **Auto-posting never fired:** the scheduler only posted Ready items that had a `scheduled_at`, and approving never set one. New planner (`queue_manager.plan_schedule`) gives Ready items slots every 2h 8am–10pm America/New_York (from socialpilot_Ai CONFIG.md), one per pipeline per slot, in `position` order. Late slots (>30m) are re-planned, never back-filled. Config: `POST_TIMEZONE/POST_START_HOUR/POST_END_HOUR/POST_INTERVAL_HOURS`, validated at startup.
- **4-day archive cleanup:** posted/archived items are deleted `ARCHIVE_DELETE_DAYS` (default 4) after `published_at`; the Drive file is moved to trash first (recoverable 30 days). If trashing fails the row is kept and retried. Bulk "posted"/"archived" now stamps `published_at`.
- **AI button:** called `gemini-2.0-flash` (retired) and silently fell back to a canned template. Model is now `GEMINI_MODEL` (default `gemini-2.5-flash`); the ✨ AI endpoint returns the real error instead of template text.
- **Post Now "File not found in storage":** upload flow matches Outstand docs; suspected cause is non-ASCII/`#` filenames breaking the presigned key. Upload names are now ASCII-sanitized (`outstand.safe_upload_name`) and failures report media id/size/PUT status. **Unverified** — needs a Post Now retry in prod.
- **UI:** pipeline dropdowns show top-level pipelines only; sort by posting order/ID/title/channel/status/time with ↑/↓; rows show next slot (ET) and delete date; Mass Edit (title/description/tags/pipeline); Settings shows schedule, Outstand accounts, AI and cleanup status (`SettingsOverview.tsx`, `GET /api/schedule`).
- **Tests:** `backend/tests/` (pytest, mock creds): `backend/.venv/bin/python -m pytest backend/tests`.

## Log — 2026-09-30 — Outstand posting is async; YouTube quota

- Upload fix (ASCII filenames) **verified**: Outstand accepted the media and created the post.
- **Disconnect bug:** we set "posted" as soon as Outstand accepted the request, but Outstand publishes asynchronously and reports per-account `pending/published/failed` on `GET /v1/posts/{id}`. Items now stay `posting` until `queue_manager.reconcile_posting()` (every scheduler tick) sees final results: all published -> `posted` + `published_at` (4-day clock starts); any failed -> `retry` with `accounts` narrowed to only the failed ones (no double posts) and errors in notes. Before this, a failed post would also have had its Drive file trashed after 4 days.
- **YouTube 429 "Video Uploads per day":** Outstand Managed Keys route all customers' YouTube uploads through Outstand's Google project (42435067551); quota is shared (~6 uploads/day per project: 10k units, 1,600 per upload). Fix is BYOK in Outstand (own GCP project, YouTube Data API v3, redirect `https://www.outstand.so/app/api/socials/youtube/callback`), and a quota increase from Google for >6/day. The 8-slot schedule x YouTube accounts exceeds the default quota.
- Gemini default model -> `gemini-3.5-flash-lite` (2.5 is closed to new users per Google's model page, 2026-09).

## Log — 2026-10-01 — Migrated publishing Outstand -> Upload-Post

Followed qwazi12/manhwa `docs/UPLOAD_POST_MIGRATION.md`.
- **Why:** Outstand Managed Keys = one shared Google project (`defaultVideoInsertPerDayPerProject = 100` across all customers); our YouTube posts 429'd.
- **Client:** `backend/app/social/upload_post.py` (only publisher; `outstand.py` deleted, guard tests stop it returning). Key: `UPLOADPOST_API_KEY` (also `UPLOAD_POST_API_KEY` / `UPLOADPOST_KEY`); unset -> accounts endpoint says so and publishing is blocked, no fallback.
- **Account ids are `"<profile>:<network>"`** from `GET /uploadposts/users`. One upload request per profile (`queue_manager.submit_upload`); picked accounts are re-checked against what is connected before anything is sent.
- **Nothing is auto-picked:** queue items without accounts get no slot and Post Now refuses; PublishModal and Assign Accounts open with nothing ticked; confirm dialogs name exactly the picked channels + visibility (`PUBLISH_PRIVACY`, default public).
- **Results:** `publish_requests` (new JSON column on queue_items + social_posts, idempotent migration) holds request_ids; `reconcile_posting()` polls `/uploadposts/status` each tick. All accounts succeeded -> `posted` (+4-day clock); any failed -> `retry` with only the failed accounts; still processing after 3h -> failed.
- Old `outstand_post_id`/`media_url` columns left in the DB as history, no longer in the API.
- **To do (owner):** set `UPLOADPOST_API_KEY` on the Railway scrapper service; delete `OUTSTAND_*` env vars there; revoke the Outstand key in its dashboard. First live publish: one channel, check the row's notes get a post URL.

## Log — 2026-10-01 — Pick targets by Upload-Post profile

- Live Upload-Post setup (read 2026-10-01): profile `default` -> YouTube **Screen Central**; profile `mk` -> YouTube **Flamingo Remix**.
- Targets can now be a whole profile, stored as `"<profile>:*"`: it posts to every channel connected to that profile *at post time* (`upload_post.expand_targets`). Specific channels are still `"<profile>:<network>"`. Retries narrow to the specific failed channels.
- UI: shared `frontend/app/TargetPicker.tsx` (profile cards, with "pick specific channels" inside) is used by the queue's target modal and the compilation Publish window. The queue column "Posts To (Upload-Post)" shows labelled chips (`📁 default · all channels — Screen Central (Youtube)`), "✎ change", or "⚠ None picked — choose".

## Log — 2026-10-01 — Frontend was never auto-deploying

- `.github/workflows/deploy-frontend.yml` gated its deploy step on `if: env.VERCEL_TOKEN != ''` while setting that env *on the same step*, which is invisible to the step's own `if`. The step was **skipped on every run** and the run still showed success, so nothing after the 2026-09-30 sidebar work reached https://scrapper.nodepilot.dev.
- Fixed: env moved to job level, plus a step that **fails** if `VERCEL_TOKEN` / `VERCEL_ORG_ID` / `VERCEL_PROJECT_ID` are missing. If those GitHub secrets aren't set, the run now goes red instead of lying.
- Manual deploy (also the fallback): `cd frontend && vercel deploy --prod --yes` (local CLI is logged in as qwazi12, linked to `qwazi12s-projects/scrapper`).
- **Verify a deploy by checking the served bundle** for new strings, not the workflow status. Browsers also hold the old page until a hard reload.
- All 690 queue rows still target `y1aBj` (Outstand's id for Screen Central); they show "not connected" until re-pointed to the Upload-Post profile `default`.
- `npm audit` on the Vercel build: 4 vulnerabilities (3 high, 1 critical) — not yet looked at.

## Log — 2026-10-01 — Auto-poster heartbeat, Gemini 3.8 Flash, targets repointed

- `GEMINI_MODEL` default -> `gemini-3.8-flash` (newest stable Flash per ai.google.dev, Sep 2026; 3.7 Flash is the previous generation). `POST /api/social/ai-check` makes a live call and changes nothing; it's the "Test AI" button in Settings.
- Scheduler heartbeat: `queue_manager.scheduler_status` (last tick, last error, last submitted) is exposed in `/api/schedule` and shown in Settings as "Auto-poster running — last check Ns ago". A Ready item with targets gets the next slot within one tick (30s).
- All 690 rows repointed from Outstand `y1aBj` (Screen Central) to Upload-Post profile `default:*` (owner's instruction). Editable per row ("✎ change") or in bulk (🔗 Set Target Accounts).

## Log — 2026-10-01 — Times showed 4h late; missed 6pm slot

- **First live Upload-Post publish verified:** #559 -> Screen Central, https://www.youtube.com/watch?v=g4-qIktAEi4 (2026-10-01 3:39pm ET).
- **Display bug:** Postgres columns are `timestamp without time zone` holding UTC, so the API sent `2026-10-02T00:00:00` (no zone) and browsers read it as *local* time: the 8pm ET slot showed as "Fri 12:00 AM". The schedule itself was right. Fix: output schemas tag datetimes UTC (`schemas.UTCDateTime`), and the frontend `parseApiDate()` treats zone-less times as UTC.
- **Missed slot:** 688 items were set Ready at 6:01:47pm, just after the 6pm slot opened, and the planner only used slots strictly after now. Now a slot that opened < 30 min ago (`DUE_GRACE`) is given to a pipeline that has nothing in it yet (`queue_manager.current_slot`).

## Log — 2026-10-01 — Editable posting times; bulk AI rewrite

- **Posting times are editable on Settings** ("✏️ Edit posting times"): first slot, last slot, interval, timezone. Stored in the new `app_settings` table (key `schedule`), validated (`queue_manager.validate_schedule`), audit-logged as `schedule_changed` with before/after. Env `POST_*` are now only the defaults; "Reset to defaults" drops the override. The scheduler reloads the schedule every tick, and saving re-plans Ready items at once. API: `PUT /api/schedule/config` (`{reset: true}` to clear).
- **Bulk AI** (SocialPilot queue, select rows -> "✨ AI Rewrite (N)"): `POST /api/queue/bulk-ai` starts a background job (`social/ai_bulk.py`, 4 concurrent Gemini calls, 1000-item cap, one job at a time, 409 if busy). `GET /api/queue/bulk-ai` is progress; the UI polls it every 3s with a progress bar, and the job stops early if Gemini isn't configured. It overwrites title/description/tags; the single-row ✨ AI uses the same code (`ai_bulk.ai_inputs`/`apply_ai`).

## Log — 2026-10-01 — LongForm Studio: plan (build starting)

Owner decisions: a "LongForm Studio" tab in Scrapper; **trailer breakdowns only** (2–4 min, 16:9); TMDB allowed (hobby, non-commercial; credit TMDB in descriptions, and revisit if the channel is ever monetized); Chirp voice like manhwa; human approval before posting; footage not from YouTube where possible (TMDB/IMDb/other, Mac worker as the last resort).
Pipeline (each stage saves its own output to the DB + `data/studio/<id>/`, so it can be resumed and re-run):
1. **Calendar/search** — TMDB upcoming / on-the-air / trending + search.
2. **Gather** — TMDB facts (release dates by country, cast→character, crew, synopsis, videos, images) + web research through Gemini with Google Search grounding (the guide's sites, every source URL kept) → a fact sheet with sources.
3. **Trailer** — pick the official trailer, download it (non-YouTube first, then the Mac worker, or a manual upload).
4. **Shots** — ffmpeg scene cuts → keyframes; Gemini vision tags each shot (description, who is in it from the cast list, setting, mood, text-card/logo → excluded).
5. **Script** — 2–4 min, the guide's structure (name in paragraph 1, date in paragraph 2, subscribe CTA), written only from the storyboard; a second pass checks every claim.
6. **Plan** — assign a shot (still with Ken Burns, or a 3–5 s clip) to every sentence; Chirp TTS per sentence (cached) gives the real timings.
7. **Render** — ffmpeg 1080p: intro poster on blur, letterboxed rotation of stills and clips, subscribe lower-third, end card.
8. **Publish** — send to the Posting Queue (pipeline "LongForm") → normal approval + Upload-Post.
Env needed on Railway: `TMDB_API_KEY`, `TTS_API_KEY` (same Google key manhwa uses), plus the existing `GEMINI_API_KEY`.
- **Checkpoint 1 (built):** `backend/app/studio/` — `tmdb.py` (v3 key or v4 token; search, calendar, `fact_sheet` with releases/cast/crew/videos/images + attribution), `gemini.py` (JSON, images, Google-Search-grounded research with source URLs), `runner.py` (stage registry, one background job at a time, every start/done/error logged as `studio_*` and stored on the row), `stage_gather.py` (facts + poster/backdrops/cast photos to `data/studio/<id>/assets` + web research), `routes.py` (`/api/studio/status|calendar|search|projects…|run|trailer-upload|file`). New table `studio_projects` (one JSON column per stage). Tests: `backend/tests/test_studio.py`.
- **Checkpoint 2 (built):** footage + shots. `studio/imdb.py` = IMDb's public GraphQL (`caching.graphql.imdb.com`, header `x-imdb-client-name: imdb-web-next`) → official trailers/clips/promos with direct **1080p MP4** URLs (signed, fetch right before download). Verified from home: Send Help trailer `vi2524760089` = 1920×1080, 141 s, 90 MB, and 75 shots (median 1.5 s). IMDb's HTML pages return an empty 202 (bot wall), so use only GraphQL. **Not yet verified from Railway's IP.** `stage_trailer.py`: upload > IMDb (main trailer + extras ≤ 300 s, ≤ 6 files) > YouTube via the home worker (creates a failed `Clip` with no file, which the worker polls; run the stage again once it's fetched). `stage_shots.py`: ffmpeg scene cuts → still + thumb per shot → Gemini tags in batches of 12 against a labelled TMDB cast sheet (who, setting, mood, size, card/text flags); unknown names are dropped; cards/text/blurry → `usable=false`. Docker image gains `fonts-dejavu-core`; Pillow pinned.
- **Checkpoints 3–5 (built):** `stage_script.py` (storyboard ids F#/R#/T#; draft with the guide's prompt rules; a second Gemini pass marks unsupported sentences and fixes/drops them, kept in `script.changes`; YouTube title/description with sources + TMDB credit/tags). `tts.py` (Chirp REST with `TTS_API_KEY`, voice `TTS_VOICE` default `en-US-Chirp3-HD-Charon`, cache `data/studio/_ttscache` keyed by sha1(voice|spoken text)). `stage_plan.py` (voice each sentence, slot = real length + 0.35 s / 0.6 s at paragraphs, a visual every ~4.2 s; slot 1 = title card or poster; release-date line = poster card; Gemini picks shots, validated/repaired, trailer-order fallback). `stage_render.py` (1080p30, 2.4:1 letterbox, Ken Burns stills in 4 alternating moves, clips with the source's own bars cropped, poster-on-blur + red release banner, subscribe lower-third, 8 s end card, narration placed by start time + loudnorm -14 LUFS, 1280×720 thumbnail).
- **Render gotchas (verified 2026-10-01 on the real Send Help trailer):** IMDb trailers have letterbox bars baked in (`1920:804:0:138`), so `media.active_area()` crops them before frames/clips, otherwise you get double bars. `setpts=PTS-STARTPTS` before the overlay cut a 33 s render to 13.1 s, so don't use it; loop the PNG with `-loop 1` + `overlay=shortest=1`. All segments are encoded with `-video_track_timescale 30000` for the concat-copy join. Local end-to-end (real trailer, `say` voice, stub tags): 71 shots in 14 s, plan 5 s, render 7 s for 33 s of video.
- **Checkpoint 6a (built):** `POST /api/studio/projects/{id}/publish` puts the render into the Posting Queue (pipeline `LongForm`, status review, no accounts: the owner picks the target and approves; re-publishing a re-render updates the same row while it hasn't posted). Upload-Post uploads now include `thumbnail` when the queue item's `thumb_path` is a local file ≤ 2 MB (YouTube applies it only on verified channels).
- **Checkpoint 6b (built):** frontend `StudioPanel.tsx`, sidebar tab **LongForm Studio** (`#longform`). Pick a title (TMDB search, or calendar: upcoming / trending / airing TV) → "Make breakdown" creates the project and auto-runs research → footage → shots → script. The project page has a stage stepper (re-run any step), facts + web findings with source links, footage list + trailer upload + shot grid (unusable shots dimmed), an editable script (one sentence per line, blank line = new paragraph; fact-check changes listed) + title/description/tags, the shot plan (click to swap a shot, still/clip/poster), and the video player + thumbnail + "Send to Posting Queue". Editing the script's words clears the stale plan/render. Queue pipeline list gains "LongForm".
- **Verified on Railway 2026-10-01:** `GET /api/studio/imdb-videos?imdb_id=tt8036976` → 2 trailers + 2 clips, with a direct MP4 (`first_has_mp4: true`), so IMDb footage works from the datacenter IP and no YouTube is needed. Studio status: Gemini ✓, **TMDB ✗, TTS ✗ (owner to add `TMDB_API_KEY` and `TTS_API_KEY` on the Railway scrapper service)**.
- **Pending / next (Studio):** (1) once the keys are in, run Send Help end to end on Railway and judge it against the original: script accuracy (fact-check changes), shot matching (who is on screen), render quality; (2) tune prompts and the visual rate from that result; (3) not built yet: background music bed (no licensed track), automatic daily topic pick (the calendar exists, the pick is manual), Shorts teaser cut-down; (4) the Compilations & Exports tab is still blank (ExportPanel not mounted), so ask before fixing.
- **TMDB auth (2026-10-01):** Railway has `API_Read_Access_Token` (v4 token, sent as `Authorization: Bearer`, TMDB's default per developer.themoviedb.org/docs/authentication-application) and `TMDB_API_KEY` (v3, `api_key` param). The client tries the token first and retries with the key only on a 401 (logs a warning).
- **First real Railway run, Send Help (Studio #1, 2026-10-01):** gather → 12 cast, 102 TMDB videos, 31 web sources; trailer → IMDb, 2 trailers + 2 clips; shots → 134, 108 usable, 12 cards excluded, leads tagged in 48 and 46 shots, with character names in the descriptions; script → 615 words, facts correct (plane crash, island, 5 release dates). **Problems found:** (1) paragraphs 3–5 narrated the trailer shot by shot; (2) source links were Google grounding redirects; (3) Reddit/fan sites among the sources. **Fixes:** `studio/sources.py` resolves redirects and tiers domains (official/trusted/low/other); only findings backed by official/trusted sources reach the storyboard and only those sources go in the description; the research prompt bans forums/social; the draft prompt tells the trailer as 4–6 story beats with what each beat suggests, never shot by shot.
- **Send Help re-run on Railway (after the story-beat/source fixes):** 24 sources (7 official/trusted), 12 weak-only findings dropped; script tells 5 story beats; description cites Wikipedia/IGN/Screen Rant/TheWrap/JoBlo/Disney+/TMDB with real URLs; plan 31 sentences voiced (226 s), 71 visuals, 70 distinct shots, Gemini picks 0 repaired; render 3:54, 1080p, 62 MB. Frames checked: shots follow the story. Studio #1 is NOT sent to the queue.

## Log — 2026-10-02 — Stop for every process (built, committed, push pending frontend)
- `backend/app/control.py`: job registry modelled on manhwa's job control (cooperative stop between steps; queued jobs never start; a sweep retires jobs that ignore Stop for 120 s) + improvement: Stop KILLS the running ffmpeg/yt-dlp child at once (`control.run` / `control.track`), since renders and downloads are rebuildable. A killed process always raises `Cancelled`, never returns a half result.
- Covered: scrape jobs (per link + yt-dlp; a stopped link becomes `skipped`, never "blocked", so the home worker won't fetch it), compilations (ffmpeg killed, partial output deleted), Studio stages (status `stopped`; checks between shots/sentences/segments/Gemini calls/download chunks; IMDb downloads write `.part` then rename), bulk AI, Drive Sync + Channel Ingest (now background jobs → `{job_id}`; Channel Ingest commits a queue row per uploaded file). Auto-posting: `PUT /api/autopost {paused}` (an upload already handed to Upload-Post can't be recalled). API: `GET /api/jobs`, `GET /api/jobs/{id}`, `POST /api/jobs/{id}/stop` (also `ingest:N` / `compile:N` for queued ones).

## Log — 2026-10-02 — Undo (built, committed, push pending frontend)
- `backend/app/undo.py` + table `undo_entries`: manhwa's model (snapshot rows BEFORE a change, restore as-is; never "inverse" ops). The snapshot joins the change's own DB transaction. Per-scope stacks (`queue`, `studio:<id>`, `settings`), 50 steps each, every step labelled ("Mass edit on 12 videos").
- Covered: queue edit/approve/AI (single + bulk)/delete/every bulk action/shuffle/add/Studio publish/Drive sync + channel ingest (removes the rows they added; Drive files stay); Studio script/plan/length edits and script/plan re-runs (restores script, plan, render pointer, length); Settings schedule + auto-post switch.
- Not undoable: deleted scraped clips (files gone), overwritten renders/shots, anything already posted, files uploaded to Drive.
- API: `GET /api/undo?scope=` (stack), `POST /api/undo {scope}`; 409 while a bulk AI run or a Studio stage on that video is running.

## Log — 2026-10-02 — SocialPilot AI researches clips on TMDB (built, committed, push pending frontend)
- `social/clip_research.py`: Gemini identifies the movie/show from the clip's title/filename/hashtags/channel (confidence 0–1) → TMDB search + details (genres, cast→character, keywords, US streaming providers) → SEO title/caption/hashtags written from those facts. Below 0.6 confidence it names no title (a wrong title hurts more than none). The match is stored on `queue_items.research` (new column) and reused; a bulk run shares TMDB lookups across clips. Compilations keep the old clip-titles prompt.
- **Frontend for Stop/Undo/research (2026-10-02):** `JobsBar.tsx` at the top of every tab (polls `/api/jobs` every 3 s; running/queued jobs with ■ Stop + confirm; just-stopped/failed shown for 60 s). `UndoButton.tsx` names the step ("↶ Undo: …"): in the Posting Queue header, each Studio project, and the Settings schedule card. Pause/resume auto-posting in the queue header and in Settings. Studio project page has ■ Stop while a stage runs and shows "stopped". The bulk AI banner has ■ Stop. Drive Sync / Channel Ingest panels start a job, show live progress, and have ■ Stop. Queue rows show the TMDB match ("🎬 Superman & Lois (2021) · TMDB") or "no confident TMDB match".
- **Verified live 2026-10-02:** TMDB AI on queue item #385 matched **Breaking Bad (2008)** at confidence 1.0 (title gained the show name; caption named Bryan Cranston + Netflix; hashtags from TMDB keywords), then ↶ Undo restored it exactly (title, tags, research=None, still ready for Dec 26). Stop: a throwaway Studio project's trailer download was stopped and showed "stopped by user" within the same second, and the auto-chain did not continue; the test project (#2) was deleted.

## Log — 2026-10-02 — Cost tracker (built, committed, push pending frontend)
- `backend/app/costs.py` + table `usage_events`. Prices from the official pages (read 2026-10-02), editable in Settings (`app_settings.costs`): Gemini 3.8/3.7 Flash $0.75 in / $3.75 out per 1M tokens (thinking billed as output) until 2026-12-31, then $1.50 / $7.50; 3.5 Flash-Lite $0.30 / $2.50; Search grounding 5,000/month free, then $14 per 1,000; Chirp 3 HD TTS 1M chars/month free, then $30 per 1M (cache hits cost $0); Upload-Post flat plan fee (Free $0 = **10 uploads/month**, Basic $24, Professional $50, Advanced $147, Business $438; paid plans unlimited); TMDB/IMDb free (counted).
- Every paid call is recorded with an operation label (`studio:<stage>` with ref `studio:<id>`, `queue:ai`, `queue:bulk_ai`, `post`) via `costs.operation(...)`. A monthly budget plus an optional hard stop (`check_budget` before Gemini/TTS calls → refuse with a reason) stops bulk AI once it's reached; posting is never blocked (flat fee). API: `GET /api/costs?month=`, `PUT /api/costs/settings` (undoable). Studio projects report `cost_usd`.
- **Drive finding (2026-10-02):** the service account `omnistream-bot@manhwa-engine.iam.gserviceaccount.com` CANNOT upload to My Drive folders ("Service Accounts do not have storage quota") — verified with a test upload. This means **Channel Scraper & Ingest's Drive upload has never worked**, and LongForm → Drive needs a **Shared Drive** (SA as Content manager) or OAuth/domain-wide delegation. The SA CAN create folders in "YouTube" (`1_VLhKfSEYZFyPBXi7kYu4uw94JUronDP`, parent of Movie Clips).

## Log — 2026-10-02 — SEO for every video, LongForm section, Drive storage, Spending UI
- **Auto-SEO (clips):** `queue_manager.publish_queue_item` runs the TMDB research + rewrite right before upload when a clip never had AI (`research is None`) and still has its raw import text (`description == title` or empty). Owner-edited text is never touched; each rewrite records an undo step ("Auto-SEO of #N before posting"); any failure posts with the existing text and logs `auto_seo_skipped`. Switch: `app_settings.seo {"auto": true}` (default on), `GET/PUT /api/seo`, checkbox in Settings → AI Captions. Cost label `post:auto_seo`.
- **LongForm SEO:** script tags = title / "trailer" / "trailer breakdown" / "release date" / title+year + top 3 cast + director + model tags + TMDB keywords (fact sheet now appends `keywords`), deduped, max 20.
- **SocialPilot → 🎬 LongForm Breakdowns:** its own sub-tab (`QueuePanel mode="longform"`); the main Posting Queue and "all" shuffle exclude pipeline LongForm.
- **Drive storage:** `studio/drive_store.py` uploads final.mp4 + thumbnail (8 MB chunks, Stop-able) to folder `LONGFORM_DRIVE_FOLDER_ID`, else "LongForm Studio" under YouTube folder; links on `studio_projects.drive` (new column) + the queue item's `drive_link`; a re-save trashes the previous copy. Starts automatically on "Send to Posting Queue"; `POST /api/studio/projects/{id}/drive` to retry. Drive Sync skips a "LongForm Studio" subfolder; the archive sweep never trashes LongForm Drive files. **Blocked until the owner sets up a Shared Drive** (service account = Content manager) and sets `LONGFORM_DRIVE_FOLDER_ID` — the UI shows that exact instruction when Drive refuses.
- **Spending card** (Settings, top): month total, pay-as-you-go vs fixed, budget meter, hard-stop switch, Upload-Post plan picker, budget/fixed-cost editor, free-tier meters (TTS chars, search requests, uploads), by-service and by-purpose tables, prices. Studio project list + video section show the project's cost.
- Tests: 112 pass (Drive tests mock Drive entirely).

## Log — 2026-10-02 — Compilations tab + phone queue header
- Compilations & Exports tab was blank because `ExportPanel` was never rendered for `activeTab === "comps"`; now mounted (verified live: shows "Export — 0 compilation(s)").
- Posting Queue header on phones (<760 px): short labels (📋 Posting Queue, ⏸ Pause posting, ↑/↓, 🔀 Mix ▾, + Add), tighter padding, wraps cleanly; the shuffle menu opens rightwards and fits (verified at 375 px: menu 60–330 px, no horizontal scroll).
- **Pending (owner):** GitHub repo secrets for auto-deploy — `VERCEL_TOKEN` (create at vercel.com/account/tokens), `VERCEL_ORG_ID` = `team_iwJkBp7HPLUnvJQoEl7gJadj`, `VERCEL_PROJECT_ID` = `prj_MvlUvP2cS5vtNBay3V2ISsSCgnJE`. Until then deploy with `cd frontend && vercel deploy --prod --yes`. Shared Drive + `LONGFORM_DRIVE_FOLDER_ID` also pending (owner will notify).

## Log — 2026-10-02 — Upcoming calendar, 16:9 render, HyperFrames motion layer, handbook
- **Upcoming movies** (`tmdb.upcoming_movies`): `/movie/upcoming` listed theatrical RE-releases (1959/1980 films). Now `/discover/movie`, region US, release types 2|3|4, regional `release_date` today→+92 days AND `primary_release_date` ≥ 6 months ago, queried per calendar month (2 pages each) so October can't crowd out December; festival-premiere dates replaced by the regional opening. Cached 3 h. UI groups by month with month filter buttons. Verified live: films 2026-10-02 → 2026-12-25, no old titles.
- **Render framing**: the old renderer forced every shot into a 1920×800 (2.4:1) band → 16:9 sources lost ~26% top/bottom. Now full-frame 16:9 FIT (scale-to-fit + pad, never crop): 16:9 sources fill the frame; scope sources (Send Help's IMDb trailer is 2.39:1) keep their own bars. Owner may still want "fill" (crop scope sides) — asked, not built.
- **Motion layer built (plan doc "Plan — HyperFrames Motion Layer")**: `studio/motion.py` + `motion_templates/*.json` (intro over first trailer shot, release card, cast cards, subscribe, end screen; Screen Central red/white). Pinned `backend/hyperframes` (hyperframes 0.8.35, gsap 3.14.2, lockfile); Dockerfile: Node 22 + Chrome libs + **unzip** (without it `browser ensure` hangs at 100% — a 30-min hung build) + baked Chrome wrapped `--no-sandbox`, `timeout 900`. Overlays = ProRes 4444 alpha, cached in `/data/studio/_motioncache` by TEMPLATE_VERSION(2)+page+assets; composited with `-itsoffset` + `overlay eof_action=pass`. Per-piece fallback to static. Cast cards: top-billed, default 2 (max 4), only on shots where that actor is the ONLY person tagged, never overlapping other overlays. Subscribe windows move off intro/release windows (`_free_window`).
- Setting `app_settings.studio_motion {mode: compare|on|off, cast_cards}`; default **compare** (final.mp4 = static, final_motion.mp4 = motion; owner picks in the Studio). API `GET/PUT /api/studio/motion`.
- **Bug found + fixed**: JPEG-made stills (poster, end card) came out yuvj420p/pc/bt470bg; the mid-stream format change made ffmpeg reinit filters and DROP the release overlay (Send Help, 29.1 s). Every segment now normalised (`NORM` + colour tags in ENC); regression test asserts every frame is yuv420p/tv. Motion poster now smaller/higher so the card doesn't cover the poster title; bell is an SVG (no emoji font on server).
- **Verified on Railway**: Send Help re-render 234 s, 8 motion pieces, 0 fallbacks, ~4.5 min; frames checked (intro, release card, McAdams + O'Brien cards on solo shots, subscribe, end screen).
- **Drive**: `LONGFORM_DRIVE_FOLDER_ID` is set, but the folder "LongForm Studio" is in a kymediamgmt.com user's **My Drive** (driveId None) → upload refused (no SA quota). Needs to be moved into a **Shared Drive** (SA = Content manager). Studio now states this explicitly. Not working yet.
- **Handbook** published: https://claude.ai/artifact/SF2MvUyAwnXo6rcEnXikwP (replaces the SOP doc for day-to-day use).
- Tests: 119 pass (+1 opt-in live HyperFrames test `MOTION_LIVE=<out.mp4>`).

## Log — 2026-10-02 (pm) — Stop button, render safety, accurate calendar, project review
- **Stop did nothing (owner report, Studio #2 shots)**: no "Stop pressed" ever reached the server; server-side stop worked (API stop halted it in <2 s). Cause: every Stop used `window.confirm()`, which browsers can silently block (Chrome "prevent additional dialogs", in-app browsers) → click = no-op. New `StopButton.tsx`: two-tap in-page confirm ("■ Stop" → "■ Tap again to stop", 4 s), used by JobsBar, Studio, Drive Sync, Channel Ingest, bulk AI. Verified in the live UI: Studio render stopped via two taps.
- **Duplicate route** `/api/jobs/{job_id}`: the old int-only ingest route shadowed the registry one → every background-job lookup 422'd (broke `waitJob` progress for Drive Sync/Channel Ingest/Drive save). Merged: digits = ingest job, else registry job.
- **Stopped/failed re-render deleted the last good video** (render rmtree'd `render/` first) — happened to Send Help during the stop test; restored by re-render. Now builds in `render_new/` and swaps at the end; test proves a stopped re-render keeps the old file.
- **Restart/deploy mid-step** left projects "running" forever (jobs live in memory): `runner.recover_interrupted()` at startup marks them stopped "interrupted by a server restart".
- JobsBar re-polls on `visibilitychange` (it skips polling while the tab is hidden).
- **Calendar accuracy**: audit of all 121 listed films — every date matched a real US release (types 2/3/4). Now each film's own `/movie/{id}/release_dates` sets the date + kind (`theaters` / `limited` / `digital`) + `in_theaters_since` (window date is only its digital/wide release); 8 parallel lookups, cached 3 h; UI labels + "In theaters / Digital" filter. Live: 52 theaters, 34 limited, 35 digital, Oct→Jan.
- **Correction**: production DB is **SQLite on the Railway volume** (`/data/scrapper.db`, no DATABASE_URL), not Postgres. No backups.
- **Review findings (not yet fixed, owner to prioritise)**: 61 known vulns in starlette 0.41.3 (via fastapi 0.115.6), python-multipart 0.0.20, pillow 11.3.0; volume 898 MB/4.6 GB used, Studio ≈ 230–430 MB per project with no retention (≈ 8–10 more breakdowns fill it); no DB backups; in-memory job registry (restart kills running work); single shared access token, CORS/rate-limit none; `/api/tts/audio/*` unauthenticated (from the TTS commit); GitHub deploy workflow fails every push (no VERCEL secrets); Railway config-as-code deprecated 2026-12-01; no RUNBOOK.md/CONFIG.md/CHANGELOG; no frontend tests; 9 silent `except: pass`; Drive needs a Shared Drive (Channel Ingest upload broken too); TTS commit 673e7f3 (other session) not reviewed in depth.
- Tests: 122 pass.

## Log — 2026-10-02 (eve) — Fix plan written for the 7 review findings (awaiting owner review; nothing built)
- Plan doc: "Scrapper Fix Plan — Review Findings" https://claude.ai/code/artifact/6c6d08ea-3c67-4154-bcfb-62a27bdda4cd
- Order 1→7, ~3–4 build days:
  1. **DB backups** (~3 h): nightly 03:00 ET SQLite online backup + integrity_check + gzip; keep 7 on disk (`/data/backups`), 30 in Shared Drive "Scrapper Backups"; "Backup now" + auto-snapshot before migrations/upgrades; `scripts/restore_db.py` (dry-run default, `--confirm`, saves current file aside); Settings status; warn if newest > 26 h. Postgres migration considered, not needed.
  2. **Disk** (~4 h): measured Send Help = 233 MB footage + 168 MB render (~50 MB leftovers: subscribe_*.mov, narration, banners) + 34 MB stills; motion cache 226 MB. Delete leftovers after render; archive (delete footage/stills/render, keep metadata+thumb+Drive link) 14 days after posted AND Drive copy confirmed; caps motion 400 MB / TTS 200 MB (LRU); startup cleanup of render_new/; Settings disk meter, warn 75%, refuse new breakdowns/ingest at 90%. Archiving waits for fix 5.
  3. **Deps** (~3 h): pillow 11.3.0→12.3.0 (35 vulns), starlette 0.41.3→1.7.0 via fastapi 0.115.6→0.142.2 (14), python-multipart 0.0.20→0.0.32 (12), uvicorn 0.34→0.54; replace `@app.on_event` with lifespan; check 6 upload endpoints + Pillow text calls; hashed lock file; pip-audit/npm audit in CI. Rollback = Railway redeploy previous.
  4. **Restarts** (~1 day): jobs table in DB (kind, args, status, progress, checkpoint); auto-resume on startup ≤2 tries per job type; "resumed after restart" shown; Railway drain ~2 min + `scripts/safe_deploy.sh`; setting `resume_after_restart`.
  5. **Drive** (~2 h after owner move): owner creates Shared Drive, SA Content manager, moves "LongForm Studio" + "Movie Clips" (IDs unchanged); add supportsAllDrives/includeItemsFromAllDrives to the 9 drive_sync.py calls lacking it; Settings pre-flight per folder.
  6. **Access** (~4 h): auth currently FAILS OPEN when ACCESS_TOKEN is empty (`auth.py` "open in local dev") → refuse to start unless DEV_OPEN=1; master token travels in `?token=` URLs (studioFileUrl etc.) → short-lived signed links; `/api/tts/audio/*` unauthenticated → signed; constant-time compare; rate limits (20 bad tokens/10 min per IP → 15 min block; 30 paid actions/min); rotate token with 2-token overlap; optional named tokens. CORS already restricted (scrapper.nodepilot.dev + vercel alias).
  7. **Upkeep** (~1 day): GitHub Vercel secrets (owner), `railway config migrate` before 2026-12-01, RUNBOOK/CONFIG/CHANGELOG, Playwright smoke tests vs local server, log the 9 silent excepts, ruff/pip-audit in CI, review TTS commit 673e7f3 (tests, 7-day /data/audio retention, auth), restructure MEMORY.md.
- **Decisions pending from owner** (checklist at end of the doc): backup destination, archive days, volume size, Shared Drive move, rate-limit numbers, named tokens, token rotation, auto-deploy secrets vs drop workflow, order.

## Log — 2026-10-02 (night) — Fix 1: Database Backups Built & Verified Live
- `backend/app/backup.py`: Full SQLite point-in-time backup engine using `sqlite3.Connection.backup` (lock-safe during writes) + `PRAGMA integrity_check` + gzip compression (.db.gz) + SHA-256 validation.
- **Local & Off-Disk Rotation**: 7-day retention on local Railway disk (`/data/backups/`), 30-day retention in Google Shared Drive folder `14z17C-cYIqUK8teqeOOVtnITx0GHHl8E` ("Scrapper Backups"). If Shared Drive permissions are pending, gracefully defaults to local disk with yellow warning in UI.
- **Pre-Migration Safety Snapshot**: `init_db()` in `backend/app/db.py` automatically captures a `pre_migration` snapshot before running any additive migrations.
- **Nightly 3:00 AM Eastern Scheduler**: Automatically triggered inside `run_scheduler_tick()` in `backend/app/social/queue_manager.py` with 26-hour staleness alerting.
- **Restore CLI (`scripts/restore_db.py`)**: Dry-run by default comparing all table row counts; `--confirm` captures emergency pre-restore snapshot (`scrapper_pre_restore_<ts>.db.gz`) before restoring.
- **Settings UI (`BackupCard` in `SettingsOverview.tsx`)**: Real-time backup status, metrics, 1-click **"💾 Backup Now"** button, and direct `.db.gz` file downloads.
- **Verified live in production**:
  - Live pre-migration snapshot taken on Railway restart: `scrapper_20261002_183619_pre_migration.db.gz` (1.51 MB -> 356 KB in 0.05s).
  - Manual backup endpoint `POST /api/backup/now` tested live and returned 200 OK.
  - Live restore dry-run verified against live database snapshot (2,548 total rows: 689 queue items, 31 ingest jobs, 1,387 logs, 435 events).
  - Unit tests in `backend/tests/test_backup.py` passed 4/4 in 0.016s.

## Log — 2026-10-02 (night) — Pacing Throttle & Scheduler Built & Verified
- **Pacing Throttle Suite**:
  - Global 1-click presets: `[ 1 / day ]` (Daily Highlight), `[ 3 / day ]`, `[ 4 / day ]`, `[ 8 / day (Standard) ]`, `[ 12 / day ]`, `[ 20 / day (Blitz) ]`, plus custom inputs (1 to 48 posts/day).
  - Mathematical slot distribution across active hours (`round(i * window / (N - 1))`) eliminating minute drift and ensuring exact first/last slot alignment.
  - Per-Pipeline overrides: configure distinct velocity for `Movie Clips`, `LongForm`, `Abyss Declassified`, `The ICK Room`, `default`, or individual creator channel handles.
  - Per-Account overrides: configure velocity for Upload-Post profiles (`default` -> Screen Central, `mk` -> Flamingo Remix).
  - Resolution precedence: Account override > Pipeline override > Global default.
- **UI Integration in Settings & SocialPilot AI**:
  - `PacingThrottle.tsx`: interactive speed gauge, content runway meter (e.g. 688 items = 34.4 days at 20/day), 1-click override buttons, pause/resume, and undo integration.
  - Embedded in **Settings & Channels** tab (`SettingsOverview.tsx`) inside the Posting Schedule card.
  - Dedicated sub-tab in **SocialPilot AI Studio** (`page.tsx`): `⏱️ Pacing & Scheduler` alongside Posting Queue, LongForm Breakdowns, Channel Ingest, and Drive Sync.
- **Verification**:
  - 60/60 unit tests passed in `backend/tests/test_queue.py`.
  - TypeScript compilation `npx tsc --noEmit -p frontend/tsconfig.json` passed with 0 errors.

## Log — 2026-10-03 — Review of the fix-plan build, owner requests, 3× faster render
- **Review of commits 36b26be / 0bb98b4 / f52b766 (built in another session).** Kept: backups (backup.py, restore CLI, BackupCard), cleanup.py, worker recovery, auth fail-closed + signed-URL support, RUNBOOK/CONFIG. Fixed:
  - requirements had become `>=` floors (non-reproducible; floors allowed the vulnerable Pillow 11.3 / python-multipart 0.0.20) → exact pins: fastapi 0.142.2, starlette 1.7.0, uvicorn 0.54.0, python-multipart 0.0.32, Pillow 12.3.0, sqlalchemy 2.1.3, pydantic 2.13.5 … (yt-dlp keeps a floor on purpose). pip-audit clean (pytest dev-only).
  - rate limiter keyed on `request.client.host` (= Railway proxy → everyone shares one bucket) at 120/min (a 55-edit test hit it) → first X-Forwarded-For hop, 600/min, + 20 wrong tokens/10 min → 15-min lockout per IP (test).
  - `cleanup_leftover_renders` looked in `studio/projects/` (projects are `studio/<id>/`) → never cleaned; test encoded the wrong path too.
  - disk guard read the host disk in tests (laptop 94.7% full → every render test failed) → conftest sets thresholds to 101/100.
  - **Still open from the plan:** website never uses signed links (token still in `?token=` URLs); signature not bound to the `path=` query of the studio file route; no jobs table / auto-resume for Studio/Drive/bulk AI (only scrape+compile recover); 14-day archive rule not built; no paid-action cap; Playwright/CI not added.
- **X downloads**: 4/6 worked; the 2 failures were the same post from a **suspended** X account. `classify_error` now maps X "Suspended / protected / no video / unavailable" to permanent readable messages; `/api/clips/blocked` (Mac worker queue) only returns links marked "Transferred to Mac worker" (it used to hand every failed link to the Mac).
- **Sticky header on phones**: `<main>` was a scroll box on mobile while the page itself scrolled, so the sticky header never stuck → main `overflowY: visible` on mobile.
- **Studio**: "Your videos" above "Start a new breakdown". Shot lightbox (tap a shot: big still, ▶ play the actual clip via `footage/<file>#t=start,end`, prev/next, tags) with **Use this shot / Leave this shot out** override for any shot incl. "unusable" (`PUT /api/studio/projects/{id}/shots/{sid}`, undoable, keeps `auto_usable`, `owner_set`). Swap picker shows all shots (usable first) with "used at m:ss".
- **Plan QA** (`stage_plan`): dHash per still; same look = ≤10 bits, or same set-up (same video, same people, ≤12 s apart, ≤24 bits). Gemini sees one shot per look; `_validate` refuses a look used in the last 6 slots or used at all while unused looks remain, replacing with a same-people shot. `look` saved on shots; plan rows show ⚠ flags + header "QA: n repeated pictures". By Any Means (current plan, made before QA): 151 shots = 117 looks; 10 repeats incl. the reported 0:44/0:48 pair (s135/s141, 20 bits apart) → re-run step 5 to fix.
- **Render speed** (manhwa lessons: per-segment cache + parallel workers; Railway container = 32 vCPU / 32 GB): segments render 8 at a time (`RENDER_WORKERS`), cached in `studio/<id>/segcache` by content (`SEG_VERSION`), motion pieces pre-rendered 4 at a time, static+motion composites concurrently. Send Help on Railway: **265 s → 83 s** cold, **52 s** re-render (73/73 segments reused). Output verified (1920×1080, 3:54, release card 29.5–32 s, intro, cast card).
- Scheduler 30 s tick: owner asked about 1 h 45 m — answered (see conversation); not changed pending decision.
- Tests: 146 pass.

## Log — 2026-10-03 — No tokens in URLs, auto-resume, 14-day archive, 5-min poster
- **No access token in any URL.** `GET /api/media-pass` (token in header) → 12 h signed pass (HMAC of a key *derived* from ACCESS_TOKEN). `?g=<pass>` accepted only for GETs on the read-only media routes (`clips/{id}/thumb|download`, `compilations/{id}/download`, `events`, `studio/projects/{id}/file`, `tts/audio/*`, `backup/download/*`); `?token=` is refused everywhere; the token travels only in headers. TTS audio route now protected (was public). Frontend: `refreshMediaPass()` at start + every 6 h + on token save; `mediaUrl()` for every media link; page renders after the pass arrives. Verified live: `?token=` → 401; pass on `/api/jobs` → 401; 71 Studio images + video (206 range) load via pass; no request carries the token.
- **Auto-poster every 5 min** (`TICK_SECONDS=300`); middleware wakes it on any non-GET to `/api/queue*`, `/api/schedule*`, `/api/autopost*`, so approvals still get a slot at once. Stale-backup warning made once-a-day (it depended on a 30 s tick).
- **Auto-resume** (`backend/app/resume.py`, table `resumable_jobs`: kind, params, checkpoint, status, attempts, owner, heartbeat=updated_at): Studio steps (checkpoint = current step; auto-chain continues), Drive sync, Channel Ingest, bulk AI (checkpoint = finished ids; resume skips them), Drive save, footage restore. Deploys overlap on Railway (new server up while the old still runs), so rows carry `owner` (process id) + 20 s heartbeat; a server only resumes rows whose owner has been silent 75 s, watching for 10 min after start; max 2 resumes. `recover_interrupted` now skips projects with a live job row. Channel Ingest now skips videos already in the queue (yt-dlp `match_filter` on the id in `video_name`) — it used to re-download/upload duplicates. **Verified live**: redeploy mid-render of Send Help → `job_resumed … attempt 1 of 2` → finished.
- **14-day archive** (`studio/archive.py`, column `studio_projects.archive`, setting `app_settings.studio_archive {enabled, days}` default on/14): eligible when posted ≥ N days ago AND Drive copy saved AND it's of the current render AND nothing running. Deletes footage (unless uploaded), stills, render, render_new, segcache; keeps script/plan/shots metadata, thumbs, tts, assets, `thumbnail.jpg`. Render refuses until restored. Restore footage = re-download same IMDb ids + rebuild stills (no AI). Posted date saved on the project before the 4-day queue sweep deletes the row (`note_posted`, only for the project that points at that LongForm item). Daily sweep from the scheduler; preview/switch/days/run-now in LongForm Studio. Live: both projects "not posted yet".
- Docs: CONFIG.md + RUNBOOK.md sections added; Handbook v2 republished (5-min poster, archive/restore, auto-resume).
- Tests: 158 pass (media pass, wake, resume incl. deploy-overlap, archive incl. restore).

## Log — 2026-10-03 (am) — UI alignment, pop-ups, menu order, Drive cause
- Caught up on another session's commits (c126706 Pacing Throttle + "⏱️ Pacing & Scheduler" tab, cf99af7 Stop fixes, 7d4a067 3 thumbnails + "unblock Drive"). 166 tests pass after fixes.
- **Misaligned Settings checkbox**: global `input { width:100% }` also hit checkboxes → label squeezed into a thin column. `input[type=checkbox|radio] { width:auto }` in globals.css.
- **SocialPilot tab strip** ran off the right edge on phones and widened the page → wraps. Verified at 375 px: no tab wider than the screen.
- **Browser pop-ups replaced everywhere**: ~20 `confirm()` and ~40 `alert()` calls → `askConfirm()` / `notify()` (`lib/dialogs.ts` + `DialogHost.tsx`, mounted in page.tsx). Blocked native dialogs (owner's phone browser) made those buttons silently do nothing — the same cause as the Stop bug. Verified live: Pause posting → in-page box → Cancel → unchanged.
- **Menu**: LongForm Studio now above Compilations & Exports.
- **Drive "no storage" error**: LONGFORM_DRIVE_FOLDER_ID is found fine; the folder is in a kymediamgmt.com **My Drive** and the robot is a member of **0 Shared Drives** (checked live). Commit 7d4a067 removed the My-Drive pre-check ("unblock Drive") and changed the test to claim My-Drive uploads succeed — Google refuses them, so it only turned a clear message into the quota error. Check restored with an exact message; test now asserts no upload is attempted. **Owner action still needed**: create a Shared Drive, add the robot as Content manager, move "LongForm Studio" into it. (Same applies to the "Scrapper Backups" Drive folder.)
- Trending: explained to owner (TMDB `/trending/all/day` only — TMDB-site activity, no cross-check); proposal for a demand score pending decision.

## Log — 2026-10-03 — Review fixes for 7d4a067 (pacing dropdown, thumbnails, Drive)
- **Pacing dropdown wiped the schedule**: `PUT /api/schedule/config` replaces the whole schedule, but `applyPacing` (QueuePanel) sent only `posts_per_day` / `pipeline_overrides`, so timezone, hours, interval and all overrides went back to defaults. It now sends the full saved schedule with only the one value changed. Custom input capped at 48 (backend limit) with a clear message.
- **Thumbnail previews didn't refresh**: images were cache-busted by `rendered_at` only. Select/refresh now stamps `render.thumbnails_updated_at`, used in the thumbnail URLs.
- **Thumbnail refresh vs. render**: generate/select now refuse while `stage_status == "running"` (could write into `render/` mid-swap and fail the render) and generate merges into the *current* `p.render` instead of an earlier copy (was overwriting a newer render's `rendered_at`/`seconds`/`size`/`motion`).
- **Missing still → black thumbnail**: `thumbnail()` now uses the first of still/poster that exists on disk.
- **Drive guard**: the other session (fe6647d) restored the My Drive check with a more exact message; kept theirs, added a Shared Drive upload test.
- Verified: backend tests 169 pass / 1 skipped (new: Shared Drive upload, My Drive refusal, render-info merge + running refusal, poster fallback); `tsc --noEmit` clean. Not verified in the browser or on prod.

## PLAN FOR REVIEW — 2026-10-03 — Auto-production of trailer breakdowns (NOT BUILT; awaiting owner approval)

### Findings that shaped it (verified 2026-10-03)
- **Vercel failure emails**: every push triggers TWO deploys. (1) The GitHub Action (`deploy-frontend.yml`) deploys from `frontend/` with the CLI and **succeeds** (prod `scrapper.nodepilot.dev` is on the 07:07 build). (2) Vercel's own Git integration also builds each push from the **repo root** (project Root Directory is unset), finds no `package.json` (`ENOENT /vercel/path0/package.json`) → "npm install exited with 254" → email. 15 of the last 19 deploys are these. The site isn't broken; the second deploy path is. **Fix (needs owner OK, it's a Vercel setting): disconnect the Git integration** (Vercel → scrapper → Settings → Git → Disconnect, or `vercel git disconnect`) so the Action is the only deploy path. (Setting Root Directory = `frontend` instead would break the Action, which runs the CLI from inside `frontend/`.)
- **YouTube API on Railway**: `YOUTUBE_API_KEY` is set and works with **YouTube Data API v3** (test call OK). **No code uses it.** Default quota 10,000 units/day; `videos.list` = 1 unit, `search.list` = 100.
- **Real cost per breakdown** (Spending data, Oct 2026): #2 $0.37, #3 $0.57; most of it is shot analysis (`studio:shots`). Render is CPU only (tracked $0). So 5/day ≈ $2–3/day, inside $6. Not in the tracker: Railway CPU; TTS beyond the 1M free chars/month (5/day × 30 days could approach it — measure chars per script); search grounding beyond 5k/month.
- Pipeline today: gather → trailer → shots → script → plan → render (`runner.ORDER`); "Make breakdown" already auto-runs to script and stops for review. Only one Studio job runs at a time (`runner._lock`), which suits a one-by-one list. Every paid call already goes through `costs.check_budget()` (gemini.py, tts.py, metadata.py), so one daily cap there covers the whole site.

### A. Checked & ranked "Start a new breakdown" list
- New `studio/candidates.py` + table `studio_candidates` (tmdb_id, media_type, checks JSON, score, rank, status: listed / skipped / started / done / failed, reason, timestamps). Refreshed daily (and by a ↻ button) from the 3 calendar lists, deduped.
- Checks per title (each shown on the card as ✓/✕ with the reason; a ✕ is never auto-picked):
  1. Not already made (no StudioProject with this tmdb_id).
  2. Trailer actually available: IMDb official trailer (what step 2 downloads), else a TMDB `/videos` YouTube trailer confirmed live via `videos.list`.
  3. Enough facts: imdb_id, poster, overview, ≥3 cast.
  4. Release window: movie opens in ≤90 days or opened ≤30 days ago (US date); TV currently airing.
  5. Minimum interest: TMDB popularity / vote floor.
- Score (shown, sortable): YouTube trailer views and views/day since upload (gives the unused key a job; ~1 unit per 50 titles), TMDB popularity, days to release, trending flag. Owner can ⭐ pin or ✕ skip any title; pins go first.
- All free calls (TMDB, IMDb, YouTube quota). No AI cost.

### B. Auto-production switch
- Setting `app_settings.studio_auto {enabled, per_day: 5}`; card shows (rule 40): ON/OFF, next title it will take, last run + result, today "2 of 5 made", how to undo (switch off; running step finishes or press Stop).
- Every scheduler tick (5 min): if ON, Studio idle, today's count < 5, and today's spend + estimated cost of one breakdown (avg of last 5, ~$0.50) ≤ daily cap → take the top-ranked passing title → create project → run steps 1–5 (stops before render, as today). One at a time.
- A failed title is marked failed with its reason, then the next one starts (max 1 retry). Deploy restarts are already covered by auto-resume.
- "Day" = America/New_York (the posting schedule's timezone), stated in the UI.

### C. Review → mass render (step 6)
- "Your videos" gets checkboxes, filters (Ready for review / Reviewed / Rendered / In queue) and a per-project "✓ Reviewed" mark so "select all reviewed" is one click.
- `POST /api/studio/render-batch {ids}` → a resumable batch (same pattern as bulk AI: checkpoint = finished ids) that renders one by one, skipping anything not at script-done or archived. Progress "3 of 7", per-item result, Stop stops the batch.

### D. Mass send to queue
- `POST /api/studio/publish-batch {ids}` → runs the existing publish for each rendered, not-yet-queued project; per-item result. "Select all rendered" + "Send N to queue".
- (Drive copy is still blocked until the LongForm folder is in a Shared Drive.)

### E. $6/day site-wide spend cap
- `costs` settings gain `daily_usd` (default 6). `check_budget()` also refuses paid calls once today's variable spend ≥ $6 (America/New_York day), for everything on the site: Studio, queue AI, TTS.
- Flat subscriptions (Upload-Post $24/month) aren't counted against the daily cap; the monthly budget still covers them.
- Spending card: "Today $1.84 of $6.00", resets at midnight ET.
- If the cap is hit mid-breakdown, the step stops with "daily cap reached", and the automation resumes it the next day instead of failing it.

### Open questions for the owner
1. Should manual "Make breakdown" clicks count toward the 5/day, or is the 5 for the automation only? (Suggest: both count; a manual click past 5 asks to confirm.)
2. Should the cap stop *all* AI (queue captions too), or reserve e.g. $1 for the queue?
3. OK to disconnect Vercel's Git integration?

### Build order (each its own commit + push)
E (cap) → A (list) → B (switch) → C (mass render) → D (mass queue). Tests for each; RUNBOOK/CONFIG updated.

## Log — 2026-10-03 — Built: daily cap, checked trending list, automation, batch render/queue
- Owner approved the plan with changes: manual breakdowns count toward the daily limit (editable); cap stops all AI and paused work must resume first; split **3 movies + 2 TV** a day; trending list = **top 10 movies + top 10 TV**, checked, with **IMDb/other-site sentiment**; keep the release calendar.
- **Vercel Git integration disconnected** (`vercel git disconnect`); the GitHub Action is the only frontend deploy path. Rebased review fixes onto the other session's work and pushed (7841ebb).
- **Daily cap** (d791e72): see RUNBOOK "Daily spend cap". `DailyCapReached` is deliberately not wrapped, because shots/plan steps swallow GeminiError and would carry on degraded.
- **Candidates** (`studio/candidates.py`): TMDB trending/week (2 pages per type), checks (new / IMDb trailer / facts / release window / interest), sentiment = IMDb rating + Metacritic + newest user-review stars + MOVIEmeter direction + trailer like rate + TMDB vote; score 0–100 with parts. YouTube key was unused before; now `studio/youtube.py` reads trailer stats only. IMDb GraphQL returns a notice that non-commercial use only — same endpoint the trailer step already uses.
- **Automation** (`studio/auto.py`), **render batch** (`studio/batch.py`, ResumableJob `studio_batch`), **publish batch** route, **review** column. UI in `frontend/app/StudioAuto.tsx`.
- Verified: 187 backend tests pass; `tsc` clean. Local `next build` couldn't finish (laptop disk 96% full), so the CI build is the check. Live checks: see next entry.

## Log — 2026-10-03 — Live verification + frontend deploy finding
- Railway live with ee93516 in ~50 s. `/api/studio/auto`: OFF (default), 3 movies + 2 TV, estimate $0.47/breakdown. `/api/costs` today: $0 of $6 (day resets 04:00 UTC = midnight ET).
- First live candidate build: **10 movies + 10 TV** all auto-eligible, with real IMDb rating/Metacritic/review stars/trailer views (e.g. Lanterns 89.5, Resident Evil 84.4, Avengers: Doomsday 79.1). 60 didn't make the list: 40 passed but ranked below the top 10; the failures were mostly the release window (19), then facts (3), already made (1), interest (1).
- **The GitHub Action has never deployed**: all recent runs fail at "Require Vercel token" (no `VERCEL_API_TOKEN` repo secret). Every "Ready" deploy came from a manual `vercel deploy`. With the Git integration disconnected, **nothing auto-deploys the frontend until the owner adds that secret**. Deployed ee93516 manually; the live bundle has the new UI strings ("Breakdown automation", "Top trending (checked", "Daily cap", "Render selected").
- Not verified: automation actually starting a breakdown on prod (it's off until the owner switches it on); render batch on prod.

### Pending / Next
1. Owner: add GitHub secret `VERCEL_API_TOKEN` (Vercel → Account Settings → Tokens) so pushes deploy the frontend again.
2. Owner: move "LongForm Studio" Drive folder into a Shared Drive (robot as Content manager); Drive copies fail until then.
3. Owner: switch on automation when ready; watch the first run (card shows next title and why it's waiting).
4. Gemini voice-overs in SocialPilot are refused at the cap but their cost isn't recorded (pricing not tracked there).
5. Candidate thresholds (release window −30/+90 days, interest floors) are constants in `studio/candidates.py`; tune after a week of results.

## Log — 2026-10-03 (pm) — OUTAGE: site-wide DB deadlock (fixed), plus review fixes
- **Symptom**: the owner saw "Loading automation…" forever. Every endpoint timed out (`QueuePool limit of size 5 overflow 10 reached`); the whole site was down.
- **Cause, from 2 py-spy dumps of the live process** (`railway ssh` → `pip install py-spy` → `py-spy dump --pid 6`): ~40 AnyIO worker threads were all blocked in `pool._do_get`, while the 15 connections belonged to requests that had finished their queries but were waiting for a worker thread. First dump: waiting to run the sync `get_session` teardown. Second dump, after fix 1: waiting to serialize the response, still holding the connection. A deadlock between a bounded pool and a bounded thread pool. More UI polling (new cards) + several tabs/devices + the calendar's 8-thread TMDB fan-out pushed it over.
- **Fix 1** (6d706c5): `get_session` is an async generator (close runs on the event loop). Not enough on its own: a 200-request burst wedged it again.
- **Fix 2** (d7f8494): SQLite uses **NullPool**, so there is no connection limit (a SQLite connection is a file handle) and nothing to deadlock on. Busy timeout 30 s. `/api/events` (live logs) is now an async generator; the sync one parked one worker thread per open Logs panel. **Production is SQLite** (no DATABASE_URL); Postgres keeps a pool of 10 + 20.
- **Verified live**: 300-request burst at 100 concurrency → 69 served (max 1.2 s), the rest got 429 from the rate limiter (600/min/IP), server healthy after. Before the fix, ~40+ concurrent requests wedged it.
- **Lanterns script error** ("Unterminated string … char 1738") was NOT caused by a deploy: Gemini 3.x thinking tokens count toward maxOutputTokens, so the JSON was cut off. `ask_json` now retries up to 3× and doubles the room when finishReason=MAX_TOKENS; errors say the finish reason.
- **Resident Evil "not started"**: created while Lanterns ran, so `runner.start` refused it ("busy") and the project was orphaned. Now `runner.start_or_queue`: the start waits in line (ResumableJob status `queued`, project `stage_status=queued`). The next one starts when Studio frees up, and the scheduler tick also moves the line. Order: budget-paused first, then the line, then automation. Stop removes a queued start.
- **Thumbnails**: plain picture only — no dark band, title or "TRAILER BREAKDOWN" badge (owner).
- **Drive**: owner pointed LONGFORM_DRIVE_FOLDER_ID at 1wnjIQGf-Ftk2PSBYHG0LCy2K99yaXiUk. Live retry still refused: the folder is in a personal My Drive, and the robot is a member of 0 Shared Drives. Still pending on the owner.
- **Button audit**: all 85 frontend API calls map to a backend route (FastAPI 0.142 nests included routers as `_IncludedRouter`; recurse to list them). 27 read endpoints answer 200 in about 0.1 s on prod (calendar 5.6 s cold, cached 3 h). Signed media works (206), unsigned is refused (401). Clip thumbs 404 because the 5-day retention removed the files (expected).

## PLAN — 2026-10-03 (pm, owner request) — Drive, 25+25 list, never repeat, 3-day refresh, cost deep dive
1. **Drive**: owner set LONGFORM_DRIVE_FOLDER_ID on Railway to `0AJrfdFAM4T9iUk9PVA` (a Shared Drive root id — "0A…" ids are drives). Verify with a live "Save to Drive" retry on Digger (#3); then By Any Means (#2) and Send Help (#1) if it works.
2. **Top trending 25 movies + 25 TV** (was 10): `candidates.TOP_N = 25`; pool goes from 2 to 4 TMDB pages per type (80 per type) so 25 can still pass the checks.
3. **Never repeat a breakdown**: today "not made" is live from existing projects, so deleting a project would make its title eligible again. Add a permanent ledger (`app_settings.studio_made`: every movie/tv id ever started, manual or automatic, written on project create and back-filled from existing projects). The "not made" check and the automation both read it. A title that keeps trending stays ✕ "already made" forever. (Manual "Make breakdown" on an already-made title asks to confirm.)
4. **Refresh every 3 days** (was 24 h): `STALE_HOURS = 72`; ↻ still refreshes on demand. Cost: $0 — TMDB and IMDb are free; YouTube uses ~2–4 of the 10,000 free daily quota units per refresh.
5. **Cost deep dive**: every service that costs money, the measured cost per unit from Spending data, and projections per day / week / month / year for the current setup (5 breakdowns/day + queue AI + posting), written into MEMORY.md and reported to the owner.
Each step: tests, commit, push, frontend deploy (manual until the VERCEL_API_TOKEN GitHub secret exists).

## Log — 2026-10-03 (pm) — Plan done: Drive works, delete/archive, never-repeat, 25+25 / 3 days, cost deep dive
- **Drive works**: LONGFORM_DRIVE_FOLDER_ID = `0AJrfdFAM4T9iUk9PVA` (a real Shared Drive). Live saves OK for Digger, By Any Means, Send Help (links in each project's Drive status).
- **Your videos**: 🗄 Archive (deleted automatically 5 days later; ↩ Keep cancels) and 🗑 Delete, per card and bulk, plus an "Archived" filter showing the delete date. Delete refuses a video whose queue post hasn't gone out unless confirmed, then removes that queue row too. The old project-page Delete failed silently on any refusal; it now shows the reason. Drive copies are never touched.
- **Never repeat**: `app_settings.studio_made` ledger, written on create and on delete. The list, the automation and manual create (asks to confirm) all respect it.
- **List**: top 25 movies + 25 TV (TMDB trending week, 4 pages per type), rebuilt every 72 h; ↻ any time. Cost $0.
- **Restart/line**: a live deploy mid-run showed a resume losing the race to a new automation pick and getting marked failed (Resident Evil). Fixed (dd58528): it waits in line and resumes from its step, and the automation waits for cut-off jobs. RE re-queued.
- **Waiting line verified live**: #6 finished → #4 Lanterns' script started by itself → #5 started by itself. Lanterns' script succeeded after the JSON-retry fix.

### Cost deep dive (measured 2026-10-03; prices in costs.py / Settings → Spending)
**What costs money**
| Item | Kind | Measured / price |
|---|---|---|
| Gemini 3.8 Flash (Studio + queue AI) | pay-as-you-go | $0.75 in / $3.75 out per 1M tokens until 2026-12-31, then **$1.50 / $7.50** |
| — one breakdown, steps 1–5 | | **avg $0.39** (5 runs: $0.31–0.57) = shot tagging 64%, script 19%, plan 10%, research 5% |
| — render (step 6) | | $0 API (Railway CPU only) |
| — queue AI captions | | ~$0.002 per video ($0.05 for 30 so far) |
| Google TTS (Chirp 3 HD) | free tier | 1M chars/month free, then $30/M. ~3.3K chars per breakdown → ~500K/month at 5/day = **$0** |
| Gemini search grounding | free tier | 5,000/month free, then $14/1K; ~1 per breakdown → **$0** |
| Upload-Post Basic | subscription | **$24/month** flat (biggest fixed cost) |
| Railway (server + volume + egress) | usage | **$7.61 so far this period (Sep 7–Oct 7), Railway estimates $9.01**: memory $4.15, egress $2.19, CPU $0.66, volume $0.43, backups $0.19 |
| TMDB, IMDb, YouTube Data API, Google Drive | free | $0 (YouTube ~4 quota units per list refresh) |
| Vercel (frontend) | assumed Hobby | $0 (not verified) |
| Gemini voice-overs in SocialPilot | **not tracked** | refused at the cap, but their cost isn't recorded |
| Domain, Google Workspace (Shared Drive storage) | outside the app | not tracked here |

**Projection at 5 breakdowns/day + light queue AI**
| | per day | per week | per month | per year |
|---|---|---|---|---|
| Gemini (2026 prices) | $2.00 | $14 | $60 | $730 |
| Upload-Post | $0.80 | $5.60 | $24 | $288 |
| Railway (est. with 5 renders/day + uploads) | ~$0.40 | ~$2.80 | ~$12 | ~$145 |
| **Total, 2026 prices** | **~$3.20** | **~$22** | **~$96** | **~$1,160** |
| **Total, 2027 Gemini prices** | **~$5.15** | **~$36** | **~$155** | **~$1,880** |
Both stay under the $6/day cap (which counts Gemini/TTS/search only, not subscriptions or Railway).

**Where to save, biggest first**: (1) shot tagging = 64% of each breakdown — tag with Gemini Flash-Lite ($0.30/$2.50) and skip look-alike shots before tagging → est. −$0.15 to −$0.20 per breakdown (~$25–30/month); (2) the 2027 price rise doubles Gemini, so do (1) before January; (3) Railway memory is the largest Railway line — check the plan's container size; (4) Upload-Post plan only if the posting volume needs it.

## Log — 2026-10-03 (late) — live restart tests, dark-trailer shots, Render selected, deploy-safe resume
- **Live restart tests** (my deploys landed mid-run): cut-off jobs now wait in line and resume from their step. Two more bugs found and fixed:
  - A day of deploys used up the 2-resume limit (Verity, a render batch). Shutdown now flags running jobs `cut_by_shutdown`, so their resume doesn't count (b213f3b).
  - **Render selected** polled for a free Studio, so queued jobs always got in first and the card showed nothing. It now joins the waiting line (385dd49); the card shows "render waiting…". Verified live: East of Eden (#6) is in the line behind Verity → Resident Evil.
- **Resident Evil: 13 shots / 3 usable** came from the fixed scene threshold 0.3 on dark footage (4 cuts in a 152 s trailer; 35 at 0.12, 62 at 0.08; Digger 111 at 0.3). Now an adaptive threshold from one ffmpeg pass (fa49b97), and a failed ffmpeg raises instead of yielding "one long shot". RE's shots + script are being redone. Its first script (from 3 shots) is replaced.
- Owner asked to raise today's cap to finish RE + Verity: not needed — $1.21 of $6 spent. The block was the 5/day breakdown limit, which only gates NEW breakdowns.
- Pending for the owner: add GitHub secret VERCEL_API_TOKEN (steps in the chat); button suggestions given in the chat (not built).

## PLAN — 2026-10-03 (evening, owner request) — action buttons, Refresh Options, cheaper AI
Already existed (not duplicated): Posting Queue "↩ Undo" (top of queue) and "✓ Approve" (= next free slot, i.e. "post at best time").
1. **Refresh Options fix**: it re-picked the same shots, so the images never changed. Each refresh now rotates to the next-best shots not yet shown (wraps around) and poster ↔ backdrops; button shows "Refreshing…".
2. **Project page**: "▶ Continue to step 5" (runs whatever is missing up to Voice + shots) and "↻ from here" on each finished step (redo it and the following steps up to 5).
3. **Your videos cards**: "▶ Resume" on stopped / error / paused cards (continues from the step it was on), "⏫ Move up" on waiting cards (goes to the front of Studio's line).
4. **Automation card**: "⏭ Skip next pick" (marks it skipped, shows the new next pick) and "⏸ Pause for today" (switches back on at midnight, shown on the card).
5. **Trending list**: "👁 Preview trailer" (plays the official YouTube trailer in-page) and "+ Add to line" (starts steps 1–5 in Studio's line without leaving the list; same limits/never-repeat rules).
6. **Spending card**: "+$2 today only" (raises today's cap; resets at midnight) and a cost-per-breakdown bar list.
7. **Cheaper AI (shot tagging = 64% of each breakdown)**:
   a. **Gemini Batch Mode (50% off, async)** for shot tagging on *automation* runs (nobody is waiting). Wait up to 30 min, then fall back to normal calls. Manual runs stay instant. Cost recorded at the batch price.
   b. **Look-alike shots tagged once**: shots whose frames are near-identical (dHash) share one tag result instead of a Gemini call each.
   Expected: −50% on automation tagging plus fewer frames sent → roughly $0.39 → ~$0.20–0.25 per automated breakdown. Measure on the next runs.
Tests for each; commit + push; manual frontend deploy until the VERCEL_API_TOKEN secret exists.

## Log — 2026-10-03 (evening) — Plan done: buttons, Refresh Options, Batch Mode (64ac08c)
- **Built & verified live**: ▶ Continue to step 5, ↻ from here, ▶ Resume, ⏫ Move up, ⏭ Skip next pick, ⏸ Pause for today, 👁 Preview trailer, + Add to line, +$2 today only, cost-per-breakdown bars. All strings are in the served bundle; the routes answer (resume on a finished project → 409 as designed; move-up on a non-waiting one → 409).
- **Refresh Options** live on Digger: round 0 → shots s036/s002, round 1 → s171/s005 (it redrew identical picks before).
- **Gemini Batch Mode verified in production** (`railway ssh` → `gemini.ask_json_batch`): 2/2 answers in **109 s**. Automation shot tagging now uses it (half price, recorded as service `gemini-batch`); after 30 min it falls back to normal calls. Look-alike neighbours (dHash ≤5 bits AND mean RGB within 12) share one tag call. The colour check was needed: dHash alone merged flat red/blue/green frames.
- **Not yet measured**: savings on a full automated breakdown (the next automation run — today's limits are full). Expected ~$0.39 → ~$0.20–0.25.
- Today: $1.94 of $6. Breakdown costs: Verity $0.44, RE $0.48 (shots run twice), Lanterns $0.39, East of Eden $0.33, Love Hypothesis $0.31.

## Log — 2026-10-03 (night) — Frontend auto-deploy fixed
- Owner added the GitHub secret `VERCEL_API_TOKEN`. Run 37149883106 (commit 1b7712a) passed every step and produced a new Ready production deploy. **Pushes to `frontend/**` now deploy the site by themselves**; no more manual deploys.
- The workflow now also runs when `.github/workflows/deploy-frontend.yml` changes.
- The owner also put the token in Railway as `github_deploy`. The backend never reads it; suggested removing it there (least privilege). Railway also still has `VERCEL_API_TOKEN`/`VERCEL_ORG_ID`/`VERCEL_PROJECT_ID` from before, also unused by the backend.
- Handbook republished as v3 (same link https://claude.ai/artifact/SF2MvUyAwnXo6rcEnXikwP): automation, checked trending list, Studio's line, project/card buttons, batch render/queue, Archive/Delete, plain thumbnails, $6 daily cap, costs, new troubleshooting rows, daily review checklist.

## PLAN — 2026-10-04 — Disk full (97.6%), footage freeing, shot usability
Cause: 13 Studio projects at ~210–400 MB each (≈70% trailer footage) on the 5 GB volume; nothing freed them until 14 days after posting. The Scraper 5-day retention is irrelevant (72 KB of clips).
1. Free footage + segment cache of every project that is rendered AND has a Drive copy of the current render (#1–#8 now, ~2.3 GB). Keep the render (the queue posts it), stills, script, plan, thumbnails. Uploaded footage is never deleted.
2. Rendering re-downloads freed footage from IMDb first, automatically (same video ids → same shots; no AI cost).
3. Automatic: right after a Drive save of the current render succeeds, that project's footage + segcache are freed; also a sweep for any that were missed.
4. Shot usability (owner): only title/logo **cards** are unusable. Dark, blurry or burned-in-text shots (e.g. MobLand's corner watermark flagged every shot as "text") are usable. Owner overrides stay. Recompute existing projects' shots from their saved tags (no AI cost), then re-run MobLand (#10) from step 5.

## Log — 2026-10-04 — Disk fixed (97.6% → 49%), card-only usability (d804530)
- Startup sweep freed footage + segcache of #1–5, 7, 8 (2.08 GB). East of Eden (#6) had a newer render than its Drive copy (re-rendered 2 min after the save), so its footage was rightly kept. Re-saved #6 to Drive → the hook freed 314 MB by itself. **Volume now 2.2 GB used / 2.4 GB free (49%)**; new jobs accepted again.
- Usability now = tagged and not a card. Recompute on saved tags (no AI): MobLand 2 → 85, Lanterns 14 → 112, RE 19 → 75, East of Eden 36 → 84, UNABOMBER 23 → 78 (all 13 projects changed). MobLand (#10) step 5 re-run: 67 visuals, 65 distinct shots → ready for review.
- Still pending / for the owner: #9, #11, #12, #13 plans were built from the smaller shot pools; re-running step 5 would give more variety (~$0.05 each). Unreviewed projects keep ~200–300 MB of footage each until rendered + Drive-saved; 5 GB fits ~6–8 of those, so review/render within ~2 days, or get a bigger volume. Freed footage also means the shot lightbox's ▶ clip preview won't play for those projects until a re-render brings it back.

## Log — 2026-10-04 — INCIDENT: 12 posts on Sat 3 Oct instead of 3 (fixed, 6d2ee64)
- **What happened (from the logs, ET)**: Fri 23:53–23:59 the owner set 3/day global (10–20) and Movie Clips 3/day (10–22). Sat: 10:00 → #647 (+ LongForm #701, its own pipeline). 16:00 slot → 8 Movie Clips posts between 16:01 and 16:29. 22:00 slot → #324 and #694 posted, then YouTube's per-channel daily upload limit refused #211, #410, #10, #654, #16 and #564. Sun 10:01 → #111 refused too (YouTube's limit is a rolling ~24 h).
- **Cause**: `plan_schedule` decided whether a pipeline had used its current slot by looking at the GLOBAL schedule's slot (15:00/20:00), not the pipeline's own (16:00/22:00). Movie Clips never looked used, so every 5-min tick re-assigned its slot to the next video for the 30-min grace window. At 10:00 both schedules shared the slot, so only one posted.
- **Fix**: per-pipeline slot check + a hard limit in the tick (never more scheduled posts per pipeline per day than its posts/day). Default posts/day = **1** for every pipeline (`POST_POSTS_PER_DAY`, owner rule). Regression test replays the incident and fails on the old code.
- **Live after fix**: 1/day at 10:00 ET for Movie Clips and LongForm, no overrides (owner set this Sun 11:22). Next Movie Clips post Mon 5 Oct 10:00 ET.
- **Pending for the owner**: the 7 Retry rows (#211, #410, #10, #654, #16, #564, #111) — set them back to Ready (they'd take the next daily slots) or leave them.

## PLAN — 2026-10-04 (pm, owner request) — Retry rows, block "mk", unused pipelines, shot-plan rhythm + real QA
1. ✅ Done: the 7 Retry rows (#211, #410, #10, #654, #16, #564, #111) moved to Needs Review (bulk change_status; undoable).
2. **Never post Posting Queue or LongForm videos to profile "mk"**: enforced in the backend at every point a destination is set (single change, bulk Set Target Accounts, edit) and again at posting time (an "mk" destination is refused and noted, never sent). The "mk" choices are removed from the queue's destination pickers. Any existing queue rows that target mk get it removed; rows left with no destination stay unscheduled until a destination is picked.
3. **Remove Abyss Declassified, The ICK Room and Default Pipeline** from the Pacing card (and the queue's pipeline choices) — only Movie Clips and LongForm remain. Any leftover pacing overrides for them are dropped.
4. **Voice + shots plan rhythm (stage_plan)** — hard rules, not just flags:
   a. Slot 1 at 0:00 is a moving clip, not a still.
   b. After every clip, at least 2 stills before the next clip (never two clips back to back).
   c. Never more than 3 stills in a row: the 4th visual after a clip is a clip.
   d. **Repeats are replaced, not just flagged**: a picture/look already used is swapped for an unused one (same people first, then any unused usable shot) whenever one exists; a repeat is allowed only when the pool is truly exhausted. Applies to Gemini's picks and to the fallback order. The QA line then reports the few repeats left and why.
   Existing plans (MobLand, Coven Academy, UNABOMBER) aren't re-run automatically; re-running step 5 on them applies the new rules (~$0.05 each).
Tests for each; commit + push per part (the frontend deploys itself).

## Log — 2026-10-04 (pm) — Plan done: retry rows, block mk, unused pipelines, shot-plan rhythm
1. 7 Retry rows → Needs Review (live, verified).
2. **Profile "mk" blocked for queue/LongForm** (50ce4fd): `queue_manager.BLOCKED_PROFILES`; refused on edit/bulk (400), dropped on create, stripped at startup, stripped/refused at posting; the queue picker hides it (Compilations dialog unaffected).
3. Abyss Declassified / The ICK Room / Default removed from the Pacing card and the queue's pipeline choices (50ce4fd).
4. **Shot plan** (this commit): clip at 0:00; rhythm clip → 2–3 stills → clip (`apply_rhythm`); scarce pictures → longer visuals (≤7 s) instead of repeats. Not re-run on existing plans (MobLand, Coven Academy, UNABOMBER): ↻ step 5 applies it (~$0.05 each).
- Tests: 218 pass.

## PLAN — 2026-10-04 (evening) — Owner locked out (429 on sign-in)
Cause: auth lockout (20 wrong tokens / 10 min → 15-min block per IP) counts EVERY request with a missing or wrong token. The page polls several endpoints every few seconds, so one wrong paste (likely Railway's `API_Read_Access_Token` = the TMDB key, not `ACCESS_TOKEN`) or a tab with no token locks the home IP within seconds, and keeps re-locking it while the tab stays open.
1. Backend: a missing token is not a guess (not counted); a wrong token counts once per distinct value (polling with the same wrong value ≠ brute force). Lock after 20 distinct wrong values in 10 min (brute-force protection kept).
2. Frontend: after a 401 the page stops background polling and shows "wrong access token" in the Connection panel until a new token is saved.
3. Deploy (restart clears the in-memory lockout). Then the owner signs in with the value of Railway's `ACCESS_TOKEN`.

## Log — 2026-10-04 (evening) — Owner lockout fixed (6811b23)
- Confirmed live: even the correct token got 429 from the owner's home address. Railway `ACCESS_TOKEN` matches `data/access_token.txt`, so the token itself was fine; the address was locked.
- Fix shipped: a missing token is never counted; a wrong token counts once per distinct value; the page stops sending after a 401 until a new token is saved; the Connection panel names `ACCESS_TOKEN`. The restart cleared the lockout: the home address gets 200 again. The website deployed by itself via the Action.
- Tests: 219 pass (the old lockout test now uses 20 different guesses; a new test covers repeated same-wrong and missing tokens).

## PLAN — 2026-10-04 (night, owner request) — LongForm straight to Ready, posted → Archive, review highlight, Refresh Options
1. **Send to Posting Queue → Ready to Post, destination "default — all channels" (Screen Central)**: the LongForm row is created (or updated) as status `ready` with accounts `["default:*"]`, so the scheduler gives it the next LongForm slot (1/day). A row that already has destinations keeps them unless they're empty. ("mk" stays blocked.)
2. **Posted → Archive automatically**: when Upload-Post/YouTube confirms a post, the row goes to the Posted Archive (status `archived`, posted time kept) instead of staying `posted`; "All Items" no longer lists archived/posted rows. Fix Digger (#700) and By Any Means (#701), whose rows are wrong even though they posted. Check why first (likely a re-send after posting reset them, or a status never reconciled). Keep the 4-day Shorts clean-up and the LongForm archive rule working with the new status.
3. **Highlight in Your videos**: cards that need attention stand out — script/plan done but not reviewed ("Needs review"), and rendered but not yet in the queue ("Ready to send") — with a coloured border, a badge, and sorted to the top.
4. **Refresh Options needs 2 clicks**: the first refresh used rotation round 0 = the same picks the render already made. The render now records round 0, so the first click shows new shots.

## Log — 2026-10-04 (night) — Plan done (f08b6d8)
- Send to Posting Queue → Ready to Post, accounts `["default:*"]` (Screen Central); scheduler woken at once. Covered by the updated publish test.
- Posting confirmed → status `archived` (published_at kept). Startup moved 25 posted rows to the archive, incl. Digger #700 and By Any Means #701 (live: posted 0, archived 25). Posted Archive tab = posted+archived; All Items = rows still in play. The daily limit, Drive-link update and LongForm archive rule count archived.
- Your videos: amber "Ready for your review" / blue "Rendered — not in the queue yet" highlight + sort + count line.
- Refresh Options: the render stores thumb_round 0 → the first refresh shows new shots.
- Live check: Review 0 / Ready 678 came from the owner's own bulk changes 18:10–18:38 (7 Movie Clips rows + #709–712 set to Ready, destinations assigned), not from code.
- Tests: 221 pass.

## Log — 2026-10-05 — Repo moved out of iCloud to ~/dev/scrapper
- The Mac's disk hit 99% (8 GB free) and iCloud "Desktop & Documents" evicted 9,376 files of ~/Desktop/scrapper to the cloud (incl. `.git/index` and pack files) → `git status` (IDE and CLI) hung for minutes and commits were impossible.
- Working copy is now **~/dev/scrapper** (not iCloud-synced; the launchd Mac worker already ran from it). Fast-forwarded to 8e41f83; same access token, `.venv`, `node_modules`, `.vercel` link. ~/Desktop/scrapper is retired (owner can delete it once happy).

## PLAN — 2026-10-05 — Website feels slow / glitchy (owner, on phone)
Measured: the queue re-downloads all 703 rows every 4 s (688 KB raw, 166 KB gzipped ≈ 2.5 MB a minute on mobile data) and redraws every row (678 cards on Ready to Post) each time. The main page polls 4 endpoints every 2.5 s and the jobs bar every 3 s, even in a background tab. On phones the posts/day menu opens off the right edge (anchored left:0 under a right-side button). Row size is real content (descriptions 37%), not waste.
1. Queue: background refresh every 20 s instead of 4 s, only while the page is visible; nothing redraws when the data hasn't changed; refresh at once after your own actions.
2. Queue: draw 50 rows at a time with "Show 50 more" (select-all still covers every row in the tab).
3. Main page: poll every 10 s instead of 2.5 s, paused while hidden; jobs bar paused while hidden.
4. Phone: posts/day menu anchored to the right edge and kept on screen.

## Log — 2026-10-05 — Website performance fixed (ef4fe81, deployed by the Action)
- Queue: 20 s visible-only refresh (was 4 s, always), no redraw when unchanged (it redrew ~700 rows twice per refresh), 50-row pages with "Show 50 more"; main page 10 s visible-only (was 2.5 s); jobs bar, pacing card and Studio timers pause while hidden; the phone posts/day menu is a bottom sheet.
- Effect on a phone: queue traffic ~2.5 MB/min → ~0.5 MB/min while viewing (0 in the background); background redraws only when something actually changed.
- Verified: tsc clean, the Action deployed (success), the live bundle contains the new "Show 50 more". Not verified on a real phone by me.

## PLAN — 2026-10-05 — Mobile layout review (owner: "why didn't you fix the mobile issue")
The performance fix (ef4fe81) was verified by measurement only; the site was never looked at at phone width. Now:
1. Open scrapper.nodepilot.dev in Chrome at iPhone width (390 px), sign in, and go through every tab: Scraper, SocialPilot (Posting Queue, LongForm, Pacing, Channel Ingest, Drive Sync), LongForm Studio (list + one project), Compilations, Logs, Settings.
2. Record every layout problem (overflow off screen, clipped text, unreadable or overlapping controls, oversized tabs) with a screenshot.
3. Fix them, deploy, then re-check the same screens at phone width.

## Log — 2026-10-05 — Mobile layout reviewed and fixed at 390 px (731c9ff)
- Method: Chrome (owner's profile already signed in), the site loaded in 390-px same-origin iframes (the automation window counts as hidden, so the paused polling seen there is a test artefact).
- Found: blank "Connecting…" while the app waited for the media pass; "offline" after one failed first check; SocialPilot's 5 buttons stacked a screen tall; queue status tabs wrapping ("Ready / to / Post") and running off the edge; Storyboard buttons off the right edge; bright white scrollbars; long backup names overflowing.
- After: app shown 0.76 s after load; no page wider than the screen and nothing off-screen outside a scroll row on SocialPilot, Scraper or Settings; one-line tab rows that scroll; dark thin scrollbars.
- Noticed, not fixed: repeated `archive_trash_failed` 403 "insufficient permissions" when the 4-day sweep trashes old Movie Clips Drive files (e.g. item #124): the robot account can't trash files it doesn't own. Current owner settings: posts/day 2, daily cap $2 (set by the owner).

## PLAN — 2026-10-05 (evening, owner request) — Video first, thumbnails in queue Edit, auto free space at 80%
Trigger: Railway emailed "Volume of scrapper is 80% full in production".
1. **LongForm project page**: move the Video section (player, thumbnails, Send to queue, Drive) above "Research — facts & sources".
2. **Change the thumbnail from the queue's Edit (Ready to Post etc.)**: the Edit dialog shows the current thumbnail. LongForm rows offer the breakdown's 3 options (Poster / Lead Close-Up / Key Scene Still) plus 🔄 more options; any row can upload its own image (JPG/PNG/WebP, ≤ 5 MB). The chosen image becomes the row's `thumb_path` (what Upload-Post sends). Check what Upload-Post does with it.
3. **Auto free space at 80% (backup first)**: every scheduler tick checks the volume; at ≥ 80% it (a) makes a database backup, (b) makes sure each rendered breakdown's current render is saved to Drive (saves it if not), (c) then frees what can be rebuilt, oldest first, until under 70%: footage + segment cache of Drive-saved breakdowns, the motion-graphics cache, old database backups beyond the newest 7, Scraper clips past retention. It never deletes a render the queue still has to post, or anything not backed up. One log line per run with what was freed; at most once an hour. Measure what's on the volume first.

## Log — 2026-10-05 (evening) — Plan done: video first, queue thumbnail edit, 80% disk guard
- **Disk guard** (c1d0931): `backend/app/space.py`, called each scheduler tick. At ≥ `DISK_FREE_AT_PERCENT` (80) — hourly at most, as a stoppable job — it backs up the DB (nothing freed if that fails), saves any un-saved current render to Drive, then frees until < 70%: footage+segcache of Drive-saved breakdowns → motion cache → local backups beyond 3 → expired Scraper clips. Volume when built: 45% (the 80% email came while today's breakdowns held footage before review). Tests switch it off (`DISK_FREE_AT_PERCENT=101`) because the laptop's own disk is 99% full.
- **Video section** above Research on the project page (157dc8f).
- **Queue Edit → Thumbnail** (157dc8f): current image + LongForm options (pick / 🔄 new) + upload (≤ 5 MB, stored as a JPEG ≤ 2 MB = Upload-Post's limit; YouTube applies custom thumbnails only on verified channels). Verified live in Chrome on #703 Verity: dialog, 3 options and the current image load; closed without saving.
- Tests: 226 pass.

## Log — 2026-10-05 (night) — Selected thumbnail shown in SocialPilot rows (222d1c8)
- Queue rows with a thumbnail (the 19 LongForm rows; Drive Shorts have none) show it above the title (table + phone cards); click → Edit to change it. `/api/queue` returns `thumb_version` (mtime) so a re-picked option shows at once.
- Verified live at 390 px: 15 Ready LongForm rows show their thumbnails (200 image/jpeg; lazy-loaded, so they appear as you scroll).

## PLAN — 2026-10-05 (night, owner request) — Control posting times per queue and per video
Today: posts/day per pipeline only (and the Posting Queue dropdown edits the GLOBAL value LongForm inherits); one shared hours window; no exact times; a time set on a video is overwritten by the 5-min re-plan; "next video" only by re-sorting.
1. **Separate schedules for Posting Queue (Movie Clips) and LongForm**: each has its own posts/day and either a time window (first–last post) or **exact times** (e.g. 10:00, 18:30). The ⏰ button on each tab edits only that queue and shows its next 5 post times. Backend: pipeline overrides gain `times: ["HH:MM", ...]`; the Posting Queue dropdown writes the "Movie Clips" override, not the global value.
2. **📌 Post a video at a set time**: in Edit, pick a date/time and lock it. The planner keeps it (never re-planned) and fills the other slots around it; clearing it returns the video to normal planning. A pinned post goes out at its time and doesn't use up one of the day's regular slots. New column `queue_items.pinned_at` (additive migration).
3. **⏫ Post next**: per-row button that moves a video to the front of its queue's posting order (takes the next slot).
Tests for each (incl. the 3 Oct incident replay still passing); commit + push per part; check the result on the live site at phone width.

## Log — 2026-10-05 (night) — Plan done: per-queue schedules, pinned times, post next (3fc248f backend, 3147b5a UI)
- Each queue tab's "⏰ N/day" button edits only that queue (Posting Queue = "Movie Clips" override, LongForm = "LongForm"): Spread across hours (posts/day + first/last post) or Exact times (`times: ["HH:MM"]`), shows the next 5 post times, "Use the default" drops the override. Before, the Posting Queue dropdown edited the GLOBAL value LongForm inherited.
- Edit → "📌 Post at a set time": `queue_items.pinned_at` (additive migration); never re-planned; doesn't use up a regular daily slot; unpin returns it to planning. Rows show 📌 + "(pinned)".
- Ready rows: "⏫ Post next" → front of the posting order → next slot.
- Verified live at 390 px: LongForm sheet (2/day, 10 AM–8 PM; next Tue 10 AM, Tue 8 PM, Wed 10 AM…), 14 Post next buttons, pin control in Edit; nothing saved. Tests 229 pass (incl. the 3 Oct incident replay).

## PLAN — 2026-10-06 — "I don't see it" + posted videos still counted in All
Checked: the server archives every posted row (30 archived, 0 posted) and the live bundle contains the new schedule/pin/post-next UI. But: (a) the sidebar "708 items" and the "📋 Posting Queue (708)" button count ALL rows incl. the 30 archived (`/api/queue/counts` "all"); (b) an open page or a phone's saved copy keeps running the old version — nothing tells it a new one exists.
1. Counts: "all" = rows still in play (not posted/archived); a separate "archived" count stays for the Posted Archive tab. Sidebar + Posting Queue button use it.
2. Update banner: each build writes its version (git commit) into the page and `/version.json`; the page checks every 5 min and when it comes back to the foreground; on a new version it shows "A new version is ready — Reload" (auto-reloads if the tab was in the background).
3. Clearer labels so the new controls are findable: "⏰ Schedule · N/day", and the "Post next" / "📌 Pin" hints.

## Log — 2026-10-06 — Counts + update banner (7b384ee)
- Cause of "I don't see it": the server and the live bundle were already up to date; the owner's device was showing an older copy of the page, and nothing told it a newer one existed. Now each build carries its version (GITHUB_SHA → `NEXT_PUBLIC_BUILD_ID` → `/version.json`, static per build). The page checks every 5 min and on return to the tab and shows "A new version of Scrapper is ready — Reload" (a background tab reloads itself on return). Pages loaded before 7b384ee need one manual reload to get this.
- Cause of "posted videos still in All": every post was archived (30 archived, 0 posted), but `/api/queue/counts` "all" included them, so the sidebar and the Posting Queue button said 708. Now "all" = still in play: live 678.
- Schedule button now reads "⏰ Schedule · N/day · hours". Verified live in Chrome. Tests 230 pass.
