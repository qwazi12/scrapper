# Configuration Reference (CONFIG.md)

This document defines all configuration options, environment variables, default schedules, and pipeline mappings for Scrapper and SocialPilot.

---

## 1. Storage & Volume Mounts

| Variable | Default | Purpose |
|---|---|---|
| `DATA_DIR` | `<repo>/data` (local) or `/data` (Railway) | Path to persistent storage volume (holds database, downloads, backups, cache, and studio projects). |
| `DATABASE_URL` | `""` (uses `/data/scrapper.db`) | Database connection string. Unset uses persistent SQLite. |

---

## 2. Authentication & Access Security

| Variable | Default | Purpose |
|---|---|---|
| `ACCESS_TOKEN` | `""` (open in local dev) | Shared master token required on all API requests. **Mandatory in production** (fails closed if missing). Sent **only in headers** (`x-access-token` / `Authorization: Bearer`); never accepted in a URL. Media links use a 12 h media pass from `GET /api/media-pass` (`?g=…`, read-only media routes only). |
| `VERCEL_API_TOKEN` / `VERCEL_TOKEN` | `""` | Personal Vercel API token used in CI/CD (GitHub Actions) and CLI deployments. |

---

## 3. Social Publishing (Upload-Post) & Posting Cadence

| Variable | Default | Purpose |
|---|---|---|
| `UPLOADPOST_API_KEY` | `""` | API key for Upload-Post social media publisher. |
| `PUBLISH_PRIVACY` | `public` | Default privacy for published videos (`public`, `unlisted`, `private`). |
| `POST_TIMEZONE` | `America/New_York` | Operating timezone for SocialPilot scheduler. |
| `POST_START_HOUR` | `8` (8:00 am) | First posting slot of the day. |
| `POST_END_HOUR` | `22` (10:00 pm) | Last posting slot of the day. |
| `POST_INTERVAL_HOURS` | `2` | Interval between slots (every 2 hours = 8 slots/day). |
| `ARCHIVE_DELETE_DAYS` | `4` | Days after posting before an item is moved to trash. |

---

## 4. Disk Space, Retention & Cache Thresholds

| Variable | Default | Purpose |
|---|---|---|
| `RETENTION_DAYS` | `5` | Rolling retention period in days for un-queued scraped clips. |
| `RETENTION_SWEEP_MINUTES`| `30` | Interval between background retention and cache pruning sweeps. |
| `MAX_DISK_USAGE_PERCENT`| `90.0` | Refusal threshold: new breakdowns and ingest jobs are blocked if disk usage reaches this percentage. |
| `WARN_DISK_USAGE_PERCENT`| `75.0` | Warning threshold displayed on the UI disk gauge. |
| `MAX_MOTION_CACHE_MB` | `400` | Maximum LRU cache size for HyperFrames motion graphics. |
| `MAX_TTS_CACHE_MB` | `200` | Maximum LRU cache size for TTS voice audio chunks. |

---

## 5. Google Drive & Shared Drive Mapping

| Variable / Constant | Target ID / Name | Purpose |
|---|---|---|
| Service Account Credentials | `SERVICE_ACCOUNT_JSON` or `/data/service_account.json` | Google Cloud Service Account for Drive API operations. |
| LongForm Studio Folder | `14z17C-cYIqUK8teqeOOVtnITx0GHHl8E` (or `LONGFORM_DRIVE_FOLDER_ID`) | Target folder in Google Shared Drive for rendered breakdowns and database backup `.db.gz` files. |
| Movie Clips Parent Folder | `1_VLhKfSEYZFyPBXi7kYu4uw94JUronDP` | Parent directory for channel subfolders in SocialPilot Google Drive sync. |

---

## 6. AI Metadata & Voice Studio (Google Gemini)

| Variable | Default | Purpose |
|---|---|---|
| `GEMINI_API_KEY` | `""` | Google Gemini API key for metadata generation, hook research, and TTS. |
| `GEMINI_MODEL` | `gemini-3.8-flash` | Text and multimodal model for titles, descriptions, and tags. |
| `GEMINI_TTS_MODEL` | `gemini-3.8-flash-tts`| Gemini model for voiceover and studio narration synthesis. |
| `GEMINI_TTS_VOICE` | `Puck` | Default voice preset (`Puck`, `Charon`, `Kore`, `Fenrir`, `Aoede`). |

## Scheduler, jobs and archive (2026-10-03)

| Setting | Default | Meaning |
|---|---|---|
| Auto-poster check | every 5 min (`TICK_SECONDS=300`) | Any queue/schedule change wakes it at once, so approvals get a slot immediately; posts go out within 5 min of their slot. |
| Resumable jobs | on | Studio steps, Drive sync, Channel Ingest, bulk AI, Drive saves, footage restores are recorded in `resumable_jobs`; a job whose server has been silent 75 s is resumed from its checkpoint (max 2 tries). Heartbeat every 20 s. |
| `app_settings.studio_archive` | `{"enabled": true, "days": 14}` | Breakdowns posted ≥ N days ago with a confirmed, current Drive copy lose footage/stills/render/segcache (uploaded footage kept). Restore footage re-downloads from IMDb. Daily, from the scheduler. |
| `RENDER_WORKERS` | 8 | Parallel segment renders (Railway box: 32 vCPU). Segments cached in `studio/<id>/segcache` (`SEG_VERSION`). |
