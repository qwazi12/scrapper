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
