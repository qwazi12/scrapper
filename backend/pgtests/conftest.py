"""Disposable Unix-socket PostgreSQL lane; never target an existing database."""
import os
import threading
import time
from pathlib import Path
from urllib.parse import parse_qs, urlsplit

import pytest

root = Path(os.environ.get('SCRAPPER_PG_TEST_ROOT', '/nonexistent')).resolve()
url = urlsplit(os.environ.get('DATABASE_URL', ''))
query = parse_qs(url.query)
if (not root.name.startswith('scpg-') or not (root / '.scrapper-postgres-fixture').is_file()
        or url.hostname or url.path != '/scrapper_fixture' or query.get('host') != [str(root / 'socket')]):
    raise RuntimeError('PostgreSQL tests require a newly created marked local Unix-socket cluster')
for key in ('GEMINI_API_KEY','VIDIQ_API_KEY','TMDB_API_KEY','API_READ_ACCESS_TOKEN','TMDB_READ_ACCESS_TOKEN',
            'UPLOADPOST_API_KEY','UPLOAD_POST_API_KEY','GOOGLE_SERVICE_ACCOUNT_JSON','GOOGLE_DRIVE_AUTHORIZED_USER_JSON','YOUTUBE_API_KEY','TTS_API_KEY'):
    os.environ[key] = ''
os.environ.update(ACCESS_TOKEN='test-token',WORKER_MODE='web_only',SAFE_E2E_MODE='false',
    DATA_DIR=str(root/'app-data'),BACKUP_ENABLED='false',MAX_DISK_USAGE_PERCENT='101',
    WARN_DISK_USAGE_PERCENT='100',DISK_FREE_AT_PERCENT='101',POST_POSTS_PER_DAY='0')

from backend.app.e2e_safety import install_network_guard
install_network_guard()
from backend.app.db import SessionLocal, init_db, engine
from backend.app.models import BudgetReservation, CreditReservation, FileUse, QueueItem, WorkerLease
from backend.app import resume

assert engine.dialect.name == 'postgresql'
init_db()


@pytest.fixture(autouse=True)
def ownership_and_job_cleanup():
    resume._claims.clear(); resume._active_rids.clear()
    with SessionLocal() as s:
        for model in (BudgetReservation, CreditReservation, FileUse, WorkerLease): s.query(model).delete()
        s.commit()
    yield
    deadline = time.monotonic() + 10
    while True:
        jobs = [t for t in threading.enumerate() if t is not threading.current_thread()
                and (t.name.startswith(('studio-', 'job-')) or t.name == 'bulk_ai')
                and t.name not in ('job-heartbeat','job-resume-watch')]
        if not jobs: break
        if time.monotonic() >= deadline: pytest.fail('PostgreSQL lane leaked background jobs')
        for t in jobs: t.join(.1)


@pytest.fixture
def session():
    with SessionLocal() as s:
        s.query(QueueItem).delete(); s.commit()
        yield s
