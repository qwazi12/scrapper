# Scrapper & SocialPilot Operator Runbook

This document describes everyday operational procedures, failure mitigations, disaster recovery, and maintenance workflows for Scrapper and SocialPilot.

---

## 1. Fast Reference & Dashboards
- **Web UI (Production)**: [https://scrapper.nodepilot.dev](https://scrapper.nodepilot.dev)
- **API Server (Railway)**: `https://scrapper-production-d348.up.railway.app`
- **Health Check**: `GET /api/health`
- **Volume & Disk Status**: `GET /api/system/disk`
- **Database Backup Status**: `GET /api/backup/status`
- **Live SSE Event Bus**: `GET /api/events`

---

## 2. Pause / Resume Operations

### A. Pause or Resume SocialPilot Auto-Posting
1. **Via UI**: Navigate to **Settings Overview** -> **Posting Schedule** -> click **"⏸ Pause Auto-Posting"** or **"▶ Resume Auto-Posting"**.
2. **Via API**:
   ```bash
   # Pause
   curl -X PUT "https://scrapper-production-d348.up.railway.app/api/autopost?paused=true" \
     -H "x-access-token: $ACCESS_TOKEN"

   # Resume
   curl -X PUT "https://scrapper-production-d348.up.railway.app/api/autopost?paused=false" \
     -H "x-access-token: $ACCESS_TOKEN"
   ```

### B. Stop Running Jobs Mid-Flight
1. **Via UI**: Click **"🛑 Stop"** in the top navigation or on any active job card.
2. **Via API**:
   ```bash
   curl -X POST "https://scrapper-production-d348.up.railway.app/api/jobs/<job_id>/stop" \
     -H "x-access-token: $ACCESS_TOKEN"
   ```
   *Note*: Stopping cleanly terminates child processes (ffmpeg/yt-dlp) and resets state without leaving orphan files.

---

## 3. Database Snapshots & 1-Click Restore

### A. Automated Nightly Snapshots
- Runs daily at **3:00 am Eastern**.
- Performs a live, non-blocking `sqlite3.Connection.backup` with automated `PRAGMA integrity_check`.
- Compresses with gzip (`.db.gz`), computes SHA-256 fingerprint, and enforces **7-day local disk retention** on Railway and **30-day off-disk retention** in Google Drive.
- Alerts if no successful backup has run in over 26 hours.

### B. Manual Backup Snapshot
- Click **"💾 Backup Now"** on the Settings page, or call:
  ```bash
  curl -X POST "https://scrapper-production-d348.up.railway.app/api/backup/now" \
    -H "x-access-token: $ACCESS_TOKEN"
  ```

### C. Safe Database Restore Procedure
The restore CLI (`scripts/restore_db.py`) prevents accidental overwrites:
1. **Dry-Run Inspection** (compares row counts across all user tables):
   ```bash
   python scripts/restore_db.py --from /data/backups/scrapper_YYYYMMDD_HHMMSS_tag.db.gz
   ```
2. **Execute Confirmed Restore**:
   ```bash
   python scripts/restore_db.py --from /data/backups/scrapper_YYYYMMDD_HHMMSS_tag.db.gz --confirm
   ```
   *Note*: `--confirm` automatically creates a safety snapshot (`scrapper_pre_restore_<ts>.db.gz`) before restoring.

---

## 4. Disk Space, Retention & Cleanup

### A. Built-in Disk Protection Guardrails
- **Intermediate Render Cleanup**: All ProRes 4444 `.mov` overlays, temporary audio mixes, and segment lists are deleted immediately after video compositing.
- **LRU Cache Auto-Pruning**: Motion graphics cache (`/data/studio/_motioncache`) is capped at 400 MB; TTS voice cache (`/data/studio/_ttscache`) is capped at 200 MB. Oldest entries are evicted automatically.
- **Rolling 5-Day Retention Sweep**: Purges un-queued videos older than 5 days every 30 minutes.
- **SocialPilot Exemption**: Items in the SocialPilot queue (`review`, `ready`, `posting`, `posted`, `retry`) are strictly exempt from retention deletion.
- **Hard Refusal Limit**: If volume disk usage reaches **90%**, new studio renders and bulk downloads are refused with HTTP 507 to prevent container crashes.

### B. Trigger Manual Cleanup Sweep
- Click **"🧹 Run Cleanup Sweep"** in Settings -> Disk Space, or call:
  ```bash
  curl -X POST "https://scrapper-production-d348.up.railway.app/api/cleanup/now" \
    -H "x-access-token: $ACCESS_TOKEN"
  ```

---

## 5. Troubleshooting & Common Failure Modes

| Issue / Symptom | Probable Cause | Mitigation / Fix |
|---|---|---|
| **HTTP 507 Insufficient Storage** | Volume usage >= 90% | Run `POST /api/cleanup/now`. If still above 90%, remove old completed clips or increase Railway volume size. |
| **YouTube 403 / "Sign in to confirm you're not a bot"** | Datacenter IP flagged by YouTube | Set `YTDLP_PO_TOKEN` in Railway environment, or push fresh browser cookies via `python scripts/sync_cookies.py`. |
| **Google Drive Upload "storageQuotaExceeded"** | Uploading to personal Drive instead of Shared Drive | Create a Google Shared Drive, add the service account as Content Manager, and set `LONGFORM_DRIVE_FOLDER_ID`. |
| **Server Restart / Deploy interrupted a job** | Container rebooted mid-work | The self-healing worker automatically resets running clips, re-queues incomplete ingest jobs, and resumes from the exact link where it left off. |

---

## 6. Deployments & CI/CD
- **Frontend to Vercel**: Automatically deployed via GitHub Actions (`.github/workflows/deploy-frontend.yml`) on git push to `main`.
  - Requires `VERCEL_API_TOKEN` (or `VERCEL_TOKEN`) in GitHub repository secrets.
  - Working since 2026-10-03: the `VERCEL_API_TOKEN` repo secret was added, and run 37149883106 deployed every step (verified as a new Ready production deploy). The fallback is still `cd frontend && vercel deploy --prod --yes`.
  - This is the **only** frontend deploy path. Vercel's own Git integration was disconnected 2026-10-03: it built from the repo root (no `package.json` → "npm install exited with 254" emails on every push). Don't reconnect it unless the project's Root Directory is set to `frontend` and this Action is removed.
- **Manual Local Frontend Deploy**:
  ```bash
  ./scripts/deploy_frontend.sh
  ```
- **Backend to Railway**: Automatically built via Railway GitHub integration using `backend/Dockerfile`.

## Interrupted jobs, archive, media links (2026-10-03)

- **A deploy/restart cut a job off:** nothing to do. The new server resumes it once the old one has been silent for 75 s (logs: `job_resumed`). After 2 interruptions it stops (`job_not_resumed`) — re-run it by hand. Rows: table `resumable_jobs`.
- **Archived breakdown needs editing:** open it in LongForm Studio → **Restore footage** (re-downloads the IMDb trailers, rebuilds stills, no AI cost), then re-render. Preview / switch / days: the 📦 line at the top of LongForm Studio, or `GET/PUT /api/studio/archive`; run now: `POST /api/studio/archive/run`.
- **Images/videos 401 "media link expired":** reload the page (the media pass is refreshed every 6 h and lasts 12 h). Never put the access token in a URL — it is refused.

## Daily spend cap (2026-10-03)
- **Where**: Settings → 💰 Spending → "Today" box. Default **$6/day**, editable; 0 = off. Day = posting schedule timezone (America/New_York). Counts pay-as-you-go spend only (Gemini, search grounding, TTS), not subscriptions.
- **What happens at the cap**: every paid call on the site is refused (`costs.DailyCapReached`). Running jobs **pause**, not fail: Studio steps show ⏸ "paused — daily spend cap reached"; bulk AI rewrites stop with the finished videos checkpointed. Their job rows get status `budget_paused` and are listed under "Paused by the cap".
- **Resume**: automatic. The scheduler (every 5 min) relaunches `budget_paused` jobs **first, oldest first**, as soon as there's budget (new day, or you raise the cap). A Studio step restarts from the beginning of the step it was on; finished steps are kept. Single-click AI actions (one caption, one voice-over) are just refused with the reason — press again later.
- **Not tracked → not capped**: Railway CPU (renders), Gemini voice-overs in SocialPilot (refused at the cap, but their cost isn't recorded).

## Breakdown automation (2026-10-03)
- **Turn on/off**: LongForm Studio → 🤖 Breakdown automation. Off = nothing new starts; a running breakdown finishes its step (■ Stop it to halt).
- **Order each 5-min tick**: budget-paused jobs resume first → Studio must be idle → today's limits (3 movies + 2 TV) → today's spend + estimated breakdown cost must fit the daily cap → best checked candidate (pins first) with an **IMDb** trailer (YouTube-only trailers need the Mac worker, so they're manual-only) → steps 1–5.
- **Why isn't it starting?** The card's "Next" line says exactly what it's waiting on. Check the trending tab's "Didn't make the list" for each title's failed checks.
- **Review → render**: Your videos → filter "Needs review" → tick "✓ Reviewed" → Select all shown → 🎞 Render selected. The batch renders one at a time (runs step 5 first if missing), survives restarts, and stops from the running-jobs list.
- **Queue**: filter "Rendered" → select → 📤 Send to queue (status Review in the Posting Queue).

## LongForm Studio vs Countdowns Studio (2026-10-10)
- **Separation of Studios**:
  - **🎬 LongForm Studio**: Dedicated 100% to single-title **Trailer Breakdowns** (2–4 min: research → footage → shots → script → voice → video render → Posting Queue). No compilation or countdown stitchers live here.
  - **🏆 Countdowns Studio**: Dedicated standalone studio tab for **Top 5 & Top 10 Countdown Compilations** (8–12 min YouTube mid-roll eligible videos) with its autonomous 5-step research engine (Topic Discovery, Defensible Ranking, Multi-Angle Scripting, Audio/Visuals, Final Audit).
- **Release Timing & Momentum Rules (October 2026 Forward)**:
  - **Current Month Forward**: Only titles releasing in the current month forward (e.g. October 2026+) or upcoming future months (up to 90 days ahead) are eligible for trailer breakdowns.
  - **15-Day Cutoff**: Any title released more than 15 days ago (`days < -15`) or released before the first day of the current month (`date < 1st of current month`) is automatically filtered out from LongForm Studio and rejected during discovery checks.
  - **Pre-Release Priority**: Focus on unreleased and prime approaching titles (0–14 days ahead) to capture search momentum before films hit theaters or streaming.

