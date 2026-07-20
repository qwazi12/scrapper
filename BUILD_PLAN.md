# Build Plan — Scrapper Web App (scrapper.nodepilot.dev)

> Status: **awaiting approval**. Nothing below is built yet. This document is the
> agreed spec once you approve it.

Turn the current CLI scraper into a hosted web app: paste links → scrape →
review clips in a storyboard/spreadsheet UI → tick the ones you want → click
**Extract** → get one downloadable compilation. Everything logged and tracked.

---

## 1. The core constraint (why two hosts)

| Job | Needs | Host |
|---|---|---|
| Storyboard UI, ingest form, export list, logs view | static + serverless | **Vercel** |
| Run `yt-dlp` (download) + `ffmpeg` (compile) | long-running container, real filesystem, minutes of CPU | **Railway** |

Vercel is serverless: read-only filesystem, ~10–60s function limit, **no ffmpeg / no yt-dlp**. It can't scrape or encode. So:

- **Frontend → Vercel** at `scrapper.nodepilot.dev`
- **Backend/worker → Railway** at `api.scrapper.nodepilot.dev`

The backend reuses the code we already have (`scrape.py`, `compile.py`) as library modules — no logic thrown away.

---

## 2. Architecture

```
Browser (storyboard UI)
        │  HTTPS + auth token
        ▼
Vercel  (Next.js frontend)  scrapper.nodepilot.dev
        │  API calls
        ▼
Railway (FastAPI + worker)  api.scrapper.nodepilot.dev
        ├── yt-dlp     → downloads each clip
        ├── ffmpeg     → compiles ticked clips into 1 video
        ├── Postgres   → jobs / clips / compilations / logs (metadata)
        ├── Volume     → working files (temp downloads, encode scratch)
        └── R2 bucket  → finished clips + compilations (download links)
```

- **Backend:** FastAPI (Python) — reuses existing scrape/compile code.
- **Frontend:** Next.js (React + Tailwind) — the spreadsheet/storyboard.
- **Metadata DB:** Railway Postgres.
- **Video storage:** Cloudflare R2 (S3-compatible, **zero egress fees** — matters for 1 GB+ downloads). Working files on a Railway volume; finished files pushed to R2 with signed download URLs.
- **Jobs:** async — a background worker picks up ingest/compile jobs from a `jobs` table; the UI polls status (or subscribes via SSE) so a 30-min encode never blocks a request.
- **Auth:** single shared access token (personal tool) so the API isn't open to the world.

---

## 3. Data model (Postgres)

- **clips** — `id, source_url, platform, uploader, title, duration, width, height, thumb_key, file_key, status, job_id, created_at`
- **ingest_jobs** — `id, urls[], status, started_at, finished_at, error`
- **compilations** — `id, clip_ids[], orientation, output_key, duration, size_bytes, status, created_at`
- **logs** — `id, level, event, message, context, created_at` (mirrored to a log stream the UI can tail)

---

## 4. API (Railway / FastAPI)

| Method | Route | Purpose |
|---|---|---|
| POST | `/api/ingest` | body `{urls:[]}` → create ingest job, start scraping |
| GET | `/api/jobs/:id` | ingest job status + discovered clips |
| GET | `/api/clips` | all clips (feeds the storyboard table) |
| POST | `/api/compile` | body `{clip_ids:[], orientation}` → create compile job |
| GET | `/api/compilations` | list of finished compilations |
| GET | `/api/compilations/:id/download` | signed R2 URL (or stream) |
| GET | `/api/logs?since=` | tail logs for the Logs panel |
| GET | `/api/events` (SSE) | live progress push (optional, phase 5) |

All routes behind the shared access token.

---

## 5. Frontend sections (matches your reference image, built for this project)

1. **Top status bar** — live counts: clips scraped · selected · total runtime · compilations · storage used · last run.
2. **Ingest panel** — big textarea to paste many links (one per line) + **Scrape** button → creates an ingest job.
3. **Storyboard table** (the spreadsheet) — one row per clip:
   `# · thumbnail · platform · uploader · title · duration · resolution · ☑ include · status`
   Sortable, drag-to-reorder (sets compilation order), tick/untick.
4. **Extract button** — compiles the ticked clips into **one** video (portrait/landscape toggle) → creates a compile job, progress shown inline.
5. **Export section** — list of compilations with size/duration + **Download** button; re-export in the other orientation.
6. **Logs panel** — live tail of the scrape/compile pipeline (mirrors `logs/scraper.log`).

---

## 6. Repo restructure

```
scrapper/
├── backend/            # FastAPI app (Railway)
│   ├── app/            # routes, worker, db models
│   ├── core/           # scrape.py + compile.py refactored into importable modules
│   ├── Dockerfile      # python + ffmpeg + yt-dlp
│   └── requirements.txt
├── frontend/           # Next.js app (Vercel)
│   ├── app/            # storyboard, ingest, export, logs pages
│   └── package.json
├── scrape.py           # kept as a thin CLI wrapper around backend/core
├── compile.py          # kept as a thin CLI wrapper around backend/core
├── BUILD_PLAN.md       # this file
├── MEMORY.md
└── README.md
```

