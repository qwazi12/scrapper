"""Motion layer: HyperFrames (HeyGen's HTML-to-video renderer) for the designed
pieces of a breakdown — intro, release-date card, cast name cards, subscribe
button, end screen — in Screen Central's red and white. ffmpeg still draws the
trailer footage (fast); these pieces are ~25–35 s of a 2–4 min video.

Each piece = one template (motion_templates/<kind>.json) filled with the
video's data, rendered by the pinned CLI (backend/hyperframes, exact version
+ lockfile) and cached by a hash of the finished page + its images + the
template version, so a re-render only redraws what changed. Overlays render
as ProRes 4444 .mov (alpha) and ffmpeg lays them over the footage; the end
screen is full frame. Any failure returns None and the caller falls back to
the static Pillow version of that piece — the motion layer never blocks a video.
"""

from __future__ import annotations

import hashlib
import json
import logging
import os
import pathlib
import shutil

from ..config import settings
from .. import control
from . import media

logger = logging.getLogger("scrapper.studio.motion")

TEMPLATE_VERSION = "2"            # bump when a template's look changes: old cache entries stop matching
TEMPLATES = pathlib.Path(__file__).with_name("motion_templates")
HF_DIR = pathlib.Path(__file__).resolve().parents[2] / "hyperframes"
RENDER_TIMEOUT = 600
OVERLAY_KINDS = {"intro", "release", "cast", "subscribe"}   # transparent; "endscreen" is opaque


class MotionError(Exception):
    pass


def binary() -> str | None:
    env = os.environ.get("HYPERFRAMES_BIN")
    if env and pathlib.Path(env).exists():
        return env
    local = HF_DIR / "node_modules" / ".bin" / "hyperframes"
    return str(local) if local.exists() else None


def _gsap() -> pathlib.Path | None:
    p = HF_DIR / "node_modules" / "gsap" / "dist" / "gsap.min.js"
    return p if p.exists() else None


def available() -> tuple[bool, str]:
    if not binary():
        return False, "HyperFrames is not installed on this server"
    if not _gsap():
        return False, "GSAP is not installed next to HyperFrames"
    if not (media._font_path(True) and media._font_path(False)):
        return False, "no font found for the motion pieces"
    return True, ""


def cache_dir() -> pathlib.Path:
    d = pathlib.Path(settings.data_dir) / "studio" / "_motioncache"
    d.mkdir(parents=True, exist_ok=True)
    return d


def build_page(kind: str, values: dict) -> str:
    t = json.loads((TEMPLATES / f"{kind}.json").read_text())
    base = (TEMPLATES / "_base.html").read_text()
    # JSON inside <script>: escape "</" so a title can't close the tag.
    data = json.dumps(values, ensure_ascii=False).replace("</", "<\\/")
    return (base.replace("__BG__", t["bg"]).replace("__CSS__", t["css"]).replace("__BODY__", t["body"])
            .replace("__DUR__", f"{float(values['duration']):.3f}").replace("__JS__", t["js"])
            .replace("__VARS__", data))


def _digest(page: str, assets: dict[str, pathlib.Path]) -> str:
    h = hashlib.sha1(f"{TEMPLATE_VERSION}|{page}".encode())
    for name in sorted(assets):
        h.update(name.encode())
        h.update(assets[name].read_bytes())
    return h.hexdigest()[:20]


