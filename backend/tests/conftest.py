"""Test setup: throwaway SQLite DB + data dir, mock credentials only.

Env must be set before any backend.app import, because config and the DB
engine are built at import time.
"""

import os
import tempfile

_tmp = tempfile.mkdtemp(prefix="scrapper-test-")
os.environ["DATA_DIR"] = _tmp
os.environ["DATABASE_URL"] = ""
os.environ["ACCESS_TOKEN"] = "test-token"
os.environ["UPLOADPOST_API_KEY"] = ""
os.environ["GEMINI_API_KEY"] = ""
os.environ["GOOGLE_SERVICE_ACCOUNT_JSON"] = ""
os.environ["WORKER_MODE"] = "web_only"  # no background threads in tests
for _key in ("VIDIQ_API_KEY", "TMDB_API_KEY", "API_READ_ACCESS_TOKEN", "TMDB_READ_ACCESS_TOKEN",
             "TMDB_API_READ_ACCESS_TOKEN", "YOUTUBE_API_KEY", "TTS_API_KEY", "UPLOAD_POST_API_KEY", "UPLOADPOST_KEY"):
    os.environ[_key] = ""
os.environ["SAFE_E2E_MODE"] = "false"
os.environ["POST_POSTS_PER_DAY"] = "0"  # scheduler tests assume the every-2h slots; production default is 1/day
# The disk guard reads the real disk the tests run on (a nearly full laptop
# blocked every render test). Tests of the guard set their own thresholds.
os.environ["MAX_DISK_USAGE_PERCENT"] = "101"
os.environ["WARN_DISK_USAGE_PERCENT"] = "100"
os.environ["DISK_FREE_AT_PERCENT"] = "101"      # the disk guard reads the real disk too; tests switch it on themselves

import pytest  # noqa: E402
from backend.app.e2e_safety import install_network_guard  # noqa: E402
install_network_guard()  # no unmocked provider calls, even with inherited credentials

from backend.app.db import SessionLocal, init_db  # noqa: E402
from backend.app.models import QueueItem  # noqa: E402

init_db()


def pytest_sessionfinish(session, exitstatus):
    """Only this process's own synthetic root; failed evidence stays available.

    E2E evidence has a separate marked root and is deliberately retained. The
    previous mkdtemp-only setup accumulated DB/media after every passing run.
    """
    if exitstatus == 0 and os.environ.get("KEEP_TEST_DATA") != "1":
        from backend.app.db import engine
        import shutil
        engine.dispose()
        shutil.rmtree(_tmp)
    else:
        print(f"Synthetic unit-test data retained: {_tmp}")


@pytest.fixture(autouse=True)
def _finish_test_jobs(monkeypatch):
    """Keep fixture fakes alive until their background jobs actually exit.

    Several workers set the result before their finally blocks finish; leaking
    those threads lets the next test's HTTP/DB fakes service unrelated work.
    """
    import threading
    import time
    from backend.app.models import BudgetReservation, WorkerLease, CreditReservation
    from backend.app import resume
    resume._claims.clear()
    resume._active_rids.clear()
    with SessionLocal() as s:
        s.query(BudgetReservation).delete(); s.query(CreditReservation).delete(); s.query(WorkerLease).delete(); s.commit()
    yield
    deadline = time.monotonic() + 10
    while True:
        jobs = [t for t in threading.enumerate() if t is not threading.current_thread()
                and (t.name.startswith(("studio-", "job-")) or t.name == "bulk_ai")
                and t.name not in ("job-heartbeat", "job-resume-watch")]
        if not jobs:
            break
        if time.monotonic() > deadline:
            pytest.fail("Test leaked a background job: " + ", ".join(t.name for t in jobs))
        for job in jobs:
            job.join(timeout=.1)


@pytest.fixture()
def session():
    with SessionLocal() as s:
        s.query(QueueItem).delete()
        s.commit()
        yield s
