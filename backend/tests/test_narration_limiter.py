"""Fix plan 2026-10-20 step 2: the narration master never exceeds the -1 dBFS true-peak target, so the
release check (blocks > 0 dBFS) no longer stops loud entries like countdown #7."""
from backend.app.studio import media, media_quality
from backend.app.studio.stage_render import NARRATION_MASTER


def test_clipped_loud_narration_is_limited_below_minus_one_dbfs(tmp_path):
    src, out = tmp_path / "loud.wav", tmp_path / "narration.m4a"
    # Quiet tone with a loud burst every second: loudnorm alone peaks at about -0.5 dBFS here
    # (real renders reached +1.0 dBFS, e.g. #115 Cyberpunk: Edgerunners).
    expr = "0.02*sin(2*PI*300*t)+if(gt(mod(t\\,1)\\,0.98)\\,0.9*sin(2*PI*3000*t)\\,0)"
    media.run(["ffmpeg", "-v", "error", "-y", "-f", "lavfi", "-i", f"aevalsrc={expr}:s=48000:d=8",
               "-ac", "2", str(src)])
    media.run(["ffmpeg", "-v", "error", "-y", "-i", str(src), "-af", NARRATION_MASTER,
               "-c:a", "aac", "-b:a", "192k", str(out)])
    log = media.run(["ffmpeg", "-hide_banner", "-i", str(out), "-af", "ebur128=peak=true", "-f", "null", "-"]).stderr
    peak = float(log.rsplit("Peak:", 1)[1].split("dBFS")[0])
    assert peak <= -1.0, peak
    lufs = float(log.rsplit("I:", 1)[1].split("LUFS")[0])
    assert -16 <= lufs <= -11, lufs
