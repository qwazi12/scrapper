"""Inspect the final muxed file and bind owner approval to exact export inputs."""
from __future__ import annotations

import hashlib
import json
import math
from pathlib import Path
import re

from . import media


def file_hash(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as f:
        for chunk in iter(lambda: f.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def inspect_export(path: Path, expected_seconds: float | None = None, aspect: float = 16 / 9) -> dict:
    probe = json.loads(media.run(["ffprobe", "-v", "error", "-show_streams", "-show_format", "-of", "json", str(path)], timeout=30).stdout)
    streams = probe.get("streams", [])
    video = next((s for s in streams if s.get("codec_type") == "video"), None)
    audio = next((s for s in streams if s.get("codec_type") == "audio"), None)
    errors, warnings = [], []
    seconds = float(probe.get("format", {}).get("duration", 0))
    if not math.isfinite(seconds) or seconds <= 0:
        errors.append("Invalid export duration")
    if not video or not video.get("height") or not video.get("width"):
        errors.append("Missing video stream")
    elif abs(video["width"] / video["height"] - aspect) > .03:
        errors.append("Export aspect ratio differs from intended format")
    if not audio:
        errors.append("Missing narration audio stream")
    if expected_seconds and abs(seconds - expected_seconds) > max(.25, expected_seconds * .01):
        errors.append("Export duration differs from planned timeline")
    if video and audio:
        skew = abs(float(video.get("duration", seconds)) - float(audio.get("duration", seconds)))
        if skew > .25:
            errors.append("Audio/video duration mismatch exceeds 250ms")
    decoded = media.run(["ffmpeg", "-v", "error", "-xerror", "-i", str(path), "-f", "null", "-"], timeout=1800)
    if decoded.stderr.strip():
        errors.append("Final export decode reported errors")
    # Objective signals are review aids. They cannot establish relevance,
    # copyright permission, caption accuracy or excellent narration.
    signals = ""
    if video and audio:
        signals = media.run(["ffmpeg", "-hide_banner", "-i", str(path),
                             "-vf", "blackdetect=d=0.5:pix_th=0.10:pic_th=0.98",
                             "-af", "silencedetect=noise=-45dB:d=1,ebur128=peak=true",
                             "-f", "null", "-"], timeout=1800).stderr
    loud = re.findall(r"I:\s*(-?[\d.]+) LUFS", signals)
    lufs = float(loud[-1]) if loud else None
    peaks = re.findall(r"Peak:\s*(-?[\d.]+) dBFS", signals)
    peak = float(peaks[-1]) if peaks else None
    black_spans=[{'start':float(a),'end':float(b)} for a,b in re.findall(r'black_start:([\d.]+) black_end:([\d.]+)',signals)]
    silence_spans=[]
    silence_start=None
    for kind,value in re.findall(r'silence_(start|end):\s*([\d.]+)',signals):
        if kind=='start':silence_start=float(value)
        elif silence_start is not None:
            silence_spans.append({'start':silence_start,'end':float(value)});silence_start=None
    if silence_start is not None:silence_spans.append({'start':silence_start,'end':seconds})
    silence = [float(v) for v in re.findall(r"silence_duration:\s*([\d.]+)", signals)]
    black = [float(v) for v in re.findall(r"black_duration:([\d.]+)", signals)]
    if lufs is None or not -18 <= lufs <= -11:
        warnings.append("Review loudness: target -18 to -11 LUFS")
    if peak is None:
        warnings.append("True peak measurement unavailable")
    elif peak > 0:
        errors.append("Audio true peak exceeds 0 dBFS")
    elif peak > -1:
        warnings.append("Review audio true peak: target at most -1 dBFS")
    if max(silence, default=0) > 3:
        warnings.append("Review silence longer than 3 seconds")
    if black:
        warnings.append("Review full-frame black spans")
    return {"schema": 1, "sha256": file_hash(path), "seconds": seconds,
            "width": video.get("width") if video else None, "height": video.get("height") if video else None,
            "audio_codec": audio.get("codec_name") if audio else None, "lufs": lufs,
            "true_peak_dbfs": peak,
            "unverified_checks": ["caption accuracy", "visual relevance", "rights clearance", "narrative quality"],
            "silence_seconds": silence, "black_seconds": black,
            "silence_spans":silence_spans,"black_spans":black_spans, "errors": errors, "warnings": warnings,
            "status": "blocked" if errors else "needs_owner_review", "decode": "passed"}


def export_fingerprint(project, root: Path) -> str:
    render = project.render or {}
    def local_file(value):
        path = (root / str(value or "")).resolve()
        if not path.is_relative_to(root.resolve()) or not path.is_file():
            raise RuntimeError("Export asset missing or outside project storage")
        return path
    video = local_file(render.get("file"))
    qa = render.get("quality") or {}
    if qa.get("schema") != 1 or qa.get("decode") != "passed" or qa.get("errors"):
        raise RuntimeError("Inspect this final export before approval")
    digest = file_hash(video)
    if digest != qa.get("sha256"):
        raise RuntimeError("Export changed since quality inspection; inspect again")
    thumbnail = file_hash(local_file(render["thumbnail"])) if render.get("thumbnail") else None
    inputs = {"quality":qa,"project_created_at":str(getattr(project,"created_at",None)),"trailer":getattr(project,"trailer",None),"video": digest, "thumbnail": thumbnail, "script": project.script, "plan": getattr(project, "plan", None),
              "facts": getattr(project, "facts", None), "research": getattr(project, "research", None),
              "shots": getattr(project, "shots", None)}
    if hasattr(project, "ranking"):
        inputs.update(ranking=project.ranking, segment_ids=project.segment_ids,
                      segment_fingerprints=render.get("segment_fingerprints"))
    return hashlib.sha256(json.dumps(inputs, sort_keys=True, ensure_ascii=False,default=str).encode()).hexdigest()


def require_export_approval(project, root: Path) -> None:
    from .export_acceptance import require_acceptance
    require_acceptance(project,root)
