# scrapper

Personal watch-later video downloader + a storyboard web app that scrapes many
links and stitches the ones you pick into a single downloadable compilation.
Built on [yt-dlp](https://github.com/yt-dlp/yt-dlp), so it works for X/Twitter,
YouTube, TikTok, Instagram, and ~1800 other sites.

Two ways to use it:
- **Web app (live):** **https://scrapper.nodepilot.dev** — storyboard UI
  (`frontend/`, Next.js → Vercel) talking to a scrape/compile engine
  (`backend/`, FastAPI → Railway). Enter the shared access token once in the
  **⚙ Connection & cookies** panel. See [BUILD_PLAN.md](BUILD_PLAN.md) for the
  architecture and reliability strategy.
- **CLI** — `scrape.py` + `compile.py` (below).

## Web app (local)

```bash
# 1) backend (FastAPI engine — needs ffmpeg + yt-dlp)
cd backend && python3 -m venv .venv && .venv/bin/pip install -r requirements.txt
cd .. && DATA_DIR="$PWD/data" backend/.venv/bin/python -m uvicorn backend.app.main:app --port 8000

# 2) frontend (in another terminal)
cd frontend && npm install
NEXT_PUBLIC_API_BASE=http://127.0.0.1:8000 npm run dev   # http://localhost:3000
```

Paste links → **Scrape** → tick the clips you want → **Extract** → **Download**.
Config lives in `backend/.env.example` and `frontend/.env.example`.

## CLI

## Setup

```bash
pip3 install -U yt-dlp     # engine
brew install ffmpeg        # merges video+audio streams
```

## Usage

```bash
python3 scrape.py                          # download everything in urls.txt
python3 scrape.py https://x.com/...        # download specific links
python3 scrape.py --no-push                # skip the git push at the end
```

## What gets tracked

| File | Purpose |
|---|---|
| `urls.txt` | your watch-later queue |
| `MEMORY.md` | human-readable history of every run |
| `logs/scraper.log` | full debug log of every yt-dlp invocation |
| `logs/manifest.jsonl` | machine-readable record per video (id, title, path, status) |
| `archive.txt` | dedup ledger — re-runs skip anything already downloaded |
| `downloads/` | the actual videos — **local only, gitignored** |

Every run auto-commits the logs/memory and pushes to this repo, so the
history is saved passively.

## Notes

- Videos are never pushed to GitHub (size + they're for personal viewing).
- If an X post needs login to view (age-restricted etc.), yt-dlp can use your
  browser cookies: add `--cookies-from-browser chrome` to `YTDLP_ARGS` in
  `scrape.py`.
- Keep yt-dlp updated (`pip3 install -U yt-dlp`) — X changes things often.
