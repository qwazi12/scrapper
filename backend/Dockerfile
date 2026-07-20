# Scrapper backend — FastAPI + yt-dlp + ffmpeg. Deploys to Railway.
FROM python:3.12-slim

# ffmpeg/ffprobe for compiling; the rest are yt-dlp niceties.
RUN apt-get update && apt-get install -y --no-install-recommends \
    ffmpeg ca-certificates && rm -rf /var/lib/apt/lists/*

WORKDIR /srv

# Install deps first for layer caching.
COPY backend/requirements.txt /srv/backend/requirements.txt
RUN pip install --no-cache-dir -r /srv/backend/requirements.txt

# App code. The package root is /srv so `backend.app.main` resolves.
COPY backend /srv/backend
COPY scrape.py compile.py /srv/

# Railway mounts a volume here (set DATA_DIR=/data in the service).
ENV DATA_DIR=/data
ENV PORT=8000
EXPOSE 8000

# Railway provides $PORT. In-process worker runs unless WORKER_MODE=web_only.
CMD ["sh", "-c", "python -m uvicorn backend.app.main:app --host 0.0.0.0 --port ${PORT:-8000}"]
