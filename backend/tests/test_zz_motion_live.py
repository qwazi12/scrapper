import os, shutil, pytest
from backend.tests.test_studio import _render_project, three_scene_video, runner, SessionLocal, StudioProject, stage_render  # noqa
pytestmark = pytest.mark.skipif(not os.environ.get("MOTION_LIVE"), reason="live HyperFrames render, opt-in")

def test_live(monkeypatch, three_scene_video):
    pid, root = _render_project(three_scene_video)
    monkeypatch.setattr(stage_render, "motion_mode", lambda: "compare")
    runner.run_one(pid, "render")
    with SessionLocal() as s:
        r = s.get(StudioProject, pid).render
    print("RENDER", r)
    shutil.copy(root / r["motion"]["file"], os.environ["MOTION_LIVE"])