def render_piece(kind: str, values: dict, assets: dict[str, pathlib.Path] | None = None) -> pathlib.Path:
    """Render one piece (cached). Raises MotionError with the reason."""
    ok, why = available()
    if not ok:
        raise MotionError(why)
    assets = {k: v for k, v in (assets or {}).items() if v and v.exists()}
    page = build_page(kind, values)
    ext = "mov" if kind in OVERLAY_KINDS else "mp4"
    out = cache_dir() / f"{kind}-{_digest(page, assets)}.{ext}"
    if out.exists() and out.stat().st_size > 0:
        return out
    work = cache_dir() / f"_work-{out.stem}"
    shutil.rmtree(work, ignore_errors=True)
    work.mkdir(parents=True)
    try:
        (work / "index.html").write_text(page)
        (work / "hyperframes.json").write_text('{"paths": {"assets": "."}}')
        shutil.copy(_gsap(), work / "gsap.min.js")
        shutil.copy(media._font_path(True), work / "font-bold.ttf")
        shutil.copy(media._font_path(False), work / "font-regular.ttf")
        for name, src in assets.items():
            shutil.copy(src, work / name)
        tmp = work / f"out.{ext}"
        cmd = [binary(), "render", str(work), "-o", str(tmp), "--quiet", "-w", "1", "--fps", "30"]
        if ext == "mov":
            cmd += ["--format", "mov"]
        try:
            media.run(cmd, timeout=RENDER_TIMEOUT)
        except control.Cancelled:
            raise
        except Exception as exc:  # noqa: BLE001
            raise MotionError(f"{kind}: HyperFrames render failed — {str(exc)[-300:]}") from exc
        if not tmp.exists() or tmp.stat().st_size == 0:
            raise MotionError(f"{kind}: HyperFrames produced no file")
        tmp.replace(out)
        return out
    finally:
        shutil.rmtree(work, ignore_errors=True)


def try_piece(kind: str, values: dict, assets: dict[str, pathlib.Path] | None, failures: list[str]) -> pathlib.Path | None:
    try:
        return render_piece(kind, values, assets)
    except control.Cancelled:
        raise
    except Exception as exc:  # noqa: BLE001 — one piece failing falls back to its static version
        failures.append(str(exc)[:300])
        logger.warning("motion piece %s failed: %s", kind, exc)
        return None


# --- what goes where ---------------------------------------------------------------
def cast_cards(plan: list[dict], shots_by_id: dict[str, dict], cast: list[dict], max_cards: int,
               busy: list[tuple[float, float]], dur: float = 3.0) -> list[dict]:
    """Name cards for the leads (top-billed first), each on the first shot where
    that actor is the ONLY person tagged, so a card never sits on the wrong
    face. Skips any window that overlaps another overlay. Max `max_cards` (≤4)."""
    out: list[dict] = []
    for c in cast[:max(0, min(4, max_cards))]:
        actor = c.get("actor")
        for item in plan:
            sh = shots_by_id.get(item.get("shot") or "")
            if not sh or item.get("kind") not in ("clip", "still") or sh.get("people") != [actor]:
                continue
            if float(item["duration"]) < dur:
                continue
            start = float(item["start"]) + 0.2
            win = (start, start + dur)
            if any(a < win[1] and win[0] < b for a, b in busy):
                continue
            out.append({"actor": actor, "character": c.get("character") or "", "start": start, "duration": dur})
            busy.append(win)
            break
    return out


def intro_meta(facts: dict) -> str:
    year = (facts.get("primary_date") or "")[:4]
    genres = [g for g in facts.get("genres", [])][:2]
    return " · ".join(x for x in [year, *genres] if x)


def overlay_cmd(base: pathlib.Path, overlays: list[tuple[pathlib.Path, float]], audio: pathlib.Path | None,
                total: float, enc: list[str], dest: pathlib.Path) -> list[str]:
    """ffmpeg: lay each transparent .mov over the base video at its start time.
    -itsoffset shifts the overlay input (no setpts on the joined video — that
    once cut a 33 s render to 13 s); eof_action=pass shows the base again after."""
    cmd = ["ffmpeg", "-v", "error", "-y", "-i", str(base)]
    for f, start in overlays:
        cmd += ["-itsoffset", f"{start:.3f}", "-i", str(f)]
    if audio:
        cmd += ["-i", str(audio)]
    chain, last = [], "0:v"
    for i in range(len(overlays)):
        tag = f"v{i + 1}"
        chain.append(f"[{last}][{i + 1}:v]overlay=0:0:eof_action=pass:format=auto[{tag}]")
        last = tag
    if chain:
        cmd += ["-filter_complex", ";".join(chain), "-map", f"[{last}]"]
    else:
        cmd += ["-map", "0:v"]
    if audio:
        cmd += ["-map", f"{len(overlays) + 1}:a", "-c:a", "copy"]
    return cmd + [*enc, "-movflags", "+faststart", "-t", f"{total:.3f}", str(dest)]
