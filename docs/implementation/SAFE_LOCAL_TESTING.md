# Safe local testing and continuation

This branch is local review work. It is not a production release. Never copy the
production DB, credentials, Drive folders or queue into the test stack.

## Prerequisites

Python with backend/requirements.txt and backend/requirements-dev.txt, Node.js 22+
with frontend/package-lock.json installed, FFmpeg/ffprobe, and an installed
Chromium browser. macOS uses its existing Samantha voice; Linux uses espeak.
No speech, research or publishing API credentials are needed. Network access is
needed to install dependencies; test execution is offline.

On this Mac the already installed backend Python is
`/Users/kwasiyeboah/dev/scrapper/backend/.venv/bin/python`. It was reused without
changing its packages. Run commands from the isolated feature worktree:

```sh
cd /Users/kwasiyeboah/dev/scrapper-worktrees/revival-20261018
/Users/kwasiyeboah/dev/scrapper/backend/.venv/bin/python -m pytest backend/tests -q
cd frontend
npm ci --ignore-scripts --no-audit --no-fund
npx tsc --noEmit
NEXT_TELEMETRY_DISABLED=1 npm run build
cd ..
/Users/kwasiyeboah/dev/scrapper/backend/.venv/bin/python scripts/e2e/run_stack.py
```

The launcher creates a fresh temporary root with `.scrapper-e2e`, disposable
SQLite/media, and a synthetic owner token. It sets SAFE_E2E_MODE, suppresses
schedulers/recovery/rescan/cleanup, uses provider fixtures and denies external
socket connections. On macOS the OS sandbox also denies child-process outbound
networking. Credentials are omitted from the child environment. Real Drive and
social-upload entry points fail closed; fixture adapters write local receipts
and copies instead. The normal application defaults SAFE_E2E_MODE to false.

Wait for the printed `ready: true`. In another terminal, run:

```sh
cd /Users/kwasiyeboah/dev/scrapper-worktrees/revival-20261018
E2E_CHROME_PATH=/Users/kwasiyeboah/.cache/hyperframes/chrome/chrome-headless-shell/mac_arm-152.0.7977.30/chrome-headless-shell-mac-arm64/chrome-headless-shell \
  ./frontend/node_modules/.bin/playwright test -c frontend/playwright.config.ts
```

For manual preview, open http://127.0.0.1:3302. Under Connection & cookies, use
http://127.0.0.1:8802 and `synthetic-e2e-token`. The sidebar must say
`SAFE E2E · fake providers`. The fixture catalog offers Synthetic Film 1–5.
These are original colored shapes, not real film footage or research.
Inspect `/api/e2e/isolation`: no scheduler threads or blocked external attempts;
only explicit test posts may increase fake_publish_receipts. Do not switch its
connection to a production URL.

Stop the launcher with Ctrl-C; it stops its process groups. Evidence and the
synthetic temporary root remain for inspection. Each new launch creates a new
database; do not rerun destructive tests on an owner database. The test suite
expects a fresh stack for each full run. Logs use unique run filenames listed in
artifacts/e2e/stack.json so restart output cannot corrupt previous evidence.

## Linux offline container

```sh
docker build -f scripts/e2e/Dockerfile -t scrapper-e2e .
mkdir -p artifacts/e2e
docker run --rm --network none -v "$PWD/artifacts:/app/artifacts" scrapper-e2e
```

The Dockerfile installs dependencies during build; execution has no network
interface except container loopback. The local Docker daemon was unavailable
during this run, so this lane and the committed GitHub workflow remain unverified.
Do not set SCRAPPER_E2E_CONTAINER_OFFLINE on an ordinary networked Linux host.

## Interpretation

The committed browser suite exercises LongForm, Top5, native Short/carousel,
private editorial/learning actions and primary-page/mobile/project navigation. It uses actual HTTP servers, database, stage runner, FFmpeg and
offline speech. TMDB, research, model/vision, storage and publishing are fixtures.
It does not prove live provider compatibility, factual research quality, excellent
creative content, complete native-format coverage or audience growth.

Final-file QA blocks decode, missing-audio, duration/aspect and stream-skew errors.
Loudness/silence/black-frame signals require editorial review. The detector also
flags intentionally dark countdown rank cards. It cannot verify captions,
semantic footage relevance, rights or a compelling hook. Approval fingerprints
bind exact video, thumbnail, script, plan and evidence inputs; countdown approvals
also bind the segment exports. Native campaign approvals bind immutable media,
cover, captions, research approval and asset provenance hashes. Legacy exports
need fresh inspection/review.

Photo transport follows the provider's [official photo endpoint](https://docs.upload-post.com/api/upload-photo/)
and [video endpoint](https://docs.upload-post.com/api/upload-video/) (checked October 18, 2026):
ordered photos[] multipart, async request ID and Idempotency-Key. Only fixture
transport and local mocked HTTP were tested; no live publishing call was made.

## Resume authorized development

The installed CLI help has `--approve-for-me`, not the handoff's `--full-auto`.
Do not launch a second agent while this desktop session is working. For a later
terminal continuation after this session ends:

```sh
caffeinate -dimsu codex exec \
  --cd /Users/kwasiyeboah/dev/scrapper-worktrees/revival-20261018 \
  --approve-for-me - < /Users/kwasiyeboah/dev/scrapper-worktrees/revival-20261018/docs/implementation/SCRAPPER_RESUME.md
```

This is a new CLI invocation using the saved ledger. It does not guarantee that
work continues after the process exits. No scheduled/background agent was created.
