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

### Log — 2026-10-17 — Per-platform caption limits (a3fce00)
- #749 (horror countdown) failed at its 12:00 slot: Instagram refused the 3,723-char description (max 2,200),
  which failed YouTube + Facebook too (one request per profile). upload_post.upload_video now fits per platform:
  youtube_description ≤5000, no < >, tags ≤500 chars; facebook_description = full text; shared description
  (Instagram's) = fit_instagram(): ≤2200 chars / ≤30 hashtags, timestamps + chapter headings dropped, cut at a
  whole line, rank markers (#15) not counted as hashtags. #749's real caption → 2,158 chars, 12 hashtags.
  Tests 304 passed. #749 set back to Ready → next Countdowns slot Sun Oct 18 12:00 PM ET (not posted yet, so the
  fix is not yet verified against Upload-Post itself).

### Log — 2026-10-14 — Top 5/10/15 drive the topic list (ecf7792)
- Before: 13/15 topics "Top 10", only 1 could fill a Top 15 (AI gave ~10 titles), buttons only set the box,
  "Top 15 Best Movies of All Time" had 10 titles. Now: buttons filter to topics that can fill that size and reword
  "Top N" (countdown_topics.sized); AI asked for 18 titles (ASK_TITLES); best size clamped (_best); countdowns made
  from a topic are worded for their size. Tests 300 passed; frontend type-checked, not visually verified.
- Topic list rebuild deferred to 04:10 UTC 2026-10-15: vidIQ was at 95/100 daily credits (day = ET).
- 2026-10-15 04:12 UTC rebuild VERIFIED: 15 topics, all 15 can fill Top 5/10/15, all with vidIQ demand; vidIQ 20/100 used.

### Log — 2026-10-14 — Countdown line visible where you add to it
- Owner's 3 "+ Add to line" topics were safe in countdown_line (1. Top 15 Modern Scary Movies of the 2020s,
  2. Top 10 1980s Horror, 3. Top 10 Claustrophobic Survival Thrillers); they leave the topic list by design
  but the line only showed in the automation card. New YourLine box in "Start a new countdown" shows the order
  and when #1 starts. Automation ON 1/day, waiting on countdown #5 (5/10 segments; Studio busy with LongForm).
  Frontend type-checked; not visually verified on a phone.

### Log — 2026-10-14 — Posts To defaults to Cinama; Charon voice everywhere (bff9cfb, dbacf4d, 98e87e5)
- Cause: Drive Shorts were imported with no accounts; LongForm/Countdowns/compilations hard-coded "default:*"
  (profile gone; connected = Cinama yt/fb/ig, mk blocked). queue_manager.default_destination()/fill_destinations():
  connected non-blocked profiles ("Cinama:*"), used at creation + every scheduler tick. VERIFIED live: 659 ready
  rows, 0 on "default", 0 empty; sweep fixed 1 row at 23:44 UTC.
- Voice: Voice Studio, hook voice-overs, TTS check default to Charon + "energetic, fast-paced YouTube recap host"
  (were Puck). Live /api/tts default_voice = Charon; narration = gemini-3.8-flash-tts · Charon.
- Countdown #1 re-rendered (16.48 min): no 3 s gaps left (~1 s breaths), -13.9 LUFS, intro verified visually
  (no hook text, crimson held logo + title, "RELEASED 2002"). Hook→intro trimmed further in dbacf4d (not re-rendered).

### Log — 2026-10-14 — LongForm freshness rule confirmed (owner)
- Owner: no content from before Oct 2026, BUT a show with a new season airing now is acceptable.
  candidates.check already does this (movies: US release current month → ≤15 d ago / ≤90 d ahead; TV: an
  episode this month). Audit of all 40 LongForm projects: none breaks it (Ted Lasso = new episode 2026-10-06).
- Gap offered, not built: manual starts (POST /api/studio/projects) skip the release check.

### Log — 2026-10-14 — Countdown intro rework (ca8d875)
- Hook: "COUNTDOWN" + title overlay removed for good; length = its line + 0.4 s (was min 12 s, ~20 s);
  AI hook_script now max 20 words; ~1.4 s per picture. Countdown #1 hook shortened via PUT script
  (undo recorded): "Think you are immune to fear? … caused real mass hysteria." (~7 s).
- Screen Central end: poster-filled letters hold, title fades in under them (flat red plate removed).
  sting.PALETTES crimson (default) / noir_gold / black_red / steel; previews sent to owner, awaiting pick.
- Countdown #1 re-render is paused on the $2.00 daily cap (spent $2.03); resumes after 00:00 ET, or sooner if
  the owner adds extra budget today. Not visually verified in a full render yet.

### Log — 2026-10-14 — Catch-up posting, staggered times, countdown dead-air fix (486ea83, 15f5559)
- Schedule (live config, verified via GET /api/schedule), ET: Shorts YT 09:00/17:00, FB+IG 08:00/11:00/15:00/19:00;
  LongForm YT 15:00/20:00, FB+IG 09:30/13:30/17:00/21:00; Countdowns 12:00 all three. No two queues share a time.
- Catch-up (486ea83): QueueItem.catchup JSON {slots, done}. plan_schedule is platform-aware when a queue has
  per-platform times (fits_slot): a slot only takes a video that still owes one of its platforms. apply_result:
  skipped platforms → back to Ready with accounts = skipped, links kept; archives when every platform is done.
  Used slots remembered (slot_used / posted_today) so a slot is never handed out twice. Startup backfill
  re-queues archived rows that had skipped platforms (today: #514, #734, #678, #736 and any since 10-13).
  NOTE: YouTube has 2 slots/day per queue vs 4 on FB/IG, so the YouTube catch-up line grows ~2/day per queue;
  videos reach YouTube in order, later and later. Owner decision pending: cap the wait or accept.
  Tests 296 passed; planning 650 items ≈0.15 s.
- Countdown #1 review (downloaded the 16.6 min render): loudness even (≈-14 LUFS) but dead air: 3 s at every
  #N card (silent 2.5 s bumper + tail) ×15, 3 s silent during the sting's letter zoom, ~2 s after the hook.
  15f5559: voiced #N cards ("Number N. Title." Gemini Charon over a whoosh), riser under the sting letters,
  hook tail 0.4 s, badge "RELEASED <year>" for past years. Re-render of #1 started after deploy.
- Not done (owner's call): re-voice the 15 entries in Gemini Charon (they're Chirp Charon; ~1–2 h Studio time,
  ~$0.45); entries carry a "TRAILER BREAKDOWN" tag from the shared LongForm segment render.

### Log — 2026-10-14 — Correction: Shorts 2/day on YouTube only (owner)
- Owner meant YouTube only. Restored FB/IG Shorts to 08:00, 12:40, 17:20, 22:00 ET (and posts_per_day 4,
  hours 9–20 as before); YouTube Shorts stays 09:00 + 16:20. Verified via GET /api/schedule. No code change.

### Log — 2026-10-14 — Shorts (Movie Clips) posting cut to 2/day (owner)
- Live config via PUT /api/schedule/config (undo snapshot taken by the route). Movie Clips: YT 09:00 + 16:20,
  FB/IG 08:00 + 17:20 ET (kept the 1st and 3rd existing times). LongForm + Countdowns unchanged. Verified in
  GET /api/schedule: Movie Clips 4 slots/day (2 YT + 2 FB/IG times), next 16:20 ET today. No code change.

### Log — 2026-10-14 — Gemini TTS verified live
- 04:06 UTC: real interactions call (gemini-3.8-flash-tts, Charon, recap-host style) → 5.89 s WAV. VERIFIED.
- Countdown #1 (Top 15 horror) render resumed after the cap reset and finished 04:04 UTC: 16.58 min,
  intro/outro voiced via studio/tts.py (no silent fallback exists, so the narration path worked). Awaiting
  owner watch + thumbnail + Send. Segments 1–15 still Chirp voice unless step 5 is re-run (owner choice).

### Log — 2026-10-14 — Gemini TTS: interactions is primary again (Gemini API guide)
- Owner pasted the Gemini API (API-key) speech-generation guide: v1beta/interactions IS the documented call
  for gemini-3.8-flash-tts; the earlier "Interactions not supported" note was the Vertex/Enterprise page.
- studio/tts.py: interactions first (owner's original shape), generateContent kept only as fallback;
  audio = the LAST steps[].content[] audio block, per the guide. Test made robust to a background thread
  sharing the faked httpx.post. 292 passed (x2). Live call still unverified until the 04:00 UTC cap reset.

### Log — 2026-10-14 — Gemini TTS: generateContent first (owner pasted Google's model doc)
- Doc: gemini-3.8-flash-tts → Interactions API "Not supported"; generateContent supported (Vertex example).
- studio/tts.py now calls `v1beta/models/gemini-3.8-flash-tts:generateContent` with the API key (header):
  parts[].speechMetadata.style + generationConfig.responseModalities=[AUDIO] + speechConfig.voiceConfig.voice=Charon;
  reads candidates[0].content.parts[].inlineData.data. Falls back to the interactions form on a non-auth error;
  remembers whichever works. Auth/quota errors (401/403/429) stop without trying the other.
- Tests: 292 passed. NOT yet verified live (daily cap until 04:00 UTC). If the API-key endpoint rejects the
  model, the only documented path is Vertex (needs GCP project id + service account in env) — owner decision.
- Voice Studio (social/tts.py, /api/tts/generate) still uses interactions only — untouched (surgical); same doc applies.

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
- [DONE 2026-10-17 a3fce00] Posting failed: "Instagram description is too long (3723). Maximum 2200" (#749, the
  horror countdown). Fix in upload_post.upload_video for every post: per-platform caption limits — YouTube keeps the
  full text (≤5000, no < >, tags ≤500 chars) in youtube_description, Facebook full in facebook_description, the
  shared description (what Instagram uses) fitted to 2200 chars / 30 hashtags (intro + hashtags, no timestamps).
  Then put #749 back in Ready for the next Countdowns slot.
- [DONE 2026-10-14d ecf7792; topic rebuild scheduled 04:10 UTC 10-15] Countdown topics must follow the Top 5/10/15 buttons. Cause: topic wording fixed
  ("Top 10 …"), AI gives ~10 titles ("up to 15") so only 1/15 topics can fill Top 15, and the buttons only set the
  box. Fix: ask for 18 real titles per topic; buttons filter the list to topics that can fill that size and reword
  "Top N" to match; best_format clamped to what the titles can fill; rebuild the list once.
- [DONE 2026-10-14c bff9cfb/dbacf4d] Queued videos must get the connected channels automatically. Causes: Drive-imported
  Shorts are created with no accounts; LongForm/Countdowns/compilations hard-code profile "default:*", which no
  longer exists (connected: Cinama yt/fb/ig; mk blocked). Fix: default destination = every connected, non-blocked
  Upload-Post profile ("Cinama:*"), cached; used at creation and by a scheduler sweep that fills rows with no
  accounts or only disconnected profiles. Also trim the 1.2 s gap between hook and sting.
- [DONE 2026-10-14b ca8d875, palette pick pending] Countdown intro: 1) remove the "COUNTDOWN" + title text from the
  hook — never on screen again; 2) end of the Screen Central intro: hold the textured SCREEN CENTRAL letters and
  add the subtitle under them, with better colours (show owner options before building); 3) hook ~20 s → short
  (~6–8 s) so the channel intro comes sooner.
- [DONE 2026-10-14, see log] 1) Per-platform CATCH-UP: a video skipped on a platform (slot belongs to other
  platforms) waits for that platform's next slot instead of never going there. Slot planning becomes
  platform-aware (a slot only takes a video that still owes one of its platforms); rows/files are kept until
  every platform is done. 2) New staggered times (ET): FB/IG Shorts 8:00/11:00/15:00/19:00, LongForm
  9:30/13:30/17:00/21:00, Countdowns 12:00; YT Shorts 9:00/17:00, LongForm 15:00/20:00, Countdowns 12:00.
  3) Countdown #1: check + fix audio and the Screen Central intro, re-render for owner review (re-voice the
  15 segments in Gemini Charon so the whole video uses one voice).
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

## Log — 2026-10-06 — Handbook republished as v4
- https://claude.ai/artifact/SF2MvUyAwnXo6rcEnXikwP (version 4): per-queue ⏰ Schedule, 📌 pin / ⏫ Post next, thumbnails in Edit, mk blocked, LongForm send → Ready, auto-archive of posted, shot rhythm, disk guard at 80%, update banner, ACCESS_TOKEN / 429 notes, new troubleshooting rows.
- Docs only; no code changed.

## Log — 2026-10-07 — Archive sweep stops retrying Drive files the robot can't delete
- Cause: 21 posted Shorts past the 5-day window had Drive files owned by the owner's account; trashing them returned 403 insufficientFilePermissions, the row was kept, and the sweep retried all 21 every 10 min (log spam).
- Owner chose: on insufficientFilePermissions, delete the row, log one `archive_drive_kept` warning with the Drive link, leave the file for the owner to delete. Other Drive errors still keep the row and retry.
- Test added; 231 pass. Not yet verified live (needs a token to read logs): expect 21 `archive_drive_kept` lines on the next sweep and no more `archive_trash_failed` for these items.

## PLAN — 2026-10-08 — Top 5 & Top 10 Recommendations & Countdown Lists (8–12 min Compilations)
Strategic pivot from single-movie 20-min recaps to 8–12 minute curated "Top 5 / Top 10" recommendations and countdown lists (movies & TV shows released, upcoming, or of all time).

### Why this beats single-movie recaps:
1. **Unlocks YouTube Mid-Roll Monetization (≥8 Minutes)**:
   - Top 5 (5 titles × ~1.5–2 min) = **8–10 minutes total**.
   - Top 10 (10 titles × ~1–1.2 min) = **10–12 minutes total**.
   - Yields the high RPM of long-form videos while retaining high viewer completion rates.
2. **100% Reuses the Existing Trailer Studio Formula**:
   - Zero full movie rips or spoiler searches needed; utilizes verified official promotional materials (IMDb 1080p MP4 / YouTube clips).
   - Same battle-tested 1080p 30fps canvas, adaptive shot tagging, moving clip rhythm, release/streaming badges, and Chirp 3 HD / Gemini TTS voiceover.
   - Minimal copyright risk (Fair Use recommendations and commentary on public promotional materials).
3. **High Search Intent & Evergreen Traffic**:
   - Formats like *"Top 10 Upcoming Sci-Fi Movies in 2026"*, *"Top 5 Mind-Bending Thrillers You Missed"*, *"Top 10 Best Netflix Releases of the Month"*.

