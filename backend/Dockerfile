# Scrapper backend — FastAPI + yt-dlp + ffmpeg. Deploys to Railway.
FROM python:3.12-slim

# ffmpeg/ffprobe for compiling. nodejs is yt-dlp's JavaScript runtime: sites
# (YouTube today, likely others next) require JS execution to sign media URLs,
# and without a runtime yt-dlp warns "extraction deprecated" and every download
# 403s. scraper.js_runtime_args() detects and enables it.
# fonts-dejavu-core: text on LongForm Studio overlays (title cards, subscribe bar).
# Node 22 (nodesource) also runs HyperFrames, the LongForm Studio motion layer;
# Debian's nodejs is too old for it. The lib list is Chrome-for-testing's
# shared-lib set (HyperFrames downloads its own Chrome) — same as manhwa's image.
# unzip: HyperFrames unpacks its Chrome download with it; without it `browser
# ensure` sits at "100%" forever (seen 2026-10-02: a 30+ min hung build).
RUN apt-get update && apt-get install -y --no-install-recommends \
      ffmpeg ca-certificates curl gnupg fonts-dejavu-core unzip \
      libasound2 libatk-bridge2.0-0 libatk1.0-0 libatspi2.0-0 libcairo2 \
      libcups2 libdbus-1-3 libdrm2 libexpat1 libgbm1 libglib2.0-0 \
      libnspr4 libnss3 libpango-1.0-0 libx11-6 libxcb1 libxcomposite1 \
      libxdamage1 libxext6 libxfixes3 libxkbcommon0 libxrandr2 \
    && curl -fsSL https://deb.nodesource.com/setup_22.x | bash - \
    && apt-get install -y nodejs \
    && rm -rf /var/lib/apt/lists/*

# HyperFrames: one worker, screenshot capture — otherwise "auto workers" sees the
# host's many cores on Railway and over-spawns Chrome (manhwa's lesson).
ENV PRODUCER_LOW_MEMORY_MODE=1

WORKDIR /srv

# Install deps first for layer caching.
COPY backend/requirements.txt /srv/backend/requirements.txt
RUN pip install --no-cache-dir -r /srv/backend/requirements.txt

# Force-upgrade yt-dlp independently so it is ALWAYS the latest release,
# even when the requirements.txt version floor hasn't been bumped.
# YouTube's extractor changes frequently; a stale yt-dlp is the #1 cause of
# 403s on Railway. This layer is fast (single package, cached by Docker).
RUN pip install --no-cache-dir -U yt-dlp

# Motion layer: the HyperFrames CLI + GSAP at the exact versions in the lockfile
# (never resolved at render time), and Chrome baked in so renders never download.
# Chrome refuses to start sandboxed as root, so its binary is wrapped with --no-sandbox.
COPY backend/hyperframes/package.json backend/hyperframes/package-lock.json /srv/backend/hyperframes/
RUN cd /srv/backend/hyperframes \
    && npm ci --omit=dev --no-audit --no-fund \
    && ./node_modules/.bin/hyperframes --version
ENV HYPERFRAMES_BIN=/srv/backend/hyperframes/node_modules/.bin/hyperframes
RUN timeout 900 "$HYPERFRAMES_BIN" browser ensure \
    && CHROME="$("$HYPERFRAMES_BIN" browser path | tr -d '\r' | grep -E '^/' | tail -1)" \
    && echo "wrapping: $CHROME" \
    && mv "$CHROME" "$CHROME.real" \
    && printf '#!/bin/sh\nexec "%s.real" --no-sandbox "$@"\n' "$CHROME" > "$CHROME" \
    && chmod +x "$CHROME" \
    && "$CHROME" --version

# App code. The package root is /srv so `backend.app.main` resolves.
COPY backend /srv/backend
COPY scrape.py compile.py /srv/

# Railway mounts a volume here (set DATA_DIR=/data in the service).
ENV DATA_DIR=/data
ENV PORT=8000
EXPOSE 8000

# Railway provides $PORT. In-process worker runs unless WORKER_MODE=web_only.
# Self-update yt-dlp on every start so YouTube extraction never goes stale.
CMD ["sh", "-c", "yt-dlp -U --no-color 2>&1 | tail -2 && python -m uvicorn backend.app.main:app --host 0.0.0.0 --port ${PORT:-8000}"]
