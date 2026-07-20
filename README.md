# scrapper

Personal watch-later video downloader. Paste links into `urls.txt` (or pass
them on the command line), run `scrape.py`, and videos land in `downloads/`
organized by site. Built on [yt-dlp](https://github.com/yt-dlp/yt-dlp), so it
works for X/Twitter, YouTube, TikTok, Instagram, and ~1800 other sites.

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
