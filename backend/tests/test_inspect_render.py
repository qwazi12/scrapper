"""Run inspection on an existing render (fix plan 2026-10-20 step 1a): renders made before quality
inspection existed get schema-1 quality without a re-render, so Approve can be used."""
from fastapi.testclient import TestClient

from backend.app.db import SessionLocal
from backend.app.main import app
from backend.app.models import StudioProject
from backend.app.studio import media_quality, runner
from backend.tests.test_upload_quality import make_video

client = TestClient(app, headers={"x-access-token": "test-token"})


def _project(render):
    with SessionLocal() as s:
        p = StudioProject(tmdb_id=1, title="Old render", stage="render", stage_status="done", render=render)
        s.add(p)
        s.commit()
        return p.id


def test_inspect_saves_schema1_quality_for_an_old_render():
    pid = _project(None)
    root = runner.project_dir(pid)
    (root / "render").mkdir(parents=True, exist_ok=True)
    make_video(root / "render" / "final.mp4")
    with SessionLocal() as s:
        p = s.get(StudioProject, pid)
        p.render = {"file": "render/final.mp4", "seconds": 1.0}
        s.commit()
    r = client.post(f"/api/studio/projects/{pid}/inspect")
    assert r.status_code == 200, r.text
    with SessionLocal() as s:
        p = s.get(StudioProject, pid)
        q = p.render["quality"]
        assert q["schema"] == 1 and q["decode"] == "passed" and q["sha256"]
        assert p.render["file"] == "render/final.mp4"          # the rest of the render is kept
        media_quality.export_fingerprint(p, root)                # what Approve requires now passes


def test_inspect_without_a_final_export_is_refused():
    pid = _project({"seconds": 10})
    assert client.post(f"/api/studio/projects/{pid}/inspect").status_code == 409