The existing CLI keeps working; the web app calls the same core.

---

## 7. Build phases

- **Phase 0 — Scaffold & restructure.** Split repo into `backend/` + `frontend/`, move scrape/compile logic into `backend/core`, keep CLI wrappers. Add Postgres schema + migrations.
- **Phase 1 — Backend + worker (local).** FastAPI routes, background worker, ingest + compile end-to-end against local Postgres + local disk. Dockerfile with ffmpeg + yt-dlp.
- **Phase 2 — Frontend (local).** Next.js storyboard, ingest, export, logs — wired to the local backend.
- **Phase 3 — Deploy backend to Railway.** Postgres plugin, volume, R2 credentials, env vars, `api.scrapper.nodepilot.dev`.
- **Phase 4 — Deploy frontend to Vercel.** Domain `scrapper.nodepilot.dev`, point API base at Railway.
- **Phase 5 — Polish.** SSE live progress, access token, X cookie upload, log retention, README/MEMORY update.

---

## 8. Honest risks / things you'll need

1. **X/Twitter blocks datacenter IPs.** Scraping worked from your home IP; from Railway's cloud IP, X often rate-limits or requires login. Mitigation: an authenticated `cookies.txt` upload (yt-dlp `--cookies`) and/or a proxy. Plan supports uploading cookies; without them some X links may fail in the cloud.
2. **Accounts/access you must provide:** Railway account, Vercel account, Cloudflare (for R2), and DNS control for `nodepilot.dev` to add the two subdomain records.
3. **Storage cost:** compilations are ~1 GB each; R2 storage is cheap and egress is free, but they do accumulate — we'll add a retention/delete control in the Export panel.
4. **Encode time:** ~7× realtime on your Mac's hardware encoder; Railway CPU encoding is slower (no VideoToolbox). A 30-min compilation may take a few minutes of CPU — fine for a job queue, not for a synchronous request (which is why jobs are async).

---

## 9. Decisions — LOCKED (2026-07-20)

- **Video storage:** ✅ **Railway persistent volume** (no external bucket). Finished clips + compilations live on the volume; downloads stream through the FastAPI backend. Note: Railway egress counts toward the Railway bill, and large (~1 GB) downloads are served from the container — acceptable for a personal tool. The Export panel gets a delete control to keep the volume from filling.
- **Access control:** ✅ **Single shared password/token** gating the whole app + API.
- **Backend language:** ✅ **FastAPI / Python**, reusing `scrape.py` + `compile.py` as core modules.

**Adjustment from these choices:** the R2 bucket in §2 is dropped — all storage is the Railway volume; `file_key`/`output_key` in §3 become volume paths, and `/download` streams the file (with the access token) instead of a signed URL.

---

## 10. Reliability & anti-blocking (layered defense)

No setup guarantees 100% uptime against a platform (esp. X) that actively blocks
downloading. Goal: *rarely fails, self-heals on platform changes, alerts when it
genuinely can't.* ~90% of breakage is stale yt-dlp + datacenter IPs — both fixable.

| Layer | What | Cost | When |
|---|---|---|---|
| 1 | **Self-healing engine** — nightly `pip install -U yt-dlp` before jobs; fixes most "suddenly stopped working" | free | day one |
| 2 | **Auth cookies** — `cookies.txt` from a **burner** X account, uploaded via UI; clears most 403/rate-limit | free | day one |
| 3 | **Retries + backoff + queue** — exponential backoff, self-rate-limit between clips, `archive.txt` idempotency | free (in-code) | day one |
| 4 | **Residential proxy** — yt-dlp `--proxy` to defeat datacenter-IP blocks | ~$1–3/GB | reserve, only if cloud gets blocked |
| 5 | **Hybrid worker** — Railway hosts API+queue+UI; scraping runs on a worker with a **residential IP** (your Mac / home mini-PC) pulling jobs. Sidesteps datacenter blocking entirely | free (home box) | config flag, enable if needed |

Plus **observability:** health-check job, per-source success-rate in the Logs
panel, and failure-spike alert (email/webhook) so you know before a video goes missing.

**Adopted:** Layers 1–3 built in from day one. Worker designed so Layer 5 (hybrid)
is a config flag. Layer 4 (proxy) held in reserve.

---

## 11. Status

- ✅ **Phase 0** — repo restructured into `backend/` + `frontend/`.
- ✅ **Phase 1** — FastAPI backend + worker + all reliability layers + observability. Tested end-to-end locally (X clips scraped → compiled → downloaded).
- ✅ **Phase 2** — Next.js storyboard frontend. Clean build, verified in-browser against the local backend.
- ⏳ **Phase 3** — deploy backend to Railway (Postgres + volume, `api.scrapper.nodepilot.dev`). **Needs your `railway login`.**
- ⏳ **Phase 4** — deploy frontend to Vercel (`scrapper.nodepilot.dev`). **Needs your `vercel login`.**
- ⏳ **Phase 5** — post-deploy polish (retention controls, alert webhook wiring).

**Run locally now:** see [README.md](README.md) → "Web app (local)".
