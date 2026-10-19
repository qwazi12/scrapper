FROM node:22-bookworm
ENV DEBIAN_FRONTEND=noninteractive NEXT_TELEMETRY_DISABLED=1 DO_NOT_TRACK=1
RUN apt-get update && apt-get install -y python3 python3-venv ffmpeg fonts-dejavu-core espeak && rm -rf /var/lib/apt/lists/*
WORKDIR /app
COPY backend/requirements.txt backend/requirements-dev.txt /tmp/
RUN python3 -m venv /opt/e2e && /opt/e2e/bin/pip install -r /tmp/requirements.txt -r /tmp/requirements-dev.txt
COPY frontend/package.json frontend/package-lock.json /app/frontend/
RUN cd frontend && npm ci --ignore-scripts --no-audit --no-fund && npx playwright install --with-deps chromium
COPY backend /app/backend
COPY scripts/e2e /app/scripts/e2e
COPY frontend /app/frontend
# The frozen-source launcher uses the index as its file inventory. The build
# context intentionally excludes host .git metadata and credentials; create a
# fresh local index containing only the copied, disposable test sources.
RUN git init --quiet && git add backend scripts
RUN cd frontend && npm run build
ENV SCRAPPER_E2E_CONTAINER_OFFLINE=1 PATH="/opt/e2e/bin:$PATH"
CMD ["bash", "scripts/e2e/container-test.sh"]