### Video Architecture:
- **Intro Hook (15–25s)**: Rapid montage teaser of featured titles + vocal hook teasing the #1 entry.
- **Ranked Countdown Items (#5–#1 or #10–#1)**:
  - Graphic countdown bumper badge (e.g. `#5: TITLE`, release date, streaming service).
  - Opening moving clip hook (0:00–0:04).
  - Voiceover narration: premise, recommendation hook, cast highlights, critical buzz.
  - Visual rhythm: alternating moving trailer clips, high-res stills, and official poster frame.
- **Outro & CTA (15–20s)**: Discussion prompt ("Which one are you watching first?"), channel subscribe lower-third, and YouTube end-screen.

## Log — 2026-10-10 — Built: Top 5 & Top 10 Countdown Compilations (8–12 Min)
- **Countdown Transition Bumpers (`backend/app/studio/compilations/bumper.py`)**:
  - Generates 1920×1080 30fps transition bumpers (#10 down to #1) with Screen Central red/white badges, bold typography, entry titles, and release/platform subtitle badges.
  - Matches the exact ffmpeg timescale (`-video_track_timescale 30000`) and pixel format (`yuv420p`, tv range) of Studio renders for glitch-free concat.
- **Thematic Curation & Discovery (`backend/app/studio/compilations/curation.py`)**:
  - 6 preset themes: Upcoming Sci-Fi (2026), Mind-Bending Thrillers, Horror Chills, Action Blockbusters, Netflix Hits, and Binge-Worthy TV.
  - Queries TMDB Discover and external IMDb IDs; maintains permanent deduplication ledger (`app_settings.compilations_made`).
- **Master Countdown Stitching Engine (`backend/app/studio/compilations/stitch.py`)**:
  - Synthesizes Intro hook video (15–22s) with TTS narration teasing the countdown and #1 pick.
  - Inserts countdown graphic bumpers before each breakdown segment.
  - Synthesizes Outro bookend (14–20s) with community discussion prompt and subscribe CTA.
  - Concat demuxer joins all segments; normalizes master audio to YouTube broadcast standard -14 LUFS (`loudnorm`).
  - Automatically calculates exact chapter timestamps (`0:00 Intro`, `0:22 #5 ...`) and writes timestamps into video description.
  - Generates 3 clickable 1280×720 JPEG thumbnail options (Poster Collage, Action Still with #1 badge, Split Contrast).
  - Pushes to Posting Queue under `LongForm` pipeline, status `ready`, target `default:*` (Screen Central).
- **FastAPI Endpoints (`backend/app/studio/compilations/routes.py`)**:
  - `GET /api/studio/compilations/themes`
  - `POST /api/studio/compilations/curate`
  - `GET /api/studio/compilations/eligible-projects`
  - `POST /api/studio/compilations/preview-chapters`
  - `POST /api/studio/compilations/stitch`
  - Mounted under `/api/studio/compilations/` in `backend/app/studio/routes.py`.
- **Stremio Cinemeta & FMHY Integration (`cinemeta.py`, `fmhy.py`, `CountdownModal.tsx`)**:
  - `cinemeta.py`: Keyless, zero-credential client querying Stremio's Cinemeta API (`v3-cinemeta.strem.io`) for trending movie & series catalogs, IMDb ratings, cast, director, and YouTube trailer IDs with redirect following.
  - Automatic fallback in `curation.py`: If TMDB API is ever unconfigured or rate-limited, title curation automatically falls back to Stremio Cinemeta's top catalog.
  - `fmhy.py`: Indexes verified FreeMediaHeckYeah directories for movie/show discovery (Trakt, Letterboxd, JustWatch), royalty-free audio/SFX libraries (FMA, Incompetech, Freesound), and scrapers (Cobalt, yt-dlp, NewPipe).
  - API endpoints: `GET /api/studio/compilations/cinemeta/top`, `GET /api/studio/compilations/cinemeta/meta`, `GET /api/studio/compilations/fmhy/resources`.
- **Frontend UI Integration (`CountdownModal.tsx`, `StudioPanel.tsx`, `StudioAuto.tsx`, `page.tsx`)**:
  - `CountdownModal.tsx`: Interactive countdown builder with reordering controls, estimated runtime gauge, YouTube mid-roll monetization eligibility indicator, YouTube chapters preview, batch pickers ("Pick Top 5", "Pick Top 10", "+ Add All"), and tabs for Cinemeta Catalog and FMHY Resources.
  - `StudioPanel.tsx`: Added dedicated `CountdownStudioCard` prominently placed above the video list with live count of rendered breakdowns ready to stitch and one-click launch.
  - `page.tsx`: Compilations sidebar tab now features a sub-tab toggle between `🎬 LongForm Countdowns (Top 5 / Top 10)` and `📱 Short-Form Vertical Clips`.

## Log — 2026-10-10 — Official Poster Thumbnail Default & Release Momentum System Tracker

- **Default Thumbnail Choice (`backend/app/studio/stage_render.py`)**:
  - Default thumbnail changed from `👤 Lead Close-Up` (`shot1`) to `🪧 Official Poster` (`poster`).
  - Active file `render/thumbnail.jpg` defaults to `render/thumbnail_poster.jpg` on all renders and re-renders; `selected_thumbnail` defaults to `"poster"`.
  - Manual option switching between Poster, Lead Close-Up, and Scene Still remains fully available in both Studio and Queue.
- **Release Momentum System Tracker (`backend/app/studio/momentum.py`)**:
  - Purpose: Ensures movie and TV breakdowns are posted **before** theatrical/streaming release dates to ride peak pre-release search momentum.
  - Categorization:
    - 🚨 `Critical / Urgent` (0–3 days): Imminent premiere, peak search buzz window.
    - ⚡ `Approaching / Prime` (4–14 days): Ideal trailer breakdown window.
    - 🗓️ `Upcoming` (>14 days): Early pre-release planning.
    - ⚠️ `Released / Missed` (<0 days): Title already opened; pre-release momentum passed.
  - Checks: Validates whether `scheduled_at` falls on or after the movie release date (`scheduled_after_release: true`).
- **Automated Posting Scheduler (`backend/app/social/queue_manager.py`)**:
  - In `plan_schedule()`, `LongForm` items (and items with release dates) are prioritized by release urgency (`days_until_release` ascending).
  - Movies releasing soonest are allocated earlier posting slots so they publish before their release dates.
  - Already-released titles or dateless videos do not take earlier slots from upcoming premieres.
  - Pinned posting times (`pinned_at`) remain strictly respected.
- **Discovery Candidate Pool (`backend/app/studio/candidates.py`)**:
  - `RELEASE_BEHIND` updated from `30` to `0` days.
  - Breakdown candidate checks now require `0 <= days <= RELEASE_AHEAD` (unreleased titles only), rejecting already-opened films to keep channel content pre-release focused.
- **Frontend Badges & Alerts (`MomentumBadge.tsx`, `StudioPanel.tsx`, `StudioAuto.tsx`, `QueuePanel.tsx`)**:
  - `MomentumBadge.tsx`: Displays colored status pill with countdown days and release date.
  - `StudioPanel.tsx`: Adds badges to every project card in the list and in the project header.
  - `StudioAuto.tsx`: Adds dedicated `🚨 Urgent (≤3d)` and `⚡ Pre-Release` filter pills with counts and priority sorting.
  - `QueuePanel.tsx`: Displays momentum badges in the Title column; displays an eye-catching warning box with a 1-click `⚡ Post Next` button if an item is scheduled after its release date.

## Log — 2026-10-10 — Top 5 & Top 10 Countdown Compilations Research Engine & Studio Tab

- **Autonomous Content Strategist & List Engine (`backend/app/studio/compilations/engine.py`)**:
  - Implemented the full 5-step agentic countdown methodology:
    - **STEP 1 — Topic Discovery:** Scans market context from TMDB upcoming calendar and Cinemeta top titles. Scores 15 candidates across 5 demand triggers: `TRENDING`, `CALENDAR`, `STREAMING`, `DEBATE`, `EVERGREEN` using formula: Demand (0.30) + Momentum (0.25) + Debate (0.20) + Gap (0.15) + Visuals (0.10).
    - **STEP 2 — Ranking Logic:** Computes defensible composite scores normalized 0–100 (Ratings 35%, Cultural Impact 25%, Current Buzz 20%, Fan Sentiment 20%). Minimum vote count prevents obscure outliers; deduplicates franchise entries.
    - **STEP 3 — Entertainment Ordering:** Scripts for high watch-time and comment retention: #1 teased in opening hook without spoilers; surprise/controversial pick at #3–#4; strong opener at lowest rank; 2–3 honorable mentions right before #1; closing debate question drives comment section debate.
    - **STEP 4 — Fact Rules & Verification:** Pulls official US streaming availability via TMDB JustWatch (`watch/providers`), TMDB ratings, box office, awards; flags unverified claims in `unverified_flags`.
    - **STEP 5 — Standardized Output:** Generates structured JSON with 3 clickable titles (<60 chars), 3–4 word thumbnail text, 10s retention hook script, ranked entries (#10 to #1), honorable mentions, and closing question.
- **Dedicated Countdown Studio Panel & Navigation (`frontend/app/CountdownsStudioPanel.tsx`, `frontend/app/StudioPanel.tsx`, `frontend/app/page.tsx`)**:
  - Main application sidebar navigation features a dedicated **"🏆 Countdowns Studio"** item.
  - Three integrated views:
    - `🎯 Topic Discovery & Scoring`: 5-trigger filter pills, 15 scored candidates with 5-factor matrix, and on-demand custom topic research bar (Top 5 / Top 10 / Top 15 formats).
    - `📊 Researched Countdown Viewer`: Interactive countdown viewer with clickable title options, thumbnail badge, 10s retention hook script, #10 to #1 entry cards with high-res posters, JustWatch US streaming links, defensible ranking breakdown, local footage matching status, honorable mentions, closing debate question, and fact verification status.
    - `📁 Saved Countdowns`: Dedicated library to save, view, or manage researched countdowns.
  - 1-click video stitching button (`🎬 Stitch 8–12m Video`) opens `CountdownModal` to assemble trailer breakdowns into monetizable compilations.

## Log — 2026-10-10 — LongForm Studio vs Countdowns Studio Separation & Release Window Guardrail

- **Studio Separation (`frontend/app/StudioPanel.tsx`, `frontend/app/page.tsx`)**:
  - `LongForm Studio` and `Countdowns Studio` are completely separate standalone studios:
    - **LongForm Studio (`StudioPanel.tsx`)**: 100% focused on trailer breakdowns (research → footage → shots → script → voice → video → Posting Queue). All countdown sub-tabs, countdown compilation cards, and countdown modal triggers removed from `StudioPanel.tsx`.
    - **Countdowns Studio (`CountdownsStudioPanel.tsx`)**: Directly rendered from the sidebar navigation tab `activeTab === "countdowns"` as well as `compTab === "longform"`.
- **15-Day & Current-Month-Forward Release Timing Rule (`backend/app/studio/candidates.py`, `backend/app/studio/routes.py`)**:
  - From now on, trailer breakdowns are strictly titles from the current month forward (October 2026+) and at most 15 days ago:
    - Enforced in `candidates.py`: `check()` validates `rel_date >= datetime.date(today.year, today.month, 1)` and `days >= -15` (and `days <= 90`). Rejects older releases with clear diagnostics (`"released ... before current month (October 2026)"` or `"released X days ago (>15 days cutoff)"`).
    - Enforced in `routes.py`: `list_projects()` automatically filters out any existing project in the database released before the current month start or >15 days ago.
    - Pruned 20 stale projects from live Railway production database (from 1999, 2021, 2022, 2024, and pre-October September 2026).
- **Handbook & Operator Docs (`RUNBOOK.md`, `CONFIG.md`, MEMORY.md)**:
  - Updated `CONFIG.md` release threshold documentation.
  - Added "LongForm Studio vs Countdowns Studio (2026-10-10)" and "Release Timing & Momentum Rules" to `RUNBOOK.md`.

## Log — 2026-10-10 — Countdown Studio Candidate Caching & Persistent Researched Items

- **Candidate Topic Caching (`backend/app/studio/compilations/engine.py`, `backend/app/studio/compilations/routes.py`)**:
  - Implemented 72-hour database caching via `AppSetting("countdown_candidates")` in `get_or_discover_topics()`.
  - Fixes repeated `"Scoring candidate topics across market signals..."` loading screen on every visit to Countdown Studio: cached topics are now returned in milliseconds.
  - Added `refresh=true` support on `GET /api/studio/compilations/topics` wired to the "Refresh Demand Signals" button in the UI.
- **Instant Search Item Persistence & Discovery Cards (`frontend/app/CountdownsStudioPanel.tsx`, `backend/app/studio/compilations/routes.py`)**:
  - `POST /api/studio/compilations/research` now automatically commits every generated countdown to `saved_countdowns` so searched topics are never lost when navigating between tabs.
  - Added a dedicated **"🕒 Searched & Researched Countdowns"** card section to the Discovery view right under the on-demand search bar with 1-click access to previously researched scripts, rankings, and streaming links.
  - Persisted the last-viewed countdown ID in `localStorage` so returning to Countdown Studio immediately restores the user's active research.

## Log — 2026-10-10 — 1-Click Auto-Queue Researched Countdowns for Video Creation

- **Problem:**
  - When viewing a researched countdown (e.g. *Top 15 Scariest Horror Movies of All Time*), entries previously displayed `"No local breakdown rendered yet"`.
  - Clicking `🎬 Stitch Video (8–12m)` previously only opened a modal to stitch *already rendered local Studio projects*, blocking the user from turning new researched lists into videos.
- **Solution — End-to-End Autonomous Auto-Queue Pipeline:**
  - **Backend Engine (`backend/app/studio/compilations/engine.py:auto_queue_researched_countdown`)**:
    - Takes any researched countdown (Top 5, Top 10, or Top 15).
    - Checks each ranked title against existing `StudioProject` records (by TMDB ID or title match).
    - If a project does not exist, automatically searches TMDB and creates a new `StudioProject` populated with the title, year, poster, rankings, and fun facts, then triggers the studio runner (`gather` -> `plan`).
    - Assembles the complete 8–12m YouTube Master Package:
      - First 10-second retention hook script
      - Timed chapters from 0:00 Intro & Preview through each countdown slot (#15 to #1) to Outro
      - Ranked entries with 0-100 scores and JustWatch US streaming links
      - Closing debate question to drive viewer comment interaction
    - Inserts a new `QueueItem` into the Posting Queue under pipeline `"LongForm"` and source `"Countdown Studio"` with status `"ready"` and target account `default:*` (Screen Central).
    - Links the created projects to the countdown entries and auto-saves the countdown in `saved_countdowns`.
  - **API Route (`backend/app/studio/compilations/routes.py:POST /api/studio/compilations/auto-queue`)**:
    - Exposes `POST /api/studio/compilations/auto-queue` with `AutoQueueCountdownRequest`.
  - **Frontend Integration (`frontend/app/CountdownsStudioPanel.tsx`, `frontend/lib/api.ts`)**:
    - **Header & Action Buttons:** Both top banner `🎬 Stitch Video (8–12m)` and the Researched Countdown action button `🎬 Stitch Video (8–12m)` now automatically trigger `handleAutoQueueCountdown()`, confirming the operation and auto-queuing all entries.
    - **Live Status Badges on Entry Cards:**
      - If rendered: `✓ Local 1080p Breakdown Footage Ready (Ns)`
      - If in pipeline: `⏳ Studio Breakdown #ID (status)` + `▶ Run` button
      - If missing: `No local breakdown rendered yet` + `+ Create Breakdown Project` button
    - **Saved Countdowns View:** Added `🎬 Auto-Queue Video` button directly on saved countdown cards for instant queuing.
  - **Verification:**
    - `npx tsc --noEmit` passed cleanly.
    - Added and passed unit test `backend/tests/test_studio_auto_queue.py` verifying full QueueItem assembly, chapters, and project generation.

## Log — 2026-10-11 — Studio Segregation, Compilations-Only Output & Dedicated Countdown Player

- **Architectural Separation: LongForm Studio vs Countdown Studio:**
  - **LongForm Studio = Single-Title Factory (2–4m):** Strictly limited to upcoming & current-month-forward single trailer breakdowns (*Wildwood*, *Whalefall*, etc.).
  - **Countdown Studio = Macro Curator & Master Compilation Director (8–12m):** Discovers high-demand topics, ranks 5–15 titles from composite data (TMDB, Letterboxd, IMDb, JustWatch), scripts retention hooks and debate prompts, and directs compilation assembly.
- **Strict Studio Segregation & No Clutter:**
  - **Countdown Projects Excluded from LongForm Studio (`backend/app/studio/routes.py:list_projects`)**: Whenever projects are created for a countdown (e.g. catalog classics like *The Ring*, *The Descent*, *Alien*), they are tagged with `is_countdown=True` and `origin="countdown"`. `list_projects` automatically filters them out so LongForm Studio never gets flooded with catalog movies from past years.
  - **Compilations-Only Publishing (No Standalone Single Video Posts):** Countdown segment projects are created strictly as building blocks for the compilation (`queue_item_id=None`). They are never queued or published as individual videos. Only the master 8–12 minute compilation is placed in the Posting Queue.
- **Dedicated "Countdown Compilations" Shelf & Video Player (`frontend/app/CountdownsStudioPanel.tsx`)**:
  - Added 4th sub-tab: **`🎬 Countdown Compilations`** (joining `Topic Discovery`, `Researched Countdown`, and `Saved Countdowns`).
  - Lists all master countdown compilations (both completed MP4 renders and in-progress/queued compilations).
  - Built-in **HTML5 Video Player** with direct streaming via `mediaUrl(/api/studio/compilations/{id}/file?path=final_compilation.mp4)` or the queue video route.
  - Interactive timed chapters list (#15 down to #1, Intro, Outro) with 1-click clipboard copy for YouTube timestamps.
  - Retention strategy & packaging inspector (YouTube titles, 10-second retention hook script, and closing debate prompt).
  - Countdown segments inspector displaying real-time render status (`✓ Rendered` or `⏳ In Progress`) for every ranked title with individual `▶ Run` buttons.
- **Backend API Endpoints (`backend/app/studio/compilations/routes.py`)**:
  - `GET /api/studio/compilations/list`: Returns all compilations (SocialPilot queue items + on-disk renders with metadata).
  - `GET /api/studio/compilations/projects`: Lists countdown-specific segment projects (isolated from LongForm Studio).
  - `GET /api/studio/compilations/{compilation_id}/file`: Streams compilation video / thumbnail files using `FileResponse`.
  - `POST /api/studio/compilations/auto-queue`: 1-click bridge that creates/starts missing segment projects and queues the master compilation.
- **Verification & Testing:**
  - Frontend typecheck passed cleanly with zero errors (`npx tsc --noEmit`).
  - Unit test passed (`backend/tests/test_studio_auto_queue.py`).
  - Pushed to `origin/main` (commits `996b0da`, `8df26c1`, `590ee1a`, `7f83c4e`).


## PLAN — 2026-10-11 — Deep audit: unfinished routes, dead ends, broken connections (owner request)
Owner: "a lot of updates and breaks… routes and other things that aren't completely finished or not done with future connections in mind… do a deep dive and implement fixes after logging the plan, and update the handbook after."
1. Baseline: backend tests, frontend typecheck + production build, live /api/health and Vercel deploy state.
2. Review everything since 77d6796 (Countdowns Studio, momentum, poster default, release-window filter, auto-queue, compilations shelf) line by line: routes the UI calls that don't exist (and vice versa), schema changes without migrations, queue rows that could post a video that doesn't exist yet, filters that hide data, missing auth, secrets.
3. Cross-check every `api.*` call in frontend/lib/api.ts against the backend's real routes (method + path + payload).
4. Fix in small commits with tests; push after each checkpoint; verify live (Railway + Vercel).
5. Log findings + fixes here, then update the handbook (v5) to match what actually works.

## FINDINGS + FIX PLAN — 2026-10-11 — Deep audit (live data checked read-only with the owner's token)
Baseline: 247 pass / 1 fail (test_list_has_checks_reasons_and_sentiment — wording changed by the release-window rule); tsc clean; live frontend = 590ee1a (last frontend commit).
Broken / dead ends found:
1. **Countdown rows posted with no video.** auto-queue made queue rows #745–747 `ready` with no file (746/747 duplicates); first was due 2026-10-12 15:00 UTC. → DONE live: moved to Needs Review.
2. **A countdown never becomes a video.** Segment projects run only to `plan` (and only if Studio is idle — runner.start, not start_or_queue); nothing renders them, nothing stitches, nothing attaches the result to the queue row.
3. **Non-titles become projects.** "Spider-Man 4 theories" entries (no TMDB match) became movie projects #65–69 with tmdb_id 0.
4. **Countdown projects eat the daily breakdown limit.** made_today counts every project → today's automation stopped ("today's limits reached") after the 5 theory projects.
5. **Countdown tag is lost.** gather rewrites `facts`, dropping is_countdown/origin → projects #45–59 (horror Top 15) are hidden from BOTH studios.
6. **Title match steals projects.** `ilike %title%` + tagging can pull a real LongForm breakdown into Countdowns (hidden from LongForm).
7. **LongForm list hides live work.** The "current month / ≤15 days" filter hides projects still in progress (e.g. #60 CIA, #61 at plan) — and on the 1st of every month hides everything from last month.
8. **Momentum sort overrides ⏫ Post next** for LongForm (sort ignores the owner's choice).
9. **Security:** /api/studio/compilations/{id}/file has no path check (can read any file on the volume with a token).
10. **/compilations/stitch runs an 8–12 min render inside the HTTP request** (times out, blocks a worker).
11. **Dead route:** Countdown player falls back to /api/queue/{id}/video, which doesn't exist.
Fix plan (small commits, tests, push each):
A. Queue guard: never schedule/post a row with no video (no video_path/drive_link/media_url); approve/bulk-Ready refuse it.
B. Countdown membership = ids in Countdown Studio rows' research.project_ids (+ tag kept through gather) → used by LongForm list, Countdown list, daily limit.
C. auto-queue: real titles only (TMDB match by id/exact title), idempotent per countdown id, row status `review` ("in production"), segments start_or_queue through render; a tick stitches when every segment is rendered (background job), attaches the video + thumbnail, leaves it in Needs Review.
D. LongForm list: hide old titles only when nothing is in progress.
E. Post next wins over momentum order (post_next_at column).
F. File route: digits-only id, allowlisted file names, must stay inside the folder.
G. /stitch → background job; fix the player fallback route; fix the stale test.
H. Handbook v5.

## Log — 2026-10-11 — Deep audit fixes deployed (cc6bf0b → 7b65b19), handbook v5
- Live data fix (owner's token, read + 3 PATCHes): Countdown rows #745–747 were Ready with no video (first due 2026-10-12 15:00 UTC) → moved to Needs Review.
- cc6bf0b queue: a row with no video never gets a slot and can't be set Ready; stale release-window test fixed.
- 43352ef security: /api/studio/compilations/{id}/file serves only that compilation's video + thumbnails (was any path on the volume).
- 9a2cb5c studio: countdown membership via the Countdown rows' research.project_ids (tag also kept through gather) → #45–59 back in Countdowns Studio; countdown projects don't use the daily breakdown limit; LongForm list never hides work in progress.
- 04a6d8a queue: post_next_at column (auto-added on boot) — ⏫ Post next beats the release-date order.
- 49ceecb countdowns: auto-queue = real titles only, idempotent, Needs Review; production loop (one segment at a time when Studio is idle → background stitch → video attached, Needs Review); /stitch in background; ▶ Make video; saved countdowns keep their id (each save used to mint a new id + duplicate); player uses the real file route (/api/queue/{id}/video never existed).
- 7b65b19 countdowns: ▶ Run continues from the last finished step (stage "next"); no project for unmatched titles.
- Verified live: version.json = 49ceecb, new route answers; LongForm list 26 (incl. in-progress #60/#61), Countdown list 20 (#45–59, #65–69); automation count now 3 movies + 2 TV (was 8 movies incl. the 5 theory projects) — today's limit is genuinely reached.
- Tests 260 pass; tsc + next build clean.
- Handbook v5 published (same URL): Part 4 Countdowns, release-date order + badges, no-video guard, LongForm list rule, poster default thumbnail, new troubleshooting rows. RUNBOOK updated to match.
### Pending (owner decisions)
- #746/#747 (Spider-Man 4 "theories", duplicates) and their projects #65–69 (tmdb_id 0, never run) can't become videos — delete them? (not done: destructive).
- #745 (Top 15 horror, 15 real titles) is in Needs Review, not in production. ▶ Make video starts it: ~15 segments of AI spend under the daily cap, made only when Studio is idle.
- 20 stale projects were pruned on 2026-10-10 by the earlier session (before this audit) — not recoverable from here except from a DB backup.

## PLAN — 2026-10-12 — Countdowns Studio rebuilt to work like LongForm + its own posting tab + mobile (owner request)
Owner: "the scrapper hasn't been adapted for mobile… can the countdown studio keep what it's meant to do but have the whole set up and interface like LongForm. Make it its own thing and also give it its own posting tab… it doesn't act like the way LongForm works… 'Can't make this countdown: 400 … aren't real titles' … feels like a long winded paper weight."
Evaluation (why it feels broken): a countdown isn't a thing you can open — it's spread over saved-list JSON (AppSetting), a LongForm queue row and loose Studio projects; there is no project page, no steps, no progress, no review gate; research can rank non-titles and only fails at the end; it posts through the LongForm queue/schedule; the page overflows on a phone.
1. **Countdown = a project, like a breakdown.** New table `countdown_projects` (topic, format, stage/status, ranking, segments, script, render, queue_item_id, cost ref). Existing saved countdowns + queue rows #745–747 migrated into it on boot (nothing deleted).
2. **Same steps as LongForm, with a project page:** 1 Research (ranking) → 2 Titles (review the ranking: reorder, remove, swap; every entry must be a real TMDB title — non-titles are flagged at research time, not at the end) → 3 Segments (each title's breakdown, made one at a time when Studio is free, progress + ▶ Resume per segment) → 4 Script (hook, intro/outro lines, title options, closing question — editable) → 5 Render (stitch the 8–12 min video, chapters, thumbnails, preview) → Send to the Countdown queue. Same buttons as LongForm: Run / Re-run, ▶ Continue, ■ Stop, cost, Archive / Delete, ✓ Reviewed.
3. **Countdowns Studio page laid out like LongForm Studio:** "Start a new countdown" (topic ideas list with scores + custom topic + Top 5/10/15) and "Your countdowns" cards with filters (Needs review · Making · Rendered · In queue · Archived), the same card style, badges and batch buttons.
4. **Its own posting tab:** SocialPilot AI → **Countdowns** queue (pipeline `Countdowns`, separate from LongForm), with its own ⏰ Schedule (default 1/day), Needs Review → Ready → Posted Archive like the others. Rows never Ready without a video. Existing countdown rows move there.
5. **Mobile:** Countdowns Studio and its project page built with the same narrow-screen rules as LongForm (no sideways page scroll, wrapping tabs/buttons); then a 390 px pass over every tab in Chrome, verified on the live site.
6. Research engine asked for titles only; entries resolved against TMDB during research; unmatched ones flagged with a "swap" option instead of a dead end.
7. Tests for the new model/steps/queue; RUNBOOK + handbook (v6) updated.
Owner decision pending (not done without a yes): delete the two Spider-Man "theories" countdowns (#746/#747) and their 5 placeholder projects (#65–69).

## Log — 2026-10-12 — Countdowns Studio rebuilt like LongForm, own queue, mobile (8ae72d0, 1c32765, 075f6f5)
- Evaluation: the old Countdowns Studio wasn't a thing you could open (saved-list JSON + loose queue rows + loose projects), had no steps/progress/review gate, ranked non-titles and only failed at the end ("Make video" 400 on the Spider-Man theories list), posted through the LongForm queue, and overflowed on phones.
- Built: `countdown_projects` + `studio/countdown.py` (5 steps: research → titles → segments → script → render → Send to the new Countdowns queue); new page `CountdownsStudio.tsx` laid out like LongForm (Your countdowns cards/filters/highlights, Start a new countdown: your topic / topic ideas / from your breakdowns, project page with stepper, Video, Titles editor with ↑↓/Swap/✕/+Add, Segments, Script); SocialPilot → 🏆 Countdowns queue with its own schedule; segment scripts written as countdown entries.
- Removed the old auto-queue/stitch/production paths, CountdownModal, CountdownsStudioPanel, LongForm's Stitch Countdown button; Compilations tab back to short-form exports only.
- Verified: 260 tests; tsc + next build; local copy (test DB + generated test token on localhost) checked at 390 px in Chrome: Countdowns list/project page/titles/confirm flow and the Countdowns queue; a sweep of all 7 tabs found no sideways overflow. Live: version.json 8ae72d0, /api/countdowns answering.
- Migration ran live: horror Top 15 → #1 (its 15 segments attached, stopped — nothing spends until ▶ Make segments); Spider-Man theories → #2 (5 entries flagged, not makeable). It also made exact duplicates #3, #4, #5 (old saves had many ids) — migration fixed (075f6f5) but **deleting #3–#5 was blocked by the auto-mode classifier; left for the owner** (🗑 on each card).
### Pending (owner)
- Delete duplicates #3, #4, #5 in Countdowns Studio; decide on #2 (Spider-Man theories: swap in real titles or delete; its 5 placeholder projects #65–69 are hidden).
- #1 (Top 15 horror): ▶ Make segments to start (15 segments of AI spend under the daily cap).

## PLAN (for owner review — NOT approved yet) — 2026-10-13 — Countdowns Studio "exactly like LongForm"
Owner: "why am I getting '⏳ Making segments: 0 of 15 done — waiting for Studio to be free'… still not quite like LongForm Studio, buttons and automated generations are missing; I want it exactly like LongForm but creating videos for the countdowns… draw up the plan and recommendations and log it so I can review before approving."

### Why "waiting for Studio to be free" (checked live 2026-10-13 03:05 UTC)
- It isn't stuck: #1 (Top 15 horror) has 2 of 15 segments made (The Ring 138 s, The Descent 112 s), $0.24 spent, ~6 min of work per segment.
- Studio makes one thing at a time. The countdown only starts a segment on the 5-minute scheduler check, and only if nothing else is running or waiting — so it sits idle up to 5 min between segments, and waits behind any LongForm breakdown. The message doesn't say what it's waiting for.
- Found while checking: a Top 15 should have ~40 s segments, but these were created by the old auto-queue at 1.5 min, so this video would run ~30 min, not 8–12. Confirm only sets the length on new segments.

### What LongForm has that Countdowns lacks (gap list)
| LongForm | Countdowns today |
|---|---|
| 🤖 Automation card: on/off, daily limits, Next pick, ⏭ Skip next, ⏸ Pause today, last started, today's count, budget check | none — every countdown is started by hand |
| 🔥 Checked & ranked list: checks as chips (New, Trailer, Facts, Release, Interest), score, Make / + Add to line / Pin / Skip / Preview | plain "Topic ideas" cards, no checks, no pin/skip, no never-repeat |
| Studio's waiting line: "waiting in line", ⏫ Move up, starts the moment Studio is free | polls every 5 min, only when Studio is idle |
| Your videos: Select all shown, 🎞 Render selected, 📤 Send to queue (batch), ✓ Reviewed, ▶ Resume, ⏫ Move up, urgency filters, cost bars | single-card actions only |
| Project page: Run / Re-run / ↻ from here per step, ▶ Continue to step 5, Undo, target length | only one button per step, no Undo, no length control |
| Script editor with fact-check list; shot plan with swap | intro/outro text boxes; segments can't be opened or edited |
| Video: 3 thumbnails + 🔄 Refresh Options, motion graphics panel, Drive copy, archive/footage clean-up after posting | 3 fixed thumbnails, no Drive copy, no post-posting clean-up |
| Daily cap pause → resumes first; restart-safe jobs | research/render jobs aren't resumable |

### How it would work after the update
1. **🤖 Countdown automation card** (top of Countdowns Studio, same look as LongForm's): ON/OFF; daily limit (default **1 countdown started per day**, editable); Next pick + why; ⏭ Skip next; ⏸ Pause for today; last started; today's count; only starts when the whole countdown's estimated cost fits today's cap (~$0.12 per segment + research ≈ $0.70 Top 5, $1.30 Top 10, $1.90 Top 15).
   It runs **steps 1–3 by itself** (research → automatic title check → segments) and **stops before render**, card marked "👀 Ready for your review" — exactly like LongForm stops before render. Titles that aren't real movies/shows are replaced automatically from the honorable mentions (or the slot is dropped); if fewer than the format needs remain, it stops at step 2 for you.
2. **🔥 Checked & ranked topics** (replaces "Topic ideas"): top 15 topics, rebuilt every 3 days (free), each with chips — **New** (never made; never-repeat ledger), **Titles** (≥ N real titles found on TMDB), **Trailers** (IMDb trailers for most titles), **Demand**, **Timing** — a 0–100 score, and buttons **Make countdown**, **+ Add to line**, **👁 Preview titles**, **⭐ Pin**, **✕ Skip**. Search/your own topic stays.
3. **Real waiting line:** a countdown's segments go into Studio's line (after LongForm work, before nothing else is idle) and the next one starts the moment the previous finishes — no 5-minute gaps. Cards say exactly what they wait for ("Studio is rendering LongForm #46 — your segment is next"), with ⏫ Move up.
4. **Your countdowns = LongForm's Your videos:** filters (Needs review · Reviewed · Rendered · In queue · Archived), Select all shown, 🎞 Render selected, 📤 Send to queue, 🗄 Archive, 🗑 Delete, ✓ Reviewed, ▶ Resume, ⏫ Move up, cost per countdown, amber/blue highlights.
5. **Project page = LongForm's:** per-step Run / Re-run / ↻ from here; ▶ Continue to step 3 (stops before render); ■ Stop; ↶ Undo (titles, script); Delete; **segment length** picker (default by format: Top 5 1.6 min, Top 10 0.9, Top 15 0.6 — applied to every segment it made, never to a reused LongForm breakdown).
6. **Segments are editable:** each segment opens the same editor as a LongForm breakdown (script with fact-check list, shot plan with swap, re-render) and "← back to the countdown".
7. **Script step:** title options, intro hook, closing question, plus 🔊 voice preview; chapter times shown.
8. **Video step = LongForm's Video section:** player, 3 thumbnails + 🔄 Refresh Options, motion-graphics on/off, Send to Countdowns queue, ☁️ Drive copy to a "Countdowns" folder in the LongForm Shared Drive, archive/free-space rules after posting.
9. **Restart-safe:** research and render resume after a deploy; daily-cap pauses resume first (same as LongForm).
10. **Fix now (part of this):** set existing countdown-made segments to the format's length, so #1 comes out ~10–12 min (the 2 made at 1.5 min would need a re-run: ~$0.25), and the "waiting" message names the reason.

### Build order (each its own commit + test + live check, mobile-checked at 390 px)
A. Segment length fix + clear waiting reason + real waiting line (items 3, 10). B. Project page parity (5, 6, 7, 9). C. Your countdowns batch actions (4). D. Checked & ranked topics + never-repeat (2). E. Automation card (1). F. Video/Drive/archive parity (8). G. Handbook v7 + RUNBOOK.

### Decisions for the owner
1. Automation stops **before render** (recommended, like LongForm) — or after step 2 (titles) so nothing is spent on segments until you've seen the list?
2. Daily limit: **1 countdown a day** (recommended; ≈ $1–2/day) — or 1 every 2–3 days?
3. Should countdown segments wait behind LongForm (recommended, today's behaviour) or alternate with it?
4. #1 Top 15 horror: re-run The Ring and The Descent at the shorter length (~$0.25) so the video is ~10–12 min — or keep them long (≈ 30 min video)?
5. Drive copies of countdowns: yes (recommended) / no.

## PLAN v2 (for owner review — NOT approved yet) — 2026-10-13 — Countdowns Studio = LongForm Studio, for countdowns (+ vidIQ)
Supersedes the earlier 2026-10-13 plan. Owner: "it should also auto generate the videos and await a user to approve the render just like how longform works. also the topic ideas should be fresh and updated and needs to work for Top 5 / Top 10 / Top 15 / Research & rank… i want count down to work like [LongForm Studio page pasted]… also connected vidIQ so we can use it to do more research and better seo."
Answered by the owner: the automation makes everything up to the final video and **waits for the owner to approve the render** (LongForm rule).

### The page, section by section (mirrors LongForm Studio top to bottom)
1. **Header** — "🏆 Countdowns Studio · Top 5 / 10 / 15 countdowns (8–12 min): research → titles → segments → script → video → Countdowns queue" + status chips ● TMDB ● Gemini ● Voice ● vidIQ.
2. **📦 Archive rule line** — same switch and days as LongForm (deletes working files N days after posting once the Drive copy is confirmed; video stays in Drive).
3. **🤖 Countdown automation card** — identical layout to LongForm's:
   ON/OFF · "Today: 1 of 1 started" · **Next:** the topic it will start (or exactly what it waits for, e.g. "Studio is busy with #49") · **Last started** · **Last check** · ⏭ Skip next pick · ⏸ Pause for today · Undo · **Per day** limit (default 1; counts manual countdowns too) · Save limits.
   What it does by itself: picks the top topic → research (format from the topic, or a default Top 10) → **automatic title check** (non-titles swapped for honorable mentions or real TMDB titles; if it can't fill the list it stops at step 2 for you) → confirms titles → makes every segment (Studio's waiting line, after LongForm work) → fills the script (title, intro hook, closing question) → **stops before the final render: card "👀 Ready for your review"**. You review titles/script/segments, then press Render (or 🎞 Render selected). Budget check like LongForm: it only starts when the whole countdown's estimate fits today's cap (≈ $0.12 per segment + research: Top 5 ≈ $0.70, Top 10 ≈ $1.30, Top 15 ≈ $1.90).
4. **Your countdowns (N)** — same as "Your videos": "👀 N ready for your review" / "📤 N rendered, not in the queue yet" lines; filters All · 🔥 Timely (topic tied to this week/month) · Needs review · Reviewed · Rendered · In queue · 🗄 Archived; Select all shown · 🎞 Render selected · 📤 Send to queue · 🗄 Archive · 🗑 Delete; each card: poster strip, title, step line ("3. Segments — 6 of 10", "4. Script — done"), spent $, 👀/📤 highlight, 🤖 auto, ▶ Resume, ⏫ Move up, 🗄 Archive, 🗑, ✓ Reviewed.
5. **Start a new countdown** — same shape as "Start a new breakdown":
   - **Topic box + Top 5 / Top 10 / Top 15 + Research & rank** (kept; the format chosen is the one used).
   - Tabs: **🔥 Top topics (checked & ranked)** · **📅 This month** (topics built from this month's releases and streaming arrivals) · **Evergreen** — each topic card shows its best format and buttons **Top 5 · Top 10 · Top 15** (start it at that size), **+ Add to line**, **👁 Preview titles**, **⭐ Pin**, **✕ Skip**, and check chips: **New** (never made — never-repeat ledger), **Titles** (enough real TMDB titles with trailers for that size: 5/10/15 — a topic that can't fill a Top 15 doesn't offer it), **Demand** (vidIQ search volume/competition), **Timely**, **Debate**.
   - **Fresh:** rebuilt automatically every 3 days and on ↻ Refresh; never shows a topic already made or skipped; "This month" rebuilds on the 1st.
6. **Project page** — same as a breakdown's: header with Stop / Undo / Delete / segment length; step boxes with Run · Re-run · ↻ from here; "▶ Continue to review (from …)"; sections in LongForm order: **Video first** (player, 3 thumbnails + 🔄 Refresh Options, motion graphics, ☁️ Drive copy, Send to Countdowns queue), **Titles** (reorder/swap/remove/add), **Segments** (each opens the full breakdown editor — script with fact-check list, shot swap, re-render — with "← back to countdown"), **Script** (title options with vidIQ scores, intro hook, closing question, 🔊 voice preview, chapters).
7. **Posting** — SocialPilot → 🏆 Countdowns queue (done) with its own ⏰ Schedule; Send = Ready to Post on Screen Central (LongForm rule).

### Engine fixes included
- Segments join **Studio's real waiting line** (no 5-minute gaps); the card names what it waits for.
- Segment length follows the format (Top 5 1.6 min · Top 10 0.9 · Top 15 0.6) for every segment it made — fixes #1 Top 15 horror, whose segments were created at 1.5 min (≈ 30-min video).
- Research and render are restart-safe and pause/resume on the daily cap, like LongForm.

### vidIQ (checked 2026-10-13)
- Status: **vidIQ is connected to your Claude account** (claude.ai connector, `https://mcp.vidiq.com/mcp` ✔). Its tools load in a new Claude Code session. I have not run any vidIQ tool (calls use credits).
- vidIQ's MCP also accepts an **API key** (created on your vidIQ MCP settings page) — so the Scrapper server can use it, not just Claude. Credits: most research calls ≈ 5 credits, shared with your vidIQ AI balance.
- Proposed use (server, all optional per switch, with a **daily credit cap**, cached, never blocking a video if vidIQ is down):
  1. **Topic scoring** — search demand/competition for each topic idea (the Demand chip) and trending searches to seed fresh topics.
  2. **SEO before posting** — vidIQ keywords + title scoring for countdown and LongForm titles/descriptions/tags (suggestions shown in the Script step; you pick).
  3. Optional later: competitor countdown benchmarks for the topic.
- Needs from you: create a vidIQ MCP API key and add it in Railway as `VIDIQ_API_KEY` (I never handle the key), and pick a daily credit cap (suggest 50 credits ≈ 10 calls/day).

### Build order (one commit each, tested, checked live at 390 px)
A. Engine fixes (waiting line, segment length, restart-safe, budget pause). B. Project page parity. C. Your countdowns parity (batch + filters + highlights). D. Start a new countdown: fresh checked & ranked topics with Top 5/10/15 per topic, never-repeat. E. Automation card (runs to "ready for review"). F. Video/Drive/archive parity. G. vidIQ client + Demand chip + SEO suggestions (after the key is in Railway). H. Handbook v7 + RUNBOOK.

### Decisions still open
1. Daily limit: **1 countdown a day** (recommended) or one every 2–3 days?
2. Countdown segments wait behind LongForm work (recommended) or take turns?
3. #1 Top 15 horror (4 of 15 made at the long length, $0.70 so far): stop it now and re-make the 4 at the right length (~$0.50), or keep long (~30 min video)?
4. Drive copies of countdowns: yes (recommended)?
5. vidIQ: use it on the server (create the API key) — and daily credit cap?

### APPROVED — 2026-10-13 (owner answers to plan v2)
1. Daily limit: 1 countdown per day.
2. "It should take up 1 of the time slots midday" → read as: countdown work gets Studio's **midday turn** (12:00–15:00 ET countdown segments go first; otherwise behind LongForm), and the Countdowns queue posts at a **midday slot** (12:00 ET, 1/day) by default.
3. #1 Top 15 horror: keep the 4 made; make the other 11 at the correct length (0.6 min). Checked: it stopped because the **daily cap** was hit (#49 It Follows paused at shots; resumes after midnight) — the card must say so.
4. Drive copies: yes.
5. vidIQ: yes — use it fully for topics, tags, descriptions, insights and trend research across Scrapper. Plan: Boost, **2,000 credits/month** (~66/day). `VIDIQ_API_KEY` is set in Railway by the owner. Budget: daily credit cap (default 60) + monthly guard, cached results, free tools (balance, trend categories, channels) used freely.

## Log — 2026-10-13 — Plan v2 built: Countdowns Studio = LongForm for countdowns, vidIQ wired in (bf972a6 … 0cb529c)
- **Engine (bf972a6, 0cb529c):** segments go through Studio's real waiting line and chain on job end; outside 12:00–15:00 local they wait while LongForm's automation has breakdowns left today; unscripted segments take the format length (fixes #1 Top 15: 1.5 → 0.6 min for the 11 left); daily-cap pause shown on the countdown; research/render resume after cap/restart; a segment a deploy cut off is retried (not a failure); Countdowns queue defaults to one post at 12:00 (set once); research no longer returns a fixed made-up sci-fi list when the AI fails.
- **vidIQ (e15d7f6, b61e835, 72cc813, 379408c):** `backend/app/vidiq.py` — MCP over HTTP with `VIDIQ_API_KEY` (Bearer), 60 tools discovered, exact free/paid credit costs, daily cap 90 / monthly 1,800 (Boost = 2,000), 1–3 day cache, 30-min back-off, every paid call logged. Balance on 2026-10-13: 2,050 (2,000 renew Nov 13 + 50 add-on). Used for: topic demand (keyword_research), breakout/trending countdown videos (outliers, trending_videos), weekly viewer requests (comment_insights), countdown + LongForm hashtags (top related searches, max 12/15 — YouTube ignores all hashtags past 15), title scores and title ideas (on demand). `POST /api/vidiq/call` runs one tool under the cap; `GET /api/vidiq/status`.
- **Topics (82a8de5):** `studio/countdown_topics.py` — ~18 fresh topics every 3 days / on the 1st / ↻ Refresh, from the TMDB calendar + vidIQ breakouts + viewer requests; each checked: New (never-repeat ledger), Titles (real TMDB titles → which of Top 5/10/15), Demand (vidIQ), Timely, Debate; score; pin/skip.
- **Automation (82a8de5):** 1 countdown/day: top topic → research → automatic title check (fills from the topic's checked titles, shrinks the format or stops for you) → segments → script → stops before render (“👀 Ready for your review”).
- **Server parity (eaa3003):** undo (titles/script), batch (render/publish/review/archive/delete), resume, move up, segment length, Drive copy (LongForm Studio/Countdowns; frees segment footage after), thumbnail 🔄 Refresh Options, post-posting archive, segment-copy cleanup after render.
- **Page (932fd49):** CountdownsStudio.tsx rebuilt in LongForm's shape (chips, archive rule, automation card, Your countdowns with batch bar, Start a new countdown with checked topics + Top 5/10/15 + Research & rank, project page with stepper, Video/Titles/Segments/Script; segments open the LongForm editor). Checked at 390 px on a local copy (test DB + generated test token): no sideways scroll.
- Tests: 277 pass. Live: version.json 379408c → 0cb529c; topics built (17); automation started today's countdown; #1 horror restarted after the restart fix.

## PLAN (approved 2026-10-13) — Countdowns: "+ Add to line", topic buttons fill the box, real search demand, vidIQ inside research, daily topics
Owner asked: how vidIQ is used; topics must be high demand / high search / new / timely; what the Top 5/10/15 buttons do; where to queue an idea. Found: demand checked for only 8 topics and judged by vidIQ "overall" (a 4K-searches/month topic passed); vidIQ not used in the ranking research; no "+ Add to line" (planned, not built).
1. **"+ Add to line"** — on each topic and next to Research & rank. Ideas wait in an **Up next** list on the automation card (↑ reorder, ✕ remove, ▶ Make now); the automation makes them before its own pick, within the daily limit.
2. **A topic's Top 5/10/15 fills "Start a new countdown"** (topic + size, its checked titles kept) — nothing is spent until Research & rank or + Add to line.
3. **Demand = searches:** every topic checked with vidIQ (keyword results cached 7 days); score led by monthly searches (log scale) + growth; Demand ✓ ≥ 20K/month, ⚠ 5–20K, ✕ < 5K; vidIQ **rising searches** added as a topic source.
4. **vidIQ inside research:** before ranking, the AI gets the topic's search data (related searches + what people ask) and the top competing countdown videos on YouTube (titles, views) to target real searches and avoid copying.
5. **Topics rebuilt daily** (12–15 topics). vidIQ budget: daily cap 100 / monthly 1,850 (Boost 2,000 + 50 add-on); if a cap is hit, things carry on without vidIQ.

## Log — 2026-10-13 — Plan done: add to line, topic buttons fill the box, real search demand, vidIQ in research, daily topics (7e173cb, 2e32b59, 8faa3bd)
- **+ Add to line** on every topic card and next to Research & rank; **Up next** list on the automation card (↑, ✕, ▶ Make now); the automation makes the line first (daily limit still applies). API `/api/countdowns/line`.
- A topic's **Top 5/10/15 now fills "Start a new countdown"** (topic + size, its checked titles kept); nothing is spent until Research & rank or + Add to line.
- **Demand = monthly searches:** ✓ 20K+, ⚠ 5–20K, ✕ <5K; score led by searches (log scale) + growth; ✕ topics are never auto-picked.
- **Phrase pool (8faa3bd):** the first live rebuild only checked 3 topics — the daily cap (100) was already spent by setup/tests/probes, and per-topic checks would cost ~75 credits/day on their own. Now 6 broad seed lookups a week ("best movies", "best tv shows", "horror movies", "new movies", "netflix movies", "action movies") give ~100 real phrases with monthly searches; the AI must build each topic on one of them, so every topic has real demand. Plus vidIQ rising searches (trailers + a rotating film/TV category) and up to 3 extra lookups per build. Expected ≈ 40 credits/day for topics.
- **vidIQ in research:** related searches + what people ask + top existing videos (titles, views) go into the ranking prompt; shown as "🔎 What people search" on each countdown.
- Topics rebuild daily. vidIQ caps: 100/day, 1,850/month. Today (2026-10-13) used 100 → next rebuild after midnight ET uses the pool.
- Tests 281 pass; page checked at 390 px on the local copy; live: 2e32b59 (site) + 8faa3bd (server).

## PLAN — 2026-10-13 — Countdown video/thumbnails don't load; refresh goes back to the start page; per-platform posting rates (owner request)
Owner: "unable to watch countdown rendered videos or see thumbnails; when I refresh the page it takes me to the starting page… post at a different rate for other social medias — more for Facebook and Instagram, less for YouTube."
1. **Countdown video + thumbnails:** find why the player/thumbnails don't load (file route, auth on media URLs, paths) and fix; verify on the live site.
2. **Refresh keeps your place:** the open tab (and sub-tab / open countdown or breakdown) is kept in the address (#hash) so a refresh — or Back — lands where you were.
3. **Per-platform rates:** each queue's ⏰ Schedule gets "per day by platform" (e.g. YouTube 1, Facebook 3, Instagram 3). The queue posts at the highest rate; each video goes to every platform still under its daily number, so Facebook/Instagram get every slot and YouTube only its share (the first slots of the day). Skipped platforms are noted on the row. Default: no limits (today's behaviour).

## Log — 2026-10-13 — Plan done: countdown media, refresh keeps place, per-platform rates (d733dad, 68305ba, 113711d, d1de8c3)
- **Countdown video/thumbnails (d733dad):** cause — the browser loads media with a short-lived media pass (`?g=`), accepted only on routes in `auth.MEDIA_ROUTES`; `/api/countdowns/{id}/file` wasn't listed → 401. Added. Verified live: video + 4 thumbnails return 206 with a media pass.
- **Refresh keeps your place (68305ba):** cause — the hash reader didn't know `countdowns` (fell back to the start page) and never stored sub-tabs/open items. Now `#tab/sub/id`: `#socialpilot/countdowns`, `#countdowns/1`, `#longform/45` (`frontend/lib/hashnav.ts`). Verified by build/typecheck only (no signed-in browser to click through live).
- **Per-platform rates (113711d, d1de8c3):** each queue's ⏰ Schedule has "Per platform, per day" (YouTube, Facebook, Instagram, TikTok; blank = every post). The queue runs at the busiest platform's number; each regular post goes to every platform still under its number today (YouTube gets the first posts); pinned / 🚀 Post now go everywhere; skipped platforms noted on the row (`publish_requests` gets a `{"skipped": [...]}` entry). Default: none set (unchanged behaviour). Tests 282 pass.
- Owner to set the numbers (e.g. Posting Queue: YouTube 1, Facebook 3, Instagram 3).

## Log — 2026-10-13 — Owner's per-platform schedule applied (a9315d7, dc4d3f9)
- Added: a platform can have its own exact times (`platforms: {"youtube": ["11:00","19:00"], ...}`); the queue posts at the union; each regular post goes only to the platforms whose time it is. Fixed: the schedule API's request model dropped `platforms`.
- Applied live (PUT /api/schedule/config), America/New_York:
  - Shorts (Movie Clips): YouTube unchanged 09:00, 12:40, 16:20, 20:00; Facebook + Instagram 08:00, 12:40, 17:20, 22:00 → 7 posts/day (12:40 goes to all three).
  - LongForm: YouTube unchanged 11:00, 19:00; Facebook + Instagram 08:00, 12:40, 17:20, 22:00 → 6 posts/day.
  - Countdowns: all three at 12:00 (unchanged).
- Watch: LongForm now uses 6 videos/day vs ~5 made/day; videos at Facebook/Instagram times don't go to YouTube (YouTube keeps its own 4 + 2 a day).

## Log — 2026-10-13 — Countdown audio fixed (99be495, cb0e970)
Owner: "there's an issue with the audio for countdown; LongForm works fine."
- Downloaded countdown #1 and probed it: **audio 36 min on an 18-min video**, 96 kHz, 12 kbps. Cause: the stitch joined pieces with different audio (breakdown renders 96 kHz mono, #N cards 48 kHz stereo, intro/outro voice 24 kHz) with `concat -c copy`; players read a stretched, drifting timeline. Fix: `stitch.uniform_audio` gives every piece 48 kHz stereo AAC padded/trimmed to its video before the join; final loudnorm output pinned to 48 kHz. Reproduced and verified locally (old: 4:46 video → 19:05 audio; new: equal). Re-render of #1: audio 18:20.7 = video 18:20.7, 48 kHz stereo, −14 LUFS.
- Then found: **silent intro (18 s) and outro (24 s)** — the intro/outro voice-over was refused by the daily cap and the code silently used silence. Now a refused voice stops the render (cap → paused, resumes after midnight).
- And **~11 s of dead air between entries** — each segment kept LongForm's 8 s silent subscribe end screen. Now each segment's silent tail is detected and trimmed (keeps 0.5 s); chapters follow the trimmed lengths.
- #1 re-render queued; today's cap reached ($4.01/$4.00) → it pauses and finishes after midnight ET by itself.

## PROPOSAL (not approved, no code) — 2026-10-13 — "Screen Central" studio-logo intro for compilations (Marvel-style)
Owner sent a 29 s Marvel Studios intro: rapid comic-page flips in warm washes → hero artwork montage with colour washes + typed names → fire flash → camera flies through giant 3D letters whose faces play footage → lands on the red chrome "MARVEL STUDIOS" bar. Asked: replace the current 18 s countdown intro with something like it, using Screen Central and the movie posters.
**Shape (≈ 8–10 s, not 29 — long logo intros lose viewers):**
1. 0–3.5 s **Poster storm** — this video's own posters/backdrops flip and slide fast, each washed in a colour (Screen Central red → orange → blue → purple), light leaks, motion blur, the titles typed over them (#10 … #1 names).
2. 3.5–4.5 s **Flash** — fire/light burst transition.
3. 4.5–7.5 s **Fly-through** — 3D "SCREEN CENTRAL" letters, chrome edges, each letter face filled with moving poster/trailer footage from this countdown; camera pulls back.
4. 7.5–9.5 s **Lock-up** — red metallic "SCREEN CENTRAL" bar on the red gradient (+ small "TOP 10 · {topic}" under it), held, then straight into the video.
**Sound:** an original, royalty-free sting (never Marvel's music/logo) — made once and reused; whooshes on the flips.
**How it's built (recommended — hybrid, no AI cost per video):**
- Made **once** as reusable assets: the 3D letters as an alpha matte + chrome/shine pass, the flash, the final lock-up, and the music sting (rendered offline, e.g. Blender/Remotion; stored in the repo/volume).
- **Per video** (ffmpeg, ~30–60 s of render): the poster storm from that countdown's posters, and the letter-fill footage composited through the matte. Every countdown gets a different intro with the same brand finish.
- Alternative: build it as a new HyperFrames piece (the motion system LongForm already uses) — easier to tweak, but true 3D chrome letters are limited in HTML; the flat/2.5D version is weaker.
**Where it goes:** replaces the current intro of every **countdown** (and optionally the multi-clip compilations under Compilations & Exports). The 10 s hook stays: either as a **cold open before the sting** (recommended: hook line over the #1 tease → sting → #N), or voiced over the poster storm.
**Decisions for the owner:** length (8–10 s recommended), hook before vs over the sting, music source (YouTube Audio Library / commissioned / vidIQ music ≈ 20 credits once), whether LongForm breakdowns and the short-form compilations get a shorter 3–4 s version too.

### APPROVED — 2026-10-13 — build the Screen Central intro ("Use the sound of pages flipping from the video I gave. And build")
Defaults taken (owner didn't pick): ~9 s, countdowns only, hook as a cold open before the sting.
- ⚠ Told the owner: the page-flip sound comes from Marvel's official intro (copyrighted audio, Content ID risk on monetized videos). Built as ONE swappable file (`assets/intro/page_flips.*`) so it can be replaced by a royalty-free flip sound instantly; all other sound (whoosh, flash, impact) is generated by us.
- Build: per-video ffmpeg/PIL sting — poster storm with colour washes + typed titles + flip sounds → flash → "SCREEN CENTRAL" letters filled with this countdown's posters, zooming back → red chrome lock-up + "TOP N · topic"; then the hook (cold open order: hook → sting → #N).

## Log — 2026-10-13 — Screen Central intro built (ad37ea5)
- `studio/compilations/sting.py`: 9.4 s per-video intro — poster storm (countdown's posters flip in like pages, colour washes, typed titles, page-flip sound) 0–3.6 s → fire/light flash → "SCREEN CENTRAL" letters filled with the posters, extruded red edges + silver rim, camera pulls back from inside a letter → red Marvel-style plate logo + subtitle (the countdown's title) with a shine sweep. PIL frames + ffmpeg (motion blur, 48 kHz stereo); ~14 s to render, no AI cost.
- Countdown order now: hook (cold open voice-over) → intro → #N entries → outro. Chapters include it in "Intro & Preview".
- Assets: `backend/assets/intro/page_flips.wav` (from the owner's Marvel reference — ⚠ copyrighted, Content ID risk; swap this one file for a royalty-free flip sound to remove it), `Anton-Regular.ttf` + OFL licence.
- Test render sent to the owner; test added (287 pass). The horror countdown's paused re-render resumes after midnight ET and will include the intro.

## Log — 2026-10-14 — Narration voice → Gemini TTS (4a4f9ec)
Owner: use Gemini text-to-speech, model gemini-3.8-flash-tts, prebuilt voice Charon, style "energetic, fast-paced YouTube recap host" on every line, via v1beta/interactions (speech_config [{"voice":"Charon"}], text annotation {"type":"speech_metadata","style":…}).
- `studio/tts.py` `synth()` (the one function every breakdown sentence, countdown intro and outro use) now calls Gemini by default: exactly that payload, WAV→MP3, bounded retries with jitter on 429/5xx, per-line cache keyed by model|voice|style|text (so everything re-voices once in the new voice), fails loudly without GEMINI_API_KEY. `TTS_ENGINE=chirp` keeps the old Chirp 3 HD voice. Settings: TTS_ENGINE, NARRATION_TTS_MODEL, NARRATION_VOICE, NARRATION_STYLE (defaults in code).
- Cost: `costs.record_gemini_tts` from the response's token counts, else estimated (≈4 chars/text token, 25 audio tokens/s). Price $0.50/M in, $10/M out is an ESTIMATE from Gemini's published Flash TTS rates — not confirmed for 3.8; ≈ $0.015 per minute of voice.
- Tests 291 pass (payload, cache, retry, missing key, cost). Live test of one line scheduled after the daily cap resets (04:00 UTC); today was at the cap.
- Note: breakdowns already rendered keep the Chirp voice; the next step-5 run (voice + shots) re-voices them in Gemini Charon. The horror countdown's paused re-render will get Gemini intro/outro but its 15 segments keep Chirp voice unless their step 5 is re-run.


## Revival checkpoint — 2026-10-18 — local feature branch

- Worktree: `~/dev/scrapper-worktrees/revival-20261018`; branch `codex/scrapper-revival-full-stack-20261018`; verified checkpoint `6f92fb1`. Main checkout remains at `95b6bd4`.
- Revival changes have been saved as local Git commits, with detailed evidence in `docs/implementation/SCRAPPER_AUTONOMOUS_PROGRESS.md` and `SCRAPPER_COMPLETION_REPORT.md`. This is the first revival entry in this memory file; earlier revival progress was recorded in the separate ledger.
- Verified checkpoint: backend 364 passed / 1 skipped; production frontend build and TypeScript passed; five offline browser scenarios passed. Synthetic LongForm, Top 5, native Short and carousel exports inspected. All roadmap milestones remain partial.
- No GitHub push, pull request, deployment, publishing, live settings change or paid provider request performed during revival work. Owner now explicitly requires all further work to stay local.
- Next authorized order: independent fact verification, atomic budget reservations, then durable worker leases and fencing. Append evidence and limitations at each checkpoint.


## Local roadmap contracts — 2026-10-18

- Added independently fetched, immutable source records and explicit owner semantic reviews. HTTPS source retrieval pins public DNS addresses and checks redirects, content type, size and deadline. Claim words appearing in a denial are not automatically support. Project, title/year/medium/territory, exact excerpt/hash and seven-day freshness are checked. Grounding/model-provided evidence remains excluded. Reviews invalidate saved narration/plan/export approval; the model check remains storyboard-only.
- Added integer micro-dollar reservations serialized by a database mutex; provider attempts reserve before dispatch and settle usage in the same transaction. Missing receipts/timeouts retain holds across calendar resets. Unknown models/multimodal/grounding/TTS price bounds fail closed until explicitly configured. Receipt reconciliation is owner-authenticated and audited. vidIQ credits also reserve atomically; uncertain paid tool calls are not automatically retried.
- Added resource leases with increasing generations, expiry, renewal, stale-write/payment/checkpoint/completion fencing and owner-stop revocation. Applied to resumable jobs, Countdown research/render and native campaigns. Studio output folders are isolated per attempt; committed segment caches are copied for reuse. Native crash recovery preserves earlier export versions. Legacy scrape/compile workers and residential handoff still need full lease integration.
- Checks so far: full backend baseline after first integration 384 passed /1 optional skip; expanded focused contracts 37 passed; production frontend build passed. First six-scenario browser run: five passed; new source-review scenario exposed a dropdown accessible-label selector issue. Fixed its explicit label; fresh full browser rerun pending. Failed trace retained.
- All work local on the feature branch. No GitHub push, deploy, public post, live account mutation or paid API request. External spend remains $0. Full roadmap remains partial; see completion report for release gaps.


## Verified local contract checkpoint — 2026-10-18

- Final backend: **395 passed /1 optional skip**, 37.16s, 32 existing deprecation warnings; unhandled worker-thread exceptions were treated as test failures. Production frontend build5 and explicit TypeScript check exited 0.
- Final browser3: **all 6 passed /2.4min**: LongForm, Top 5, native Short/carousel, navigation, project history and independent source-review UI. Fixed a Countdown idle-read polling freeze; regression forces one stale response and proves recovery. Earlier failed traces remain preserved.
- Lease cleanup now handles owner stop without leaking the local runner lock; closing a queued child preserves its batch parent's lease; an explicit invocation after expiry stops the obsolete crash checkpoint. Dedicated negative tests and the final backend suite pass.
- Final isolation: zero external attempts, zero scheduler threads, five explicit fake posting receipts and $0 external spend. Owned test servers stopped; synthetic evidence retained.
- Root MEMORY.md and the detailed ledger are updated. Changes are committed locally on the feature branch; no GitHub push, PR, deploy, publication, live account change or paid request. Latest owner instruction remains LOCAL ONLY.
- New return package: `outputs/scrapper-roadmap-contracts` in the chat workspace. Full remaining limits, architecture and configuration are in `docs/implementation/LOCAL_CONTRACTS_20261018.md`; M1–M5 remain partial.


## Continued roadmap: worker ownership and residential protocol v2

- Owner requested continuing all remaining local roadmap work, starting with unfinished worker integrations. Local-only/no push/deploy/publication remains in force.
- Legacy ingest/compile now acquire generation leases before rechecking queued state; stale scans do not replay completed jobs, and recovery skips live leases and never marks arbitrary nonempty partial files complete. Outputs and download-archive snapshots are attempt-owned. Compilation requires every selected clip and final-file QA. Subprocess loops check lease loss.
- Residential protocol v2 adds server-issued claims, renewal, expiry, identity checks, bounded fully decoded uploads and durable hash-bound completion receipts. Local client streams multipart content, renews ownership, cancels on lease loss and checks receipts before any retry. Older unclaimed worker uploads fail closed. No actual external download or upload was run.
- Focused worker media/API tests: eight passed (real FFmpeg synthetic uploads and actual compilation; fake remote downloader). Expanded control/waiting regression results are recorded in artifacts/worker-contracts5.txt. Earlier harness GET-method and fixture status issues were corrected without loosening the stale-worker protections. Full regression follows the next integration checkpoint.
- Next: append-only publication evidence/manual uncertainty resolution, then orphan budget/credit recovery and remaining data/asset contracts. Continue autonomously; do not represent blocked external/production lanes as complete.


## Continued roadmap: publication evidence and worker UI verification

- Added an append-only publication journal and owner-authenticated uncertainty decisions. Exact account/version/receipt, HTTPS evidence and explicit review are required. Nonacceptance returns to review; acceptance still waits for provider reconciliation. Journal rows survive queue deletion and numeric-ID reuse; SQLite triggers refuse update/delete. PostgreSQL DDL is authored but unrun.
- Publication dispatch now has a renewable lease; reconciliation skips live batches, requires strict complete Boolean per-target results and uses a version comparison after provider reads. Late receipts survive stale-worker fencing and require explicit adoption. Failed transactions roll back before separate lease cleanup.
- New journal regression suite: 13 passed. Combined earlier queue/native/journal suite: 103 passed before the additional lease-revocation case. Full backend: 415 passed/1 failed/1 skipped; the one failure expected an older duplicate-submission error string. The same safety rejection now includes the established unresolved label; focused recheck follows.
- Browser suite: 7/8 passed initially; worker HTTP arrival/resume passed. A selector-label issue in the new publication dialog was fixed. Fresh focused browser rerun: both residential-worker and publication-review scenarios passed (10.4s). New production build passed. The worker client used real localhost claims/multipart upload/receipt checks; only download was replaced with original synthetic footage.
- No real provider call, publish, push, deployment, account change or paid spend. Both isolated stacks were stopped. Continuing next with financial orphan/credit recovery; roadmap remains partial.


## Continued roadmap: financial recovery checkpoint

- Paid USD attempts and vidIQ credit attempts now acquire independent renewable request leases. Expiry never releases money/credits. The owner cannot reconcile a live request; interrupted reserved/dispatched holds require explicit interruption review and provider evidence, including older holds without lease metadata.
- Added local financial hold/history API and Settings UI for exact receipt amounts, rationale, interruption acknowledgment and per-request USD ceilings. Decisions are version-checked, idempotent by UUID and serialized; append-only SQLite history rejects raw update/delete. No billing API is called.
- Late USD usage receipts preserve the earlier owner decision, record only the charge delta and raise a breach on disagreement. A fresh owner decision is needed to resolve it. Financial receipt recording remains possible after content-worker lease loss. Late vidIQ configured estimates are recorded separately and do not overwrite an owner's exact provider receipt. Configured vidIQ tool prices remain estimates requiring real provider validation.
- New financial regression suite plus atomic-budget/vidIQ tests: **23 passed**. Full backend: **423 passed/1 optional skip**, 36.05s; 32 existing deprecation warnings, thread exceptions treated as errors. Production frontend build passed. Financial browser test passed (2.2s), exercising both USD and credit reconciliation plus a saved request ceiling. Worker/journal focused browser rerun previously passed both scenarios.
- Publication focused backend recheck after the established unresolved error label was restored: 18 passed. Latest full backend also covers it. Root MEMORY.md and this ledger remain updated; local commits only. No push/PR/deploy/publication/live account mutation/paid provider request; external spend $0. Continuing remaining local lifecycle/editorial gaps.


## Continued roadmap: media lifecycle, mutation and recovery contracts

- Added database-serialized shared/exclusive file claims with expiry, renewal, descendant-path conflicts and stale database/subprocess fencing. Integrated Studio stages/edits/archive/restore/Drive, publishing media, compilation inputs, Drive staging and cache eviction. Retention/deletion now preserves database references (including Studio handoffs and compilation inputs) and live readers. Financial/publication receipts still survive content ownership loss.
- Drive saves now independently read remote MD5/size and retain local SHA-256 proof. Old saved labels alone cannot authorize Studio deletion. Source hashes are kept before footage removal; restore fully decodes and compares re-downloaded bytes, preserving old plans on mismatch. Existing queued remote copies remain protected. No real Drive account was used.
- Added bounded atomic Netscape cookie uploads (private permissions), shared Drive/upload full decode, 20-megapixel image/video bounds, unique manual trailer/queue image paths and copied queue thumbnails. Queue mutations refresh under a writer mutex; late AI rewrites cannot overwrite newer owner edits or a submission. Undo refuses submission evidence/reused identities and invalidates narration after upstream changes. DB operational logs now have configurable 30-day/100,000-row defaults.
- New SQLite schemas use monotonic core IDs. Existing SQLite tables are not rebuilt; migration and dangling-reference review remain release requirements. A full-suite fixture contained a compilation pointing at a not-yet-created clip #1; the new test now creates an unambiguous ID without relaxing the real reference guard.
- New explicit invocations supersede old queued/budget-paused/worker-wait checkpoints for the same resource, preventing stale automatic revival. Drive/restore lease keys ignore cosmetic title changes.
- Full backend **447 passed/1 optional skip**, 37.60s; 32 existing deprecation warnings; worker thread exceptions are errors. New lifecycle/mutation suite 21 passed. Lease/vidIQ focused recheck 16 passed. A full nine-browser run passed eight scenarios and exposed a vidIQ missing-credential path reserving impossible credit holds. Moved credential validation ahead of admission and added a regression; fresh browser rerun is in progress. Do not claim all nine passed yet.
- All local, no GitHub push, PR, deployment, publication, live account mutation or paid API request. External spend $0. Media lifecycle scope/limitations are in docs/implementation/MEDIA_LIFECYCLE_20261018.md. Continuing remaining roadmap work; no production-readiness claim.


## Verified lifecycle browser checkpoint

- Fresh full browser rerun **9/9 passed, 2.2min** (artifacts/e2e/lifecycle-browser5.txt). Residential HTTP handoff/resume, unknown-publication evidence UI and USD/credit recovery all passed alongside existing LongForm/Top5/native/navigation/source-review flows. The missing-vidIQ-credential admission bug is fixed without weakening the zero-unsettled-holds assertion. Focused admission/lease suite: 16 passed.
- The local lifecycle commit is 03d4d53. Its broad backend result is 447 passed/1 optional skip, plus the new missing-credential test in the focused suite. Both synthetic servers from the fresh browser run are stopped; external spend $0, no push/deploy/publication/live account changes.
- Continuing unsaved Studio/Countdown draft preservation and stale-save checks. The full roadmap remains unfinished, including external/provider/production lanes.


## Verified draft recovery and stale-save checkpoint

- Studio and Countdown unsaved script fields now survive idle polling, tab/project navigation and reload through identity-scoped browser drafts. Recovery is explicit. Concurrent server edits preserve the local words, show the saved version for review and block saving until the owner resolves the conflict. Backend script versions reject stale writes.
- If browser storage fails, in-app and external-hash navigation share one confirmation; cancellation restores the editor route. Native unload protection also applies. Countdown undo now uses the correct table/scope and rejects legacy mismatched snapshots instead of touching a same-number queue row.
- Full backend: **451 passed/1 optional skip**, 37.77s, 32 existing deprecation warnings; worker-thread exceptions treated as errors. Production frontend build4 and explicit TypeScript check passed. Full browser7: **10/10 passed, 2.5min**, including draft recovery/conflict/storage-failure navigation plus all previous workflows. Final isolated stack reported zero external attempts, no scheduler threads and six fake publication receipts; owned servers stopped. Dependency consistency check passed.
- Local-only checkpoint; no GitHub push, PR, deployment, publication, live account change or paid provider request. External spend remains $0. Full roadmap remains partial. Continuing legacy Countdown file-copy/deletion protections next.


## Verified Countdown lifecycle checkpoint

- Countdown render sources, approval, queue handoff, edits, Drive transfers and cleanup now share cross-worker file claims. Drive jobs have renewable generation leases, preserve remote checksum/size/local-hash proof and reject stale render results. A queue row receives the backup link only for its exact editable video without unresolved publication evidence. Owner Stop revokes Countdown Drive ownership too.
- Scheduled video reclamation requires the current verified copy, exact archived queue backup link, no foreign references and exclusive file ownership. Owner deletion refuses unresolved submissions and shared exports; busy scheduled deletions are skipped. Thumbnail refresh stages privately and fences promotion, while queued/shared thumbnails cannot be replaced. Countdown stitch directories now use per-attempt random IDs rather than second-resolution timestamps. Legacy numeric IDs stay readable.
- New lifecycle regression corpus: 15 cases in the final full suite, including two render attempts at the same frozen time, stale thumbnail promotion, stale Drive status, exact queue-file linking, a different backup URL sharing an ID prefix, active readers and uncertain submissions. Prior focused combined run: 52 passed before the last exact-link case.
- Final backend7: **466 passed /1 optional skip**, 40.48s, 32 existing deprecation warnings; worker-thread exceptions are test errors. Production frontend build4 passed. Full browser5: **10/10 passed, 2.6min**, including actual thumbnail refresh before export approval and rejection of queued-thumbnail replacement. The final stricter archive-link comparison is backend-tested; browser-safe mode disables archive scheduling. Final isolation: zero external attempts, no scheduler threads, six fake publication receipts, $0 external spend. Owned servers stopped.
- Architecture, completion matrix, local contracts, lifecycle notes and resume instructions are reconciled with these changes. Main remains clean at 95b6bd4. All commits are local; no GitHub push, PR, deploy, public publication, live account change or paid request. The complete Screen Central roadmap remains unfinished, including full factual verification, broader creative formats, retained original-studio export history, remaining stream/trash paths, analytics, PostgreSQL/CI and real-provider/production validation.


## Continued execution: typed material-credit contradictions

- Owner reiterated continuing the entire remaining roadmap without stopping at checkpoints. Continue after commits; keep all actions local and external spend at $0. No background/second agent is being launched.
- Expanded model-independent relational checks: exact actor/role identity (including reversed grammar and voice aliases), screenplay versus original novel/story credit, and explicit title/release-year contradictions. A related character such as Scytale’s father no longer passes a substring check. These checks are deterministic containment, not a claim that arbitrary prose has been independently proven.
- New 14-case material-credit corpus plus existing revival/evidence regressions: **51 passed**, artifacts/material-credits1.txt. Source words and legitimate novel context are preserved.
- Also in progress: response-lifetime shared media claims (61 focused checks passed), retained original-studio exports/draft recovery, and immutable thumbnail paths. The first history integration run exposed missing-parent thumbnail staging and an old test tied to overwriting a canonical file; fixing the real path handling and asserting preserved selected bytes. These broader changes are not yet claimed verified or committed. Next: finish history API/UI/regressions, full backend/build/browser checks, commit and continue M1–M5.


## Retained exports and response ownership, continued local work

- Original Studio and Countdown exports now retain independent content-addressed media, saved inputs and inspection metadata. History survives upstream edits, project deletion and numeric-ID reuse; draft restoration requires an explicit current-version comparison, clears render readiness, and preserves current/old media. Database triggers reject history updates/deletes. Deletion protects active/uncertain consumers and relocates settled queue references with an evidence event.
- File responses hold renewable shared ownership until the final body completes; range/HEAD/cancellation and expired-owner paths are tested. Thumbnail options use unique immutable paths and the selected bytes stay intact. Fixed broken hardcoded thumbnail paths under per-attempt render folders.
- History baseline backend9: 498 passed/1 optional skip, 44.67s; 32 deprecation warnings, worker-thread exceptions are errors. Production build7 passed. Focused retained-history browser13 passed (2.1s), including script recovery, actual download/range bytes and deletion survival. Complete browser12 passed 10/11; the final recovery POST hit the unchanged 600/minute per-IP limit. The test now respects Retry-After and explicitly retries through the UI; a fresh complete rerun remains required. No full-suite browser pass is claimed for this slice yet.
- Root memory, detailed ledger and continuous queue remain current. Local commit only; no push/PR/deploy/publication/live account change or paid provider call; external spend $0. Continuing independent evidence identity/contradiction lineage, then Countdown eligibility.


## Independent evidence identity and contradiction lineage

- Retrieved source records and claim reviews now have append-only database guards, including raw SQL. Additive SQLite/PostgreSQL migration code installs guards on existing tables and leaves legacy unknown identity NULL; it does not invent provenance. New records/reviews bind the original project creation identity and cannot support a reused numeric project ID. Current project context, record SHA-256 and retrieval freshness are checked again when narration is admitted.
- Supported decisions must explicitly address every earlier contradiction/unresolved decision for the same claim/context and retain the rationale and resolved review IDs. A newer contradiction invalidates older support until resolved. Case/punctuation edits cannot hide the earlier decision; semantic paraphrase matching remains outside this deterministic guard. The owner UI shows the earlier excerpts/rationales and requires explicit acknowledgment plus resolution text.
- Focused evidence/credit suite: 36 passed. Broad backend2: 507 passed/1 optional skip, 43.26s, 32 deprecation warnings; later current-context regression and combined Countdown/evidence lane: 64 passed. Production build4 passed after removing an unsupported Playwright config option caught by TypeScript. Full browser6: 10/11 passed, including the expanded support→contradiction→explicit-resolution flow; the final history scenario hit a transient reader claim during Save. A UI retry respecting unchanged busy/rate-limit responses passes the history scenario (focused browser8, 2.5s). Complete combined fresh rerun remains pending.
- Commit and continue; no checkpoint ends the roadmap. All local, MEMORY.md maintained, no GitHub push/PR/deploy/publication/live account change or paid provider request; $0 external spend. Countdown category, territory, ranking-rationale and intro/outro review work is in progress, not yet included in this evidence commit.


## Continued Countdown admission and retention work (in progress)

- Countdown confirmation now validates recognized genre/country/language/release-period/provider constraints and a substantive editorial rationale before creating segments. Exact rank positions and independently retrieved category snapshots are retained. Found/fixed a nested-JSON flush bug that lost persisted segment IDs. Manual rationale editing has local draft preservation and stale-write checks. Intro/outro review binds the exact saved words/current segment research; changed words, research or expired category evidence block voicing/release. Provider names in published copy come from bound server records, not editable draft availability labels. Legacy saved-list compatibility is explicitly not a new verification claim.
- Backend11: 529 passed/1 optional skip, 44.91s; 32 deprecation warnings, worker-thread exceptions treated as errors. Build14 passed. Earlier complete browser7 passed 11/11 (2.9min). The expanded late-ack/rationale browser13 exposed an accessibility-label lookup issue (repaired) and a test-server source-generation mix from lazy Python imports while continuing edits. New E2E runner freezes/hash-records backend source, including a staged-index option; a fresh staged Countdown full-browser16 run is active. Do not claim its result yet.
- Storage retention follow-on is uncommitted/in progress: settled queue deletion retains original media/content/delivery evidence; automatic remote trash paths are being replaced with append-only kept-version records so a newer upload cannot erase a different older export. Transfer receipts survive local ownership loss. Focused delivery/queue/file/Countdown contracts: 127 passed; earlier integration lane 172 passed. New owner-facing delivery-history UI still needs build/browser verification.
- Continue after the next local commit. MEMORY.md and the detailed ledger remain updated; $0 external spend, no GitHub push, PR, deployment, public publication, live account or setting changes.


## Countdown admission committed locally; continue delivery retention

- Local commit c30fe36 saves Countdown category, exact-rank/segment-binding, stale rationale and exact-word bookend review work. Backend11: 529 passed/1 optional skip (44.91s); build14 passed. Full browser7 previously passed 11/11 (2.9min). Expanded browser16: 9 passed, with two harness/recovery failures: missing frozen local-worker script (fixed) and Settings hiding all recovery cards on a schedule-429 response (next delivery-history slice). Focused18 residential HTTP worker passed; focused Top5 made and decoded its 1.35-minute export, then hit a test-only hardcoded three-receipt assumption when run alone. That assertion now compares to its captured pre-run receipt baseline, preserving the no-automatic-submission contract.
- Final fresh combined browser rerun is still required. Source-generation hashing and staged-source isolation are implemented. Remaining roadmap stays open, including specialized eligibility, full factual coverage and legacy saved-list upgrades. No push, deployment, live-account changes, publication or paid provider request; all local and $0 external spend. Continue immediately through the next local slice.


## Verified retained delivery and remote-version checkpoint

- Original Studio/Countdown export recovery and delivered content now retain independent hash-addressed bytes and append-only creation-identity snapshots. Queue reclamation keeps exact available media, copy/targets/provider observations and a journal link. Missing legacy bytes are explicitly unavailable. Owner Settings provides authenticated history/downloads even if schedule reads fail; unknown file types use attachments/nosniff. Delete failures preserve the row and show the owner the reason.
- Automatic remote media and database-backup deletion are disabled. Folder membership, age and a successful new upload do not prove ownership or preserve a different prior version. Remote creation IDs (including failed verification) and independently verified transfer receipts persist even after content ownership expires. Kept current/superseded versions have append-only evidence; managed remote reclamation and remaining local backup/temp paths remain open.
- Batch public status keeps polling through durable checkpoint completion. The new delayed-completion regression reproduces the earlier `running=false` / database-row-still-running race; it no longer causes premature owner completion. The older identity-reuse journal test now first verifies ORM rejection of an uncertain deletion, then uses controlled raw fixture deletion to simulate historical out-of-band ID reuse.
- Final backend11: **542 passed/1 optional skip**, 43.33s, 32 deprecation warnings; worker-thread exceptions are test errors. Full browser6: **12/12 passed, 3.3min**, including rationale edits, stale review acknowledgments, real 1.35-minute Top5 output, residential HTTP recovery, independent contradiction resolution, retained export deletion survival and retained delivered bytes under a forced schedule-503 response. Build12 passed after final explanatory-copy corrections. Later batch-completion/remote-backup-pruning guards are backend-tested after that browser run. Focused finalization10: 51 passed.
- Browser source generation hash: 44d8fbd14a10994bea17612e97a38bf352149c0f01968947b8c137557c2b914b. Final isolation, authenticated full SQLite evidence snapshot, traces/screenshots and decoded media are retained under ignored artifacts/e2e; owned test servers stopped. Zero blocked external attempts, no scheduler threads, six fake delivery receipts, $0 external spend.
- Docker launch made the daemon available but automatically restarted unrelated saved local containers and added pressure. Restored Docker Desktop to its original stopped state; no user volume/image/container was deleted or queried for application data, and no PostgreSQL/Linux pass is claimed. Pursuing a private local PostgreSQL cluster next. Homebrew skipped link/post-install steps but hit an existing dependency link conflict; downloaded official bottles will be extracted into ignored task artifacts instead of forcing user dependency links.
- All local; no GitHub push, PR, deployment, publication, live account or setting change, real provider request or paid API use. Commit this slice and continue the remaining C01–C14 queue; the full roadmap remains incomplete.


## PostgreSQL execution and archive scope update

- Executed actual PostgreSQL 16.15 contracts in a newly initialized, checksummed cluster over a private Unix socket with TCP disabled. Official local bottle files were privately extracted/relocated; no system PostgreSQL service or existing database was used. Docker remains stopped. First real run: 73 passed, 7 failures exposing PostgreSQL append-only error-class inconsistency. Guards now return SQLSTATE 23514, matching the integrity contract. PostgreSQL5: **82 passed**, 10 deprecation warnings, 2.49s, including host clock skew +/- ten years. Lease acquisition/renewal/guard/revocation use the database clock; file ownership cleanup uses the same shared clock. Cluster was stopped and its successful fixture removed. This does not close Linux/container or the rest of C11/C12.
- Latest owner instruction authorizes GitHub push, deployment and live testing **after implementation is finished**. No push/deploy has occurred. Paid-provider spending remains unauthorized. Owner also requests a complete authorized YouTube-to-Drive archive, actual video bytes, inventory/storage checks, ledger/resume/dedup, bounded local worker, manifest and UI E2E. Nine channel entries were supplied for a ten-channel objective, and Tiny Friends TV has no exact URL/screenshot here. Asked for missing identities, Shorts-only versus all public formats, and clarification of ambiguous SocialPilot publishing text. Independent local engineering continues while answers are pending. No archive queue/schedule/publish action is inferred.
- Existing archive-path audit: drive_sync.ingest_channel_to_drive defaults to25 (including zero fallback), downloads the whole batch before upload, deduplicates by queue filename suffix, auto-creates QueueItems/destination accounts, and unconditionally removes temporary files even on unverified/failed uploads. No canonical-channel/video ledger, quota preflight, full-library snapshot or verified upload exists there. Existing drive_store uploader already checks remote MD5+size and local SHA; uploads.validate_video decodes media; worker transfer v2 has generation-fenced claims/receipts. Reuse those contracts, keep archive state separate from publication state, and stop/log access challenges rather than evading controls.

- SQLite lease/file/budget regression7: **36 passed**, 10 warnings, 1.79s. A prior command referenced a nonexistent test filename and collected no tests; corrected lane7 is the evidence. Continue local roadmap after committing this slice.


## Full-channel archive foundation and exact owner scope

- Owner supplied all ten screenshots and clarified **Shorts only, archive to Movie Clips, then wait for next instruction**. This resolves the earlier format/publishing ambiguity: no queue/Ready/accounts/schedule/publication action. Tiny Friends TV's exact handle is @nfiqjfoiqhfqnfi131, canonical UCpc3WsozCCi1Cd1tf_Ne1RQ; tenth source is @ArliStoryTime. Independently resolved all ten canonical IDs through public metadata; the repository registry records them. Runtime full inventories/actual media/Drive uploads have not occurred. Tiny Friends TV resolves correctly but its Shorts tab is currently unavailable to yt-dlp; preserve/report that error, never silently import regular videos. A mistaken OCR candidate returned404 and was rejected.
- New archive ledger is independent from QueueItem. Full tab enumeration has no25/50 count cap. Stable composite canonical-channel/video keys retain title changes and missing/deleted history. Existing uploader, Drive folder/client, media validator, worker lease and resumable job contracts are reused. Sequential transfers persist intent and IDs, validate real bytes, verify remote MD5+size and local SHA before cleanup, reconcile lost responses, preserve ambiguous/nonmatching versions and maintain channel manifests. Existing ingestion endpoint now inventories first; legacy jobs use archive-only behavior. Archive UI supports inventory, storage counts/preflight, run, retry, refreshed progress, Stop and manifests. Mac residential handoff is the next integration, not claimed complete.
- Focused archive1: **13 passed**. Combined archive/lease/undo3: **31 passed**, 1.82s. Production build2 passed. Real Playwright archive-browser5: **1 passed,12.8s**; full UI63-entry fixture inventory, real ffprobe/FFmpeg/hash validation, simulated Drive response loss, reload/retry,63 unique cloud fixture files,63 total download calls, dedup rerun, unchanged publishing queue, owner manifest. Actual Drive upload count=0; all external media/provider boundaries simulated. Source hash a0ceba9d1e6f9852c786f037bcae2d26096a28d364899714ab5102d2b6dcf6d8. Owned test servers stopped. Later reload-Stop/ownership/error handling improvements are undergoing full backend/build regression.
- Canonical verification used public read-only metadata with request spacing; no media download, credentials, paid calls, live account settings changes, push or deployment. Archive adapter does not add bot-control/DRM/authentication evasion; access challenges stop the run. Continue roadmap after local commit; full completion remains open.

- Archive backend6 full regression: **555 passed/1 optional skip**, 43.77s, 32 deprecation warnings; worker-thread exceptions are errors. Production build7 passed after reload-visible Stop/status improvements. Save this verified foundation locally, then continue Mac-worker and remaining roadmap.


## Mac archive integration, actual inventory and Drive preflight

- Archive Mac protocol now has channel-scoped renewable generation claims, single-item pending work, authenticated bounded streaming uploads, exact channel/video identity checks, receipt reconciliation, release/finish and UI Stop fencing. It shares the existing clip-worker multipart transport. The Mac retains bytes until a matching server SHA/Drive-ID receipt confirms independent remote MD5+size verification. A lost server acknowledgement is reconciled before another send. Existing local_worker --watch processes only explicit owner archive handoffs; challenges stop instead of auto-looping. Local public inventory upload is supported without transferring Drive credentials to the Mac. Server/local download adapters use no cookies/proxies/PO-token workarounds and cancel on lease loss.
- Focused worker/channel/clip integration4:26 passed,3.16s. Later zero-quota guard plus owner-credential tests13: **29 passed**,3.46s. Full backend10: **561 passed/1 optional skip**,47.32s,32 warnings; two subsequent focused owner-auth tests are separate. Production build5 passed. Actual isolated browser7: **1 passed,20s**,63 fixture files plus a new64th via real localhost Mac multipart HTTP, simulated lost final acknowledgement,64 unique cloud fixture files and no queue changes. Source23bc0ffce5d54f3a9e023244d1a047c37ea215f05b214998186612244a256f80. PostgreSQL8: **100 passed**,3.19s. Later zero-quota test was added after that PG run. Full13-scenario browser12 is in progress against frozen source15a779e24d87ea262f1fd73c550f638ee39ecd78c9eeeafa8c546120abbcda86.
- Complete public Shorts enumeration (no media bytes): CharanEbenez298; AfridiAE208; RockMovie777129; Agi3D3 277; sir7076 259; FLICKSFYP1120; theaiplot674; DoctorWhoShorts1023; ArliStoryTime16 = **4004 Shorts across9 complete libraries**. Flat listings provide no byte sizes; all4004 size estimates remain unknown. Inventory snapshots are retained under ignored artifacts and summary ARCHIVE_INVENTORY.json. No source media has been downloaded and no real Drive upload has occurred.
- Tiny Friends TV exact screenshot handle/canonical identity resolves, but its current public page independently inspected in the browser has17 regular videos (ASMR/other material), no Shorts tab, and differs from the26-video movie-clip screenshot. Its inventory remains incomplete, not zero historically available. Do not import regular uploads as substitutes or invent accessible historical Shorts.
- **Real read-only Drive preflight:** Movie Clips folder1kuOKRQQRL0ws5aOVqwkdUzdnfj5KQGjo exists and canAddChildren=true, has no Shared Drive ID, while the configured service account reports storageQuota limit0/usage0. Server preflight now refuses before downloading under zero quota. The in-app Drive browser is signed out. Asked the owner which existing authenticated path to use (Brave Personal / owner sign-in / existing OAuth local config). New optional GOOGLE_DRIVE_AUTHORIZED_USER_JSON uses an explicitly configured existing grant, never creates or expands one, and fails clearly rather than silently switching accounts. Tests wipe that credential variable. No account/permission change, OAuth grant, video download, real upload, push or deploy has occurred. Continue independent local roadmap while this actual external dependency is pending.


- Full combined browser12 completed: **13/13 passed,4.4min**. It covers the new64-file archive/Mac HTTP/lost-ack flow and all prior LongForm, Top5, native campaigns, source-review, residential recovery, uncertain delivery, financial recovery, drafts and retained media flows. Evidence copied to artifacts/e2e/history/archive12; owned servers stopped. Fixture Drive media totals1,032,576 bytes,64 downloads/64 unique media copies,0 queue rows,0 actual Drive uploads and$0 external spend. Later retained-copy receipt/cleanup changes passed focused receipts15: **42 passed**,3.35s, and PostgreSQL16: **102 passed**,2.93s. Archive remote creation/verification now also uses the shared append-only copy-evidence journal. Cleanup resumes after a lost completed-upload response; verified remote bytes are rechecked before exclusive local reclamation.
- Owner asked whether Railway already has everything and authorized Brave Personal as fallback. The globally installed Railway CLI is obsolete/not logged in; an isolated official @railway/cli current version successfully reads the existing linked **scrapper** production project. No live variables changed. Live deployment still runs baseline95b6bd45 from main. Read only existing Drive configuration **in memory**, printing only key names/credential type/capabilities/quota, never credentials: it uses a service_account and the same personal Movie Clips folder, storageQuota limit0/usage0. Thus the zero-quota blocker is confirmed against Railway's actual connection. Brave Personal also reaches the Google identifier sign-in page. Preserved its login tab and asked owner to sign in; this is access input, not another implementation approval checkpoint. Local engineering continues. No grant, account/permission change, push, deploy, paid provider call, source media download or real Drive upload.


## Shared database time for budgets, ownership and recovery

- PostgreSQL time now controls daily/monthly budget admission, usage-event timestamps/month keys, credit ledger days, new USD/credit hold creation, job creation/checkpoint/heartbeat/recovery and financial orphan eligibility. Exclusive delivery-file users are checked against the same clock. Lease predicates were already switched to PostgreSQL clock_timestamp; SQLite retains local test-clock behavior. Concurrent '+today' budget increases now acquire the common mutex before reading settings, preventing lost updates. The regression barrier forces the old read-before-lock race deterministically.
- Focused SQLite3:41 passed,1.72s. Actual PostgreSQL5: **106 passed**,2.91s, including +/-ten-year host skew for admission, charges/receipts, credit-day accounting and job heartbeats; future host time cannot mark live financial ownership orphaned or ignore an exclusive file holder. A preceding PG4 test used a fixture missing required operation and failed at NOT NULL before the intended assertion; the fixture was corrected and PG5 is the evidence. Full backend6: **565 passed/1 optional skip**,43.43s,32 warnings. No provider calls or spend occurred.
- C11 remains partial: provider invoice/estimate distinction, billing-contract validation and broader crash/upgrade cases remain. A locally serialized estimated-cost ledger is not proof of actual provider billing. Full roadmap work continues after this local commit. Owner Drive login is still pending; no actual source media or Drive upload, push/deploy/publication/account change or paid API call.


## Exact-version independent evidence review

- Source-review writes now require both exact displayed claim text and a SHA-256 generation of original project identity, facts/research and append-only record/review IDs. Changed words at the same array index, context changes, newly retrieved records or later reviews return409 before saving any decision. Required fields force older clients to refresh instead of silently reviewing new words. Source retrieval/review times and admission freshness use PostgreSQL time; local SQLite can still simulate an advanced test clock.
- Owner acknowledgment binds all displayed decision inputs: project/claim/facts, evidence version, selected record/hash, excerpt, verdict, rationale and conflict-resolution choices/text. Any edit clears the effective acknowledgment; a stale decision cannot be approved by an earlier checkbox. Browser regression explicitly checks editing rationale and resolution requires acknowledgment again.
- Focused evidence3:28 passed. Production build2 passed. Actual source-review browser6: **1 passed,12.6s**, retaining support→contradiction→explicit resolution, draft invalidation and no voicing of unchecked narration. Frozen source82a62eba21e55207a7a71d9c8b61bf45e2422e308b04e6e418b86f37a9ab2965; owned servers stopped. PostgreSQL7: **112 passed**,3.96s, including authoritative evidence freshness under ten-year host skew. Old expiry tests were updated to advance the authoritative clock instead of expecting client skew to expire PostgreSQL evidence.
- First full backend8 exposed shared test-client rate buckets: added independent evidence GETs consumed the same per-minute budget across unrelated tests, causing8 downstream429/count failures. Unit-test setup now resets request buckets between cases while preserving real per-case rate-limit tests and production behavior. Corrected full backend9: **570 passed/1 optional skip**,46.66s,32 warnings. C01 broader typed/semantic factual coverage remains open; this commit closes the stale acknowledgment/write race, not all fact checking.

## Owner deferred the archive; original roadmap resumes

- Owner explicitly said to forget the YouTube/Drive task for now and return to the original task. Archive/login work is deferred. No source video or actual Drive file was downloaded/uploaded; public metadata/code/test evidence stays saved, and the archive remains separate from publication. Do not continue login, bulk downloads or live archive work unless the owner resumes it. The Brave task tab was closed; temporary in-app research tabs are not marked to survive.
- Continue original Screen Central C01–C14 work locally, maintain MEMORY.md and local commits, and do not stop at this checkpoint. Backup/restore/upgrade ownership is next. No deployment, public posting, paid provider call or account/permission change has happened.

## Verified SQLite snapshots, offline restore and legacy upgrade

- Snapshot names include UUIDs; raw SQLite backup includes WAL, then integrity/gzip CRC/raw SHA/compressed SHA checks and fsync finish before atomic no-replace publication. A six-way same-second concurrency test exposed a double-close/reused-fd defect in the first implementation; stream ownership was corrected, and subsequent concurrency tests pass. Failed compression leaves no complete backup or success status.
- Pre-migration backup runs before create_all and fails startup closed. Persisted status uses a filesystem lock and atomic replacement; older completion cannot overwrite newer evidence. Retention keeps at least one local copy, protects the current name and skips other active readers/transfers. Backup Drive uploads now share remote checksum/size verification plus retained creation/verification receipts; simulated checksum failure never reports synced. No actual Drive upload occurred.
- Restore requires explicit offline confirmation and exclusive maintenance lock; app processes hold shared locks. Verified emergency snapshots include committed WAL data. Input decompression has storage bounds, table identifiers are quoted, and post-restore integrity/counts are checked. Tests exercise only disposable databases. New offline identity CLI backs up first, transactionally rebuilds known legacy tables with AUTOINCREMENT, preserves rows/indexes/triggers, raises the sequence floor above retained local Studio directories, checks foreign keys and rolls back both tables on unexpected schema. Empty legacy tables and repeated no-op upgrades are tested. No live database was restored or migrated; custom DATABASE_URL/media roots need the documented review.
- Actual pinned baseline95b6bd45 schema upgrade preserves original narration, facts, review, IDs and creation timestamps across repeated init. Focused recovery9: **22 passed**,0.48s. Final full backend10: **585 passed/1 optional skip**,41.50s,32 warnings, worker-thread warnings are errors. Actual PostgreSQL5: **112 passed**,3.34s (after snapshot/runtime changes; later identity CLI is SQLite-only). Earlier backend5:579 passed/1skip; newer tests account for the increase. Evidence artifacts/backup-recovery-backend10.*, artifacts/backup-recovery9.*, artifacts/backup-recovery-pg5/pytest.*. Runbook DATABASE_RECOVERY.md records exact local scope and unverified PostgreSQL recovery.
- C12 is still partial: PostgreSQL backup/restore, Linux/container execution, dependency/CI and broader crash/rolling-upgrade coverage remain. Archive remains deferred. No push/deploy/publication, paid provider call, account setting change or live database mutation. Continue original roadmap after this local commit; estimated-vs-invoice billing evidence is next.

## Billing estimates and owner-attested evidence stay distinct

- UsageEvent now records nullable cost_basis/reservation_id with additive migrations; existing rows remain legacy_unknown, never retroactively called invoices. BudgetReceipt usage explicitly labels configured_usage_estimate. A late configured estimate after owner reconciliation records its original amount but contributes zero new USD and preserves the owner's attested ledger amount. Disagreement conservatively pauses paid work for review; another explicit decision may change the amount. Lower/zero/equal/higher late estimates and duplicate receipts are covered. Cost summaries expose basis totals and state that provider invoices are not independently verified.
- Financial holds show ledger basis, preserved amount and separate usage estimate. Spending displays the local-estimate/owner-attestation limitation. Acknowledgment binds hold version, amount, receipt, rationale and interrupted-worker confirmation; edits clear it. The same reviewed submission retains its idempotency key on retry instead of inventing a new key after a lost response.
- Full backend3: **589 passed/1 optional skip**,44.00s,32 deprecation warnings; production build2 passed. Actual PostgreSQL3: **122 passed**,3.66s, now includes the independent interrupted/late billing contracts. PG per-case auth buckets reset just as SQLite test isolation does. Actual browser4: **1 passed**,2.7s, including acknowledgment reset after interruption/rationale edits and USD/credit reconciliation. Frozen source4bb31077f6f0b387cf92b00f1106079b2005f6eedb88bb2cd7602d5db240b06b; root scrapper-e2e-g4hxkz55, owned stack stopped.
- C11 is not actual invoice/API contract verification: costs and ceilings are local configuration, owner receipts are attestations. No paid request, invoice retrieval, account change, actual source-media transfer, push/deploy or publication occurred. Original roadmap continues; archive stays deferred. Media acceptance is next.

## Explicit exact-file export acceptance

- LongForm/Countdown no longer turn one click into blanket editorial approval. Private review endpoints return a fingerprint binding video/thumbnail bytes, script/plan, facts/research/shots, trailer/source context, creation time and complete inspection. Four explicit checks cover captions/timing/visibility or intentional absence, relevance/repetition, permissions, and complete narration/facts/packaging. Each measured warning/repeated visual needs a substantive decision; black and long-silence spans carry timestamps. Rights remain owner_attested with rights_verified_by_app=false. Final acknowledgment binds every input and resets on edits. Legacy single-click approvals require schema2 re-review. Publishing revalidates the same complete record; decode/research failures cannot be bypassed.
- Focused1:6 passed,1.21s; full backend3: **593 passed/1 optional skip**,44.54s,32 warnings; PostgreSQL3: **122 passed**,3.52s; production build2 passed. Actual browser4: **2 passed**,1.9min: complete LongForm plus Top5 five-segment pipelines, explicit per-finding acceptance/rights acknowledgment reset, final stitch/thumbnail update and simulated delivery. Frozen source9f50b32407bbc5c29d776dd1b64aa057dcad1c8d18b1a658c6a2838718db3c6c; root scrapper-e2e-s0yvubhg; owned servers stopped.
- Actual ten-minute procedural media corpus passed duration/decode/stream checks and timestamped black detection:600s,2,636,742bytes,SHA3d5486c63d64b2fd08d65140d21c59f66f562b41847930b25ca59f4fbddbbde2. Original generated black frames/tone only; this is objective long-duration QA, not footage relevance, speech/caption alignment or narrative-quality proof. Manifest artifacts/export-acceptance-corpus/manifest.json; explicit loudness/black warnings remain needs_owner_review. Native campaign acceptance and broader caption/semantic/editorial corpus remain open, so C05 is partial.
- No actual Drive transfer/source video download, paid call, public publication, account change, push or deployment. Archive remains deferred. Original roadmap work continues after this local save; additional native formats/manual exports are next.

## Native single-image, community and poll delivery

- Added validated native formats with their own copy: single-image text/caption, Reel caption, community body, and poll question/two-to-four distinct bounded options. A single image renders exactly one decoded1080×1350 artifact; explicit Instagram/Facebook image destinations use the existing review queue, preserving its caption and status review. No automatic Ready/schedule/publish transition.
- Community/polls create actual UTF-8 JSON/text files and one preview, display complete text/options, and require approval before private download. Manifests are manual_only/not_published. Their enqueue route rejects before any account/provider work; UI has no destination/queue controls. Owner-authenticated download rechecks exact hashes/inputs/approval; media passes cannot access private packages or leak them through the preview-file route. Tests verify no queue changes, corrupted-byte rejection, input bounds and actual downloads.
- Full backend4: **602 passed/1 optional skip**,49.91s,32 warnings. Actual PostgreSQL5: **136 passed**,8.97s, including native media/versioning/manual cases. Build5 passed; post-refinement focused5:14 passed,1.24s. Actual browser4: **2 passed**,18.5s, prior Short/carousel render/simulated delivery plus newimage/community/poll forms and downloads, frozen sourcec518340d5358aae1048f0dd26d373421e599e29b4d721eaecec6bdc2f8b6e782. Browser6 after width/status wording refinements: **1 passed**,2.7s, source64a940249156dfbc5c0742c5a4c9149473d4885d0a57212265000bee534838e6, root scrapper-e2e-vedvm2te. Owned stacks stopped; report/traces/screenshots/downloaded JSON/actual single-image PNG and verified SQLite snapshot saved under artifacts/e2e/history/native-formats6. Visually inspected actual image review screenshot; generated text fits the original card and explicit destination controls remain unchecked.
- C07 remains partial: a separate Reel caption does not prove a different visual treatment; recuts and broader native editorial acceptance remain. No live account/provider mutation, real social/Drive upload, paid call, push/deploy or actual source media download. Archive remains deferred. Continue original roadmap locally with asset indexing/retrieval next.

## Authorized video/audio asset catalog and response integrity

- Editorial Asset library now imports bounded owner-authorized local video/audio, normalizes/decodes bytes, records original/normalized SHA, stream/duration/size metadata, three timed video previews/difference hashes, owner tags/descriptions/people labels and ordered bounded owner caption cues. No model, face recognition, transcription, external download or publishing occurs. Native beat cards explicitly reject non-still media until a compatible treatment exists.
- Lexical tag/description/caption search streams rows with a bounded top result set and returns caption spans for playback; owner metadata remains private. Media/preview passes only reach binary routes. Optional immutable response hashes are verified under the response-lifetime file claim before first byte, including native media/manual files. A regression mutates after response construction and confirms409 before serving. Duplicate reuse rechecks retained media/previews; corrupted files cannot become a successful duplicate. Existing permission/annotation metadata is preserved.
- Initial focused1:22 passed across asset/native contracts. Added corruption test exposed cross-case reuse of deliberately damaged fixture previews; per-case generated tones now give independent bytes. Corrected focused6: **11 passed**,4.04s. Earlier full3:612 passed/1skip; final full backend7: **613 passed/1 optional skip**,50.61s,38 warnings. Actual PostgreSQL7: **147 passed**,9.05s,16 warnings. Production build7 passed. Browser4 had1 pass/1failure: exact select label lookup failed despite the visible control; explicit accessible select labels corrected it. Actual browser8: **2 passed**,6.0s, real original-media HTTP import/normalization/previews/playback, owner caption search/duplicate reuse and native private downloads. Sourcec515e49171e1b914b359eb427f9865519bdd64428ad96a3801aeae1b0e8ca30f, root scrapper-e2e-p3ttmqgi; owned servers stopped. Saved report/traces/screenshot/actual normalized media/provenance and verified DB snapshot under artifacts/e2e/history/asset-catalog8. Visually inspected the UI screenshot and its three distinct original sampled frames. Artifact copying resolved macOS /var and /private/var aliases before the containment check; no unrelated files were accessed.
- C06 remains partial: retrieval is explicitly lexical, captions/entities owner-supplied, video/audio compatible treatments/semantic beat selection and preservation masters remain. No paid calls, account changes, actual channel media/Drive transfers, push, deploy or public publication. Archive stays deferred. Continue original roadmap with audience adapters and content/performance lineage next.

## Read-only audience reports and immutable content/cost lineage

- Added explicit YouTube Analytics adapter using only an existing declared read-only OAuth grant, fixed GET reporting endpoint, header credentials, no redirects and no new auth flow/scope. Missing configuration/report/columns/data stay unavailable or missing. It probes the latest complete Pacific reporting day, then requests the aggregate through that day; provider averages are retained, fetch time is distinct from measurement age, unsupported metrics stay NULL and average-viewed percentage can exceed100. Scope/latency derives from official Google query/report/metric/dimension/data-model docs linked in AUDIENCE_LINEAGE.md. No real credential/account was used.
- ContentLineage is append-only in SQLite/PostgreSQL and versions owner-attested public ID↔exact retained input mappings. Source fingerprints/current IDs fence edits; correcting revokes the old read-only worker and prevents its next provider query or stale promotion. Retained source/cost evidence survives changes. Project-window costs include matched late billing adjustments and disclose estimates/owner attestations, reused-segment exclusions and unattributed native/delivery costs. No invented free-cost or invoice claim.
- Provider revisions, including reverting to earlier totals, retain history while active learning uses the latest fetch at each reporting boundary. Superseded mappings are excluded; cohorts/experiments separate accounts in addition to platform/format/age. Owner observation imports can link the exact current mapping; mismatched identity/format/time rejects. UI previews retained bytes, binds mapping acknowledgment to all inputs, preserves timestamp precision and offers explicit read-only sync/correction. Pages do not auto-query providers.
- Focused7: **22 passed**,0.92s. Full backend8: **623 passed/1 optional skip**,49.39s,38 warnings. Actual PostgreSQL8: **157 passed**,10.16s,16 warnings. Production build9 passed. Browser8 initially failed on changed account table text and a repeated-edit textarea label; account grouping remains visible and the mapping textarea now has an explicit stable label. Corrected actual browser10: **2 passed**,13.4s, original Short/carousel/feedback/experiment UI and exact native-image lineage, bound acknowledgment, two-query processed-day fixture and preserved mapping correction. Frozen source/hash/root stored in artifacts/e2e/history/audience-lineage10/stack.json; reports/traces/screenshots and verified synthetic DB copied there, owned stack stopped. Analytics fixture must be explicitly enabled, is labeled fixture_only and makes zero real account/provider calls. No actual media/account mutation, paid call, public publication, push/deploy or source archive resumed.
- C08 remains partial: actual private credential/report proof, IG/FB adapters, native/delivery cost attribution, broader history/source pagination and provider refresh/retention operations remain. Original roadmap continues after this local save; archive stays deferred. Native per-export cost references and private comment/correction lineage are next.

## Native per-export metering references

- New native renders bind the cost operation to a unique native:campaign:export request reference and keep that reference in the exact media manifest. Content lineage now attributes metered variable-cost events and unsettled attempts for that one export, including later matched adjustments, instead of blending re-renders. Legacy native/delivery sources remain unattributed; no events remain NULL rather than an invented free-production claim. Fixed hosting/local processing and actual invoice proof remain outside this ledger.
- Focused1: **25 passed**,1.50s, including two actual original-image renders with separate synthetic0.012USD metering events and disjoint event IDs. Full backend2: **624 passed/1 optional skip**,49.02s,38 warnings. Actual PostgreSQL2: **158 passed**,8.56s,16 warnings. Frontend unchanged from passed build9. Actual native-cost browser2: **1 passed**,2.0s, exact native source mapping, fixture processed-day analytics and correction. Frozen manifest/report/traces/screenshot/verified synthetic DB saved under artifacts/e2e/history/native-cost2; owned stack stopped. No actual paid call, account access, source video/Drive transfer, public post, push or deploy. Archive stays deferred.
- Continue original roadmap after this local commit with private comments, thread/reply ingestion, context-bound reply drafts and claim/export correction linkage. Official Google commentThreads/comments list references have been checked; no comment provider request has occurred.

## Private comment pages, exact-context drafts and correction proposals (verification in progress)

- Original roadmap resumed; archive/login remains deferred. Added explicit read-only YouTube comment/reply pages with separate durable cursors, immutable redacted source evidence, resource validation, duplicate/repeated-token protection, stop/restart and mapping-correction fencing. Source edits preserve private owner words while invalidating exact-context review. Append-only private decisions bind creation identity, latest source/thread/mapping/coverage and draft; no send/moderation/public endpoint. Corrections target exact retained claims/narration/native beats and create only unverified source-review drafts. Exact proposal retries share the same receipt/follow-up.
- Focused rejection tests exposed a lease-cleanup deadlock: exception exit tried to release the lease using a different writer while the endpoint still held its transaction. Opt-in rollback now runs before scope cleanup for comments, audience sync and native rendering. Stale cursor and late stopped-response tests pass in SQLite and actual PostgreSQL.
- Latest full backend2:638 passed/1 optional skip,47.69s,38 deprecation warnings; PostgreSQL2:172 passed,8.95s,16 warnings. Production build10 passed. Focused8 hit a test-only random-SHA collision with the three digits555; the privacy assertion now checks the full phone globally and partial digits only in human text/drafts, preserving privacy coverage. Focused9:16 passed,1.20s.
- First actual comment browser1 passed,4.1s, source58d3a2975949b7bcb34c6dbf58a4e1b789f258259f09bddc9ce46f3eca7d5004: resume after reload, page/reply dedup, contact exclusion, browser draft recovery, acknowledgement reset, source-edit review invalidation and exact-source proposal. Source image and UI screenshot visually inspected; original procedural image only. Follow-up browser2 is running the full16-scenario revival suite, including new failed-storage internal-tab navigation protection. Do not count that run until its report finishes. Synthetic provider boundary only; zero real account access, paid request, Drive/source-media transfer, public post, push or deployment.

## Private comments and full regression verified

- Final full backend3: **638 passed/1 optional skip**,55.11s,38 deprecation warnings. Actual PostgreSQL3: **172 passed**,7.81s,16 warnings. Focused10:16 passed,1.24s; production build11 passed. API key now uses Google’s documented header instead of a URL query; the HTTP boundary test verifies no secret in INFO request logs, no redirects and sanitized errors.
- Full actual browser3: **16 passed,3.9min**, including complete LongForm and Top5 actual renders/inspection, native Short/carousel/image/manual formats, independent source review, local worker handoff, publication/financial reconciliation, failed-storage draft guards, retained media after project/queue deletion, asset catalog, audience mapping and comment/correction workflow. Earlier browser2:15 passed/1 failure on an unhandled rate-limited test detail read; the test now waits Retry-After and explicitly retries that read without weakening the real limiter. Browser3 exercised the real60s rate-limited recovery. Settings no longer says Invalid Date for unscheduled entries or promises automatic remote backup deletion/lock-free restore.
- Frozen source8985efa2da3f124500a8cb213f4e90eaa0643063cb6032313cd85d43ec10fffc; rootscrapper-e2e-7kpent5w; own servers stopped. Manifest/report/traces/screenshots/quality/probes and verified synthetic DB retained under artifacts/e2e/history/comment-workflow3. Screenshot of the actual private thread/source correction UI inspected. Real comment/account authorization, provider refresh/deletion lifecycle and deeper semantic reply assistance remain unexecuted/open; all public actions remain gated.
- MEMORY.md and local commits continue; no actual API spend, archive transfer, public post, account/permission change, GitHub push or deployment. Original roadmap continues with C10 descriptive diagnostics and explicit recommendation review.

## Descriptive diagnostics and exact learning-decision experiments

- Added private per-content leave-one-out comparisons by account/platform/native format/age, metric-specific missingness/sample counts, separate impressions/CTR/aggregate viewing signals, explicit opening/repeat-viewer unknowns and published review heuristics (five peers/0.75 ratio; not significance/causality). Cohort summaries and diagnostics share nearest-age/later-boundary selection. Float measurements reject boolean/numeric-string coercion. Recorded variable-cost ratios require known bases/no unsettled snapshot attempts/positive denominators and remain mapping-time estimates, excluding fixed costs/current invoice/profit. Missing costs remain unavailable; tiny positive costs are not displayed as zero.
- Append-only LearningDecision stores exact observation/peer/source/cost/policy context. Input-bound acknowledgment, stale409, retained browser rationale, private history and idempotent receipts verified. Only the latest current plan can seed a manually completed experiment; registration binds original account/format/age and exact decision/version. Later rejection/stale evidence blocks new registration, while exact retries recover the old receipt. Experiments expose selected observation keys, unknown publication timing and no automatic winner. No decision grants spend/publication/replies or changes existing automation.
- Focused7:22 passed,0.78s. Full backend3: **648 passed/1 optional skip**,52.57s,38 deprecation warnings. Actual PostgreSQL3: **180 passed**,11.25s,16 warnings. Production build4 passed. Browser1 found missing accessible experiment-select labels; fixed. Browser2 found a test selector matching both unsaved-draft status and the expected stale-review status; the test now identifies the expected message without removing either owner-facing state. Final actual browser3: **4 passed,16.8s**, existing original Short/carousel/feedback/experiment flow, native audience mapping, comments and new diagnostics→review→seed→registration→stale rejection/history. Screenshot visually inspected.
- Frozen source72509844586fce87b95f85bd8bf2a1a262d1ccb296c49e1f31b830e8e67fb2e9; rootscrapper-e2e-_flye1z5; own servers stopped. Manifest/report/traces/screenshot and verified synthetic DB saved under artifacts/e2e/history/learning-diagnostics3. All performance observations and provider boundaries synthetic; no real account/metric validation, invoice proof, causal winner, beneficial growth outcome or automated execution claimed. Original archive remains deferred; no source/Drive transfer, paid call, public post, account change, push or deployment. Continue original roadmap with PostgreSQL recovery/compatibility and remaining UX/media work.

## Native PostgreSQL logical recovery drill

- The existing isolated PostgreSQL harness now requires a real custom-format dump/restore drill after its behavioral corpus. The new verifier accepts only its owner-private marked /tmp/scpg-* root, exact synthetic database and private Unix socket; other hosts/URLs/sockets, writable/symlinked roots, source writes and unexpected role/database names are rejected before connecting. Native commands/SQL waits are bounded, source dump sessions enforce read-only, private archives have SHA-256 and atomic no-replace publication. Evidence directories are unique per fixture run.
- Actual restore uses a newly created unprivileged app role (no superuser/create-role/create-database/replication privileges) and new owned fixture databases. All 41 application tables, 424 rows and 17 sequence configurations/states matched semantic data/schema/constraints/index/enum/function/trigger hashes. An equal-row-count JSON mutation was detected; restored append-only learning evidence rejects deletion with SQLSTATE23514 and rolls its sentinel back. Truncated custom archives and a deliberate late SQL division-by-zero both reject and leave the entire failure target empty. Source fingerprint stayed unchanged.
- Guard2:6 passed,0.10s. Full backend1: **654 passed/1 optional skip**,48.45s,38 deprecation warnings. Actual recovery4: **180 PostgreSQL contracts passed**,8.05s,16 warnings, plus the native recovery drill **0.84s**. Archive161656bytes, SHA75a4044ab5351ccff9d7630dfdb11ff57e8106103a6668d6b1fa7fa67d51594f; manifest/Users/kwasiyeboah/dev/scrapper-worktrees/revival-20261018/artifacts/postgres-recovery4/restore-scpg-f9efl_cb/restore-manifest.json. All four actual recovery attempts passed; later runs add content-mutation, full sequence and least-privilege checks. Owned cluster stopped and its successful marked fixture removed; private dump/log/fingerprint evidence retained.
- This is a local tiny synthetic PostgreSQL16 drill, not production RTO, provider backups, remote retention, roles/ACL/tablespace/large-object/media coverage or Linux/CI execution. Automatic app backups remain SQLite-only and explicitly unsupported for PostgreSQL. DATABASE_RECOVERY.md now links official PostgreSQL dump/restore docs and separates these operational limits. No live database/account/permission was changed, no paid API, push, deployment, public post or deferred archive operation occurred. Continue original roadmap with remaining UX/compatibility/media tasks.

## Owner recovery and compatibility verification in progress

- C13 adds bounded typed/readable API errors (validation inputs/context and proxy HTML excluded), origin/token-scoped refusal caching, and browser draft recovery for authorized-comment imports and creative experiment forms. Draft controls explicitly cannot submit their enclosing forms. Confirmed saves are distinguished from later read-refresh failures; a dedicated refresh retries the read. Linked experiment draft metadata survives reload and is revalidated by the backend. Six pure frontend error tests pass; Node reports its typeless-module warning, not a functional failure. Production build3 passes.
- Focused browser2:3 passed,3.9s: linked diagnostic experiment recovery; import/experiment navigation/reload/error/storage-failure recovery; switching away from a refused API origin. Browser1 had2 pass/1 label-lookup failure despite correctly retained words in the screenshot; explicit textarea labels fixed it. Full19-scenario browser3 is running against frozen pre-compatibility backend source36ad94576497739960749f1a85ae2b4fee8e54e097c6ae0f0ad5e290eb7c4ed0. Do not count that run until its report finishes. Private artifacts for focused browser2 are under artifacts/e2e/history/owner-recovery2; its server was stopped.
- Independent C12 compatibility work is currently uncommitted: Pydantic ConfigDict replaces five legacy class Config blocks; supported Pillow get_flattened_data replaces deprecated pixel iteration; FastAPI lifespan wraps the same initialization/shutdown helpers. Initialization failure still prevents serving; shutdown still marks interrupted jobs. Focused actual HTTP/backup/asset contracts:28 passed,1 TestClient warning,4.63s. Full backend and actual PostgreSQL/native restore reruns are running. The owner-recovery browser uses a frozen backend and is unaffected by these concurrent source edits. Commit C13 frontend/docs separately after its full run, then finish compatibility verification in a fresh stack.
- No live source/Drive transfer, provider credential use, API spend, public action, account setting change, push or deployment. Archive remains deferred; all work is on the isolated local branch. Continue after local checkpoints.

## Owner recovery verified locally

- C13 adds bounded readable typed API errors, origin/token-specific refusal caching, recovered comment-import and experiment forms, exact linked-template metadata, non-submitting recovery buttons, confirmed-save/read-refresh separation and retained queue snapshots/edit state during unavailable reads. Queue failures no longer masquerade as an empty healthy list; successful current reads restore controls. Background generations reject stale pipeline responses. Production auth and request ceilings remain unchanged.
- Frontend error unit3: **6 passed**,114.49ms; production build6 passed. Focused browser4: **4 passed,1.1min**. Final full browser5: **20 passed,3.9min**, including real original-media LongForm/Top5/native rendering and the real limiter429/Retry-After/60-second recovery. Exhaustion is fixture-seeded, not a600-request load benchmark. Independent cases reset only fixture request buckets. Prior full browser3 had16pass/3fail from sharing the same compressed request window; failures/reports remain retained. The failures exposed silent queue reads, now covered by the dedicated recovery case.
- Frozen backend/scripts source e6214eab0d40e4d396422f5af813f17962cb09a205467e514b2a8e9deef410e0; owned root scrapper-e2e-08yqswnf stopped. Stack/report/traces/screenshots and verified SQLite snapshot saved under artifacts/e2e/history/owner-recovery5. All provider/publication boundaries synthetic; no paid calls, real account mutation, public post, push, deploy or deferred archive operation. Other forms/status contracts remain open; original roadmap continues after this local commit.

## Supported lifecycle and library interfaces verified

- FastAPI now uses its supported lifespan interface while retaining fail-closed pre-migration initialization and interrupted-job shutdown marking. Two real TestClient lifespan tests cover successful serving/init/shutdown exactly once and failed startup never serving. Five ORM response schemas use ConfigDict; Pillow difference hashing uses the installed pinned12.3 supported flattened pixel interface. Existing data fields/validation and visual hash tests remain intact. Official references: https://fastapi.tiangolo.com/advanced/events/ , https://docs.pydantic.dev/latest/api/config/ , https://pillow.readthedocs.io/en/stable/reference/Image.html#PIL.Image.Image.get_flattened_data .
- Focused1: **28 passed,4.63s**. Full backend1: **656 passed/1 optional skip,50.24s**. Actual PostgreSQL1: **180 passed,9.42s**, plus native backup/least-privilege transactional restore **passed,0.9s**,161617bytes,SHA40e70e010f7f642e0b2403746a7f437ae5d6be6e517348280874dd5469cf2d7e. One remaining TestClient/httpx deprecation warning is reported, not suppressed; shared runtime was not mutated. Actual full owner-recovery5 browser run **20 passed,3.9min** used a fresh lifespan-managed ASGI stack and original generated media with these interfaces. No separate redundant media run was needed; source/evidence retained in the preceding entry.
- Changes and evidence remain local, no account/provider/paid/public operations or deployment. Archive remains deferred. Continue original roadmap with native exact-export acceptance and current architecture/documentation; no completion or production recovery claim.

## Native exact-file editorial acceptance and format-correct delivery

- Native exports now use structured schema2 acceptance with four format-appropriate checks, full-input/file/manifest/inspection/check-policy fingerprint, individual warning/timed black/long-silence/repeated-source-image decisions and substantive rights/intended-use rationale. This remains an owner attestation, never automated fact/license clearance. Private review uses owner auth; legacy blanket approvals and stale/missing/duplicate findings/checks reject. Approval uses database clock and exclusive media claims. Enqueue, private manual download and publication revalidate current evidence; transport rechecks under the media claim. Manual formats remain impossible to enqueue/publish.
- Reused the Studio/Countdown validator and accessible review UI without changing their required checks. Native inspection/copy changes invalidate prior approvals and previously queued fingerprints. Removed the one-click native client API. Fixed an additional publisher mismatch: image/Reel comparisons now use their own caption and correct image/video kind. Actual original-image and generated-tone Reel render/queue tests reach only mocked transports and preserve exact caption/media. Tone replacement is not speech-quality proof.
- Focused2:22 passed,2.24s; final5 new contracts:5 passed,2.63s. Full backend1: **661 passed/1 optional skip,60.56s**,1 TestClient/httpx warning. Actual PostgreSQL1: **185 passed,10.83s**,1 warning; native custom backup/least-privilege transactional restore **passed,1.118s**,165885bytes,SHAab7fce55f44171c5106e547de9132eff00ee3b6e6eb6be60eca69bcbbcfd877f, source unchanged. Production build1 passed. Actual browser1: **4 passed,2.2min**, full LongForm/Top5 and Short/carousel/image/community/poll review, generated media, simulated delivery and private downloads.
- Visual-capture browser2 reused an already-approved synthetic opportunity in the same fixture DB and timed out on its correctly disabled approval button; retained as a failure, not an application regression or pass. Fresh isolated browser3: **1 passed,3.7s**. Poll acceptance screenshot visually inspected: silent/manual wording, rights attestation and input-bound acknowledgment are visible and readable. Frozen source d1e6c1a4eff1c46fb56594de496936f9ee7b437c8a7f546ba48be4f526105e25. Owned roots scrapper-e2e-yy4oe764 and scrapper-e2e-ida8zytc stopped; manifests/reports/traces/quality/probes/screenshots and verified DB snapshots saved under artifacts/e2e/history/native-acceptance1 and3; failed2 retained separately. Original generated exports remain on disk.
- C05/C07 still partial: word alignment/OCR/semantic scene quality, recuts/deeper Reel treatment and compatible video/audio assets remain. No paid API, real source/Drive transfer, public upload/reply, account change, GitHub push or deployment. Archive remains deferred. Continue original roadmap after this local commit; current handoff documentation reconciliation follows.

## Current architecture and handoff reconciled

- Replaced stale542/466-test checkpoint descriptions in the current architecture, completion-status index and resume instructions with implemented research/media/editorial/publication/budget/lease/storage/recovery/audience/comment/learning/owner-UX flows, actual evidence paths and explicit remaining C01–C14 limits. Added current whole-system and evidence relationship diagrams. Older results remain in Git/progress history rather than masquerading as current capability.
- Authority is explicit: original local roadmap continues without checkpoint approval; MEMORY.md/local commits continue. Earlier push/deploy/live-test authorization is conditional on completion, not yet met. No main merge/public post/account change/paid call is implied. The owner's latest instruction keeps the entire Shorts archive and Drive authentication deferred. ActualPG recovery is distinguished from still-unrun production/Linux/CI operations; implemented read-only provider adapters are distinguished from unverified real grants/reports. No completion or causal/creative-quality claim.
- This is documentation-only reconciliation against verified local code,304b5ae and existing exact test artifacts. No redundant test rerun or external operation. Continue with remaining compatible asset/treatment and editorial/UX contracts.

## Selected native video intervals — local verification in progress

Added explicit authorized catalog-video ranges (0.25–12seconds), owner-selected hold/loop, silent retained excerpt provenance, fitted full-frame video insets, actual presentation/interval hashes, exact-acceptance findings and shared source/output claims through promotion. Short/Reel-only mixed-format restriction and unsupported audio-as-visual reject explicitly. Inputs recheck after claims/before promotion; a changed source cannot become a successful export. No automatic source audio reuse or paid/external call. NATIVE_CLIPS.md documents constraints and remaining creative/audio scope.

Initial focused1 retained31pass/3test failures: FFmpeg test timings omitted leading zero, and the conflict test inherited the same job's intentionally reentrant file-claim context. Corrected the fixture timing and used a suspended claim context to represent an independent mutator without altering production locks. Focused2: **34 passed,11.32s**, including delivered moving/held frame comparisons and actual750Hz narration/1900Hz source-audio exclusion. Full backend/actualPG+native recovery/build2 and actual selected-video UI are next; do not count incomplete runs. Original archive remains deferred; no push/deploy/public/account action. Continue original roadmap.

## Authorized selected-video native treatment verified

- Catalog videos now supply explicit0.25–12second intervals for Short/Reel-only campaigns, with chosen hold/loop and source audio excluded. Complete source frames are padded into the original card inset; narration remains separate and untrimmed. Silent selected excerpts retain source/range/hash/duration/treatment provenance, are checked alongside final bytes, and extended hold/loop choices require individual exact-acceptance decisions. Still/manual/image campaigns and audio-as-visual reject incompatible inputs. Rendering now pins all selected source paths/output root, rechecks after claims and before promotion, and blocks changed-source promotion.
- Focused2: **34 passed,11.32s**, including actual moving-vs-held delivered-frame checks, video-only excerpts, two-frequency final audio check, independent conflicting claims, corruption and failed-promotion evidence. Full backend1: **665 passed/1 optional skip,64.17s**,1 TestClient/httpx warning. Actual PostgreSQL1: **189 passed,20.28s**,1warning; actual least-privilege transactional native restore **passed,1.115s**,170608bytes,SHAb34eff81f27c2d15893b157b041d2f72a46bd43a55f702d8e9ef37af9360eaad, source unchanged. Production build2 passed.
- Actual browser1:3 existing native/media workflows passed,1 new-test selector failure (the intended alert and Next hidden route announcement both matched). Narrowed the test to the intended warning; no production guard changed. Fresh browser2 selected-video flow passed,6.9s. Added real browser playback/seek proof because its first screenshot caught preload. Fresh browser3: **1 passed,8.0s**, actual UI media import, explicit range/policy, incompatible-format control, offline speech/render, intentional-loop decision, playback/time advance and seek, exact acceptance and review-only enqueue; zero additional fake-provider submissions. Screenshot visually inspected at1.5s: blue selected source frame appears inside the original card with intact text and controls; acceptance shows the loop finding and substantive rationale. No human speech audition/semantic-rights proof is claimed.
- Frozen source54a84d361118738f2565ef4548a5add4e475b11e6e1fce928f2e0654f2dd5d41; owned roots scrapper-e2e-wixijaad, scrapper-e2e-y6dqtxvi and scrapper-e2e-ix9j3gz2 stopped. Evidence retained under artifacts/e2e/history/native-clips1/2/3, including reports/traces, actual final video/selected silent excerpt/manifests/screenshots and verified SQLite snapshots. Current root is recorded exactly by native-clips3/stack.json. Earlier failed test evidence remains retained; generated sole-copy media was not deleted.
- C06/C07 remain partial: compatible music/audio treatment, semantic retrieval/identity, preservation masters, deeper recuts/Reel composition, word alignment and real editorial examples remain. No paid call, source-channel archive/Drive transfer, public upload/reply, live account/settings mutation, GitHub push or deployment. Continue original roadmap locally after this save; remaining native/form persistence and audio/media contracts are next.

## Native treatment draft recovery — verification in progress

Native campaign fields now use backend-origin-scoped browser recovery for title/research version, all format copy, selected formats, beat/source/asset choices and explicit video ranges/end policy. Malformed cached structures remain visible/private and cannot submit/crash the editor. Recovery/discard buttons remain non-submitting. Restored research fingerprints are checked against current approved context; owner reselects reviewed context before save. The new client submits expected_research_fingerprint for server409 on changed research; this is additive compatibility, legacy API clients may omit it but still require current approved research and downstream input rechecks.

Confirmed create receipts clear only the mounted editor; late responses from unmounted editors cannot erase a newer remount's draft. Inputs are disabled while the mounted save runs. Confirmation and failed read-refresh are reported separately; explicit Refresh native campaigns is read-only. Failed/unconfirmed requests retain words. Focused backend1:10 passed,8.77s; build3 passed. Full backend/actualPG/native recovery and focused actual UI, delayed responses, all-copy/clip recovery, stale research,422/network errors, storage failure and malformed cache are being verified. Do not count incomplete runs. No paid/public/account/push/deploy/archive action; original roadmap continues.

## Native treatment recovery and full22-scenario regression verified

- Native campaign drafts now persist backend-origin-scoped copy for all six formats, formats/beat/source/asset selections, video ranges and hold/loop policy. Explicit recovery/discard never submits; malformed structures remain private/readable and cannot crash/submit the editor. Current approved research must match the restored selected fingerprint or receive explicit current-context review. New UI supplies an expected research fingerprint and server409 rejects a changed version; compatibility clients can omit this additive field but still require approved research/downstream input checks. This does not claim full independent material-fact verification.
- Confirmed create clears only its mounted form; failed/unconfirmed requests retain words. Mounted saves disable edits. Unmounted editors cannot clear or refresh over a newer remount's words when late responses arrive. Confirmed mutation and failed read-refresh have distinct messages; read-only Refresh native campaigns does not retry a write. Failed browser storage protects internal navigation; malformed cache recovery stays private. Still/file-import and other forms remain outside this slice.
- Focused backend1:10 passed,8.77s. Full backend1: **666 passed/1 optional skip,66.02s**,1 reported TestClient/httpx warning. Actual PostgreSQL1: **190 passed,19.73s**,1warning; actual native custom dump/least-privilege transactional restore **passed,0.819s**,171292bytes,SHA9645c2c13ad02b723a7fd15de5399e3f94f46656c2a09132ed2344a5be368c7d, source unchanged. Production build3 passed.
- Browser1:3 native/media workflows passed,1 test failure because it searched for “unconfirmed” while the correct UI text was “could not be confirmed.” Corrected the assertion without changing the owner message. Fresh focused browser2: **1 passed,3.5s**, all format/clip recovery, stale source,422/network errors, real delayed successful create after unmount, newer words retained, two distinct manual saves, confirmed save/read failure, storage Cancel and private malformed cache. Screenshot visually inspected; all copy remains readable and creation is disabled for malformed/stale input.
- Final full actual browser3: **22 passed,3.9min**, full LongForm/Top5/native/private downloads, independent source review, local worker handoff, publication/financial recovery, retained versions/delivery, browser draft conflicts/storage guards, authorized catalog, audience/comment/correction/diagnostics, origin auth, real60-second limiter recovery, video playback/seek and new native recovery. Frozen backend/scripts source87cae4a6a3b3d498a4509c0c9b4deb79994e9e80ad459c1c99fe5bde39a47cc2; owned root scrapper-e2e-36ik2ss8 stopped. Reports/traces/screenshots/actual original media/selected excerpt/quality/probes/manifests and verified synthetic DB saved under artifacts/e2e/history/native-drafts3; focused2 and failed1 retained separately.
- C13/C14 remain partial: other form persistence/status/negative cases and real editorial/provider/Linux/CI proof remain. No actual source-channel archive/Drive transfer, paid API, public upload/reply, live account/settings mutation, GitHub push or deployment. Original archive/login remains deferred. Continue original roadmap after this local commit with explicit authorized background-audio treatment and remaining media/research/UX contracts; do not stop at the checkpoint.

## Explicit authorized native background audio — verification in progress

Added per-beat indexed audio selection with explicit0.25–12second source range,−36..−12dB relative level and silence/loop end policy; only Short/Reel campaigns admit it. Source video audio remains excluded. All narration stems in an audio-enabled campaign target the same reference; background level applies after stem targeting, fades at beat end, and final mix retains existing full QA. Silent/no-background legacy rendering is unchanged. Retained excerpt/source/range/level/mix provenance joins exact acceptance and file claims. Every background choice needs its own substantive finding decision. Native browser recovery includes the new audio fields. No automatic semantic music selection, speech ducking or license verification.

Focused2:20 passed,20.34s; after all-narration reference consistency focused3:5 passed,10.42s. Full backend1:671 passed/1optional skip,70.99s; actualPG1:195 passed,30.58s; native least-privilege restore passed0.836s,175838bytes,SHAbd8814ef0e669dc3f6859bb46ec1cd961553f44cfbf219ddc10b8189c6100ff9, source unchanged. Each Python lane reports1 TestClient warning. Frontend build1/tsc1 passed. Actual browser1:3 passed (default media/manual flows and new audio-choice recovery),1 repeated-import label failure at the second rights textarea. Explicit accessible labels now fix both repeated metadata textareas; build2/fresh combined UI verification are next, not counted yet. Original archive remains deferred; no paid/public/account/push/deploy action. Continue original roadmap.

## Explicit authorized background audio verified locally

- Short/Reel beats now admit separately indexed authorized audio with explicit source range0.25–12seconds, relative level−36..−12dB and silence/loop ending. Visual video audio remains excluded. Audio-enabled campaigns target consistent narration stems, apply chosen background reduction after stem targeting, fade background at beat end, and retain full final QA. Source/excerpt/range/gain/normalization/mix policy and frozen provenance bind exact acceptance. Every track needs its own substantive finding decision and owner rights attestation. Source claims span rendering/promotion; missing/changed originals or retained excerpts reject admission. No automatic music selection, speech ducking, seam-aware musical matching or legal-clearance claim.
- New native browser recovery includes audio ID/range/level/ending. Incorrect kind, incomplete choices, excessive range/gain and incompatible image/manual formats reject. Actual tonal contracts check foreground dominance, loop versus silence, same-voice consistency within3dB across beats and exclusion of a separate video soundtrack in a combined visual/audio render. Known generated750/1100/1900Hz signals are objective fixtures, not music/dialogue quality proof or human audition.
- Focused2:20 passed,20.34s; final audio-focused3:5 passed,10.42s. Full backend1: **671 passed/1 optional skip,70.99s**,1 TestClient/httpx warning. Actual PostgreSQL1: **195 passed,30.58s**,1warning; native custom dump/least-privilege transactional restore **passed,0.836s**,175838bytes,SHAbd8814ef0e669dc3f6859bb46ec1cd961553f44cfbf219ddc10b8189c6100ff9, source unchanged. Frontend build2 and tsc1 passed; no dependency/runtime mutation.
- Browser1:3 passed (native defaults/manual formats and audio-choice draft recovery),1 repeated-import label failure. Added explicit accessible rights/description textarea labels. Fresh actual browser2: **2 passed,10.3s**, catalog import/caption retrieval/dedup plus actual video+audio UI imports, explicit ranges/level, offline-speech render, playback/seek, both exact review findings and review-only enqueue with zero additional provider submissions. Acceptance screenshot visually inspected; independent visual-loop and audio decisions are readable. Full22-browser baseline remains the prior native-drafts3 run; this later scoped change has the completed native/copy/import cases above, not a falsely claimed newer full22 run.
- Frozen source0d1bc77c485bb79d66222829505b1fc6d502566754d1d3928e751ab8c66a99c6; owned root scrapper-e2e-wtsba2mu stopped. Reports/traces/screenshots/actual mixed final video/visual and audio excerpts/original generated sources/normalized catalog bytes plus SHA index/manifests/verified DB snapshot saved in artifacts/e2e/history/native-audio2; failed1 retained separately. No actual source-channel archive/Drive transfer, paid request, public upload/reply, account/settings mutation, push or deployment. Archive stays deferred. Continue original roadmap locally with asset-import metadata recovery, truthful confirmed-upload/read-refresh state and remaining media/research contracts.
