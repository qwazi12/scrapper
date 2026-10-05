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
os.environ["POST_POSTS_PER_DAY"] = "0"  # scheduler tests assume the every-2h slots; production default is 1/day
# The disk guard reads the real disk the tests run on (a nearly full laptop
# blocked every render test). Tests of the guard set their own thresholds.
os.environ["MAX_DISK_USAGE_PERCENT"] = "101"
os.environ["WARN_DISK_USAGE_PERCENT"] = "100"
os.environ["DISK_FREE_AT_PERCENT"] = "101"      # the disk guard reads the real disk too; tests switch it on themselves

import pytest  # noqa: E402

from backend.app.db import SessionLocal, init_db  # noqa: E402
from backend.app.models import QueueItem  # noqa: E402

init_db()


@pytest.fixture()
def session():
    with SessionLocal() as s:
        s.query(QueueItem).delete()
        s.commit()
        yield s
