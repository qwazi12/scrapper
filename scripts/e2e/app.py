"""Real application with offline provider fixtures. NEVER import outside safe mode.

DB, HTTP routes, stage runner, scene detection, edit plan, FFmpeg and reconciliation
are production code. Only external provider boundaries are replaced here.
"""
from __future__ import annotations

import json
import os
from pathlib import Path
import re
import shutil
import subprocess

from backend.app.config import settings
from backend.app.e2e_safety import IsolationError

if not settings.safe_e2e_mode:
    raise IsolationError("Fixture app requires SAFE_E2E_MODE")

from backend.app.main import app
from backend.app.db import SessionLocal, init_db
from backend.app.models import AppSetting
from backend.app.studio import (auto, candidates, gemini, imdb, media, stage_gather,
                                stage_render, tmdb, tts, countdown_topics, countdown, fact_verification)
from backend.app.studio.compilations import engine
from backend.app.social import upload_post

ROOT = Path(settings.safe_e2e_root)
ASSETS = ROOT / "fixtures"
ASSETS.mkdir(exist_ok=True)
init_db()


def assets():
    trailer = ASSETS / "trailer.mp4"
    if not trailer.exists():
        segments = []
        for n, color in enumerate(("red", "blue", "green", "orange", "purple", "cyan")):
            path = ASSETS / f"scene-{n}.mp4"
            # Original color/shape animations, distinct enough for perceptual grouping.
            media.run(["ffmpeg", "-v", "error", "-y", "-f", "lavfi", "-i",
                       f"color=c={color}:s=640x360:r=24:d=2,drawbox=x={20+n*70}:y={25+n*35}:w=130:h=90:color=white:t=fill",
                       "-c:v", "libx264", "-pix_fmt", "yuv420p", str(path)])
            segments.append(path)
        listing = ASSETS / "scenes.txt"
        listing.write_text("\n".join(f"file '{p}'" for p in segments))
        media.run(["ffmpeg", "-v", "error", "-y", "-f", "concat", "-safe", "0", "-i", str(listing), "-c", "copy", str(trailer)])
        media.frame(trailer, 1, ASSETS / "poster.jpg")
    return trailer


TRAILER = assets()


def title(n=1):
    return {"tmdb_id": n, "media_type": "movie", "title": f"Synthetic Film {n}", "date": "2026-10-18",
            "overview": "Original colored shapes change through six scenes.", "poster": None, "popularity": 1}


def facts(kind, n, **kwargs):
    return {**title(n), "primary_date": "2026-10-18", "releases": [], "directors": [], "writers": [],
            "writing_credits": [], "cast": [], "genres": ["Animation"], "videos": ([{"site":"YouTube","type":"Trailer","url":"https://youtube.com/watch?v=fixture40"}] if n==40 else []),
            "poster": os.getenv("SAFE_E2E_API_URL", "http://127.0.0.1:8802") + "/api/e2e/poster.jpg", "backdrops": [], "imdb_id": f"tt{int(n):07}",
            "source": f"https://www.themoviedb.org/movie/{n}", "attribution": tmdb.ATTRIBUTION}


def ask(prompt, images=None, **kwargs):
    # Failures are triggered via the actual owner's saved script, then appear
    # through the normal stage error API and review UI.
    if "fact-checker" in prompt:
        text = prompt.split("Script:\n", 1)[-1]
        numbered = re.findall(r"(?m)^(\d+)\. (.+)$", text)
        if "MALFORMED" in text:
            return {"not": "a verdict list"}
        if "TIMEOUT" in text:
            raise gemini.GeminiError("Synthetic provider timeout; retry check")
        return [{"i": int(i), "verdict": "unsupported" if "INVENTED" in line else "ok",
                 "kind": "cta" if line.endswith("?") else "fact", "refs": [] if line.endswith("?") else ["C1"] if line.startswith("Number ") else ["F2"],
                 "reason": "Synthetic unsupported claim" if "INVENTED" in line else ""}
                for i, line in numbered]
    if "logging shots" in prompt:
        n = int(re.search(r"next (\d+) images", prompt)[1])
        return [{"i": i + 1, "description": f"Original colored shape scene {i+1}", "people": [],
                 "setting": "synthetic", "mood": "bright", "size": "wide", "card": False, "quality": "good"} for i in range(n)]
    if "video editor placing" in prompt:
        return []  # production deterministic fallback/validation chooses actual shots
    if "scriptwriter" in prompt or "ONE segment" in prompt:
        number = re.search(r'The first sentence is exactly: "([^"]+)"', prompt)
        sentences = ([{"paragraph": 1, "text": number[1], "refs": ["C1"]}] if number else [])
        return {"sentences": [*sentences, {"paragraph": 1, "text": "Original colored shapes change through six scenes.", "refs": ["F2"]},
                              {"paragraph": 2, "text": "Which shape would you pick?", "refs": []}],
                "youtube_title": "Synthetic Film — Local Test", "tags": ["synthetic"]}
    # Countdown intro/outro fixtures, not real research or audience measurements.
    return {"hook": "Which original shape will finish first?", "intro": "Here are five synthetic films.",
            "outro": "Tell us which original shape you prefer.", "hook_script": "Five original animations.",
            "closing_script": "Thanks for watching this synthetic test."}


def synth(text, dest):
    dest = Path(dest); dest.parent.mkdir(parents=True, exist_ok=True)
    # Installed local voices only. No cloud TTS, credentials or downloadable voices.
    if shutil.which("say"):
        wav = dest.with_suffix(".aiff")
        subprocess.run(["say", "-v", "Samantha", "-r", "190", "-o", str(wav), text], check=True, timeout=30)
        media.run(["ffmpeg", "-v", "error", "-y", "-i", str(wav), "-af", "loudnorm=I=-14:TP=-1:LRA=7", str(dest)])
        wav.unlink()
    elif shutil.which("espeak"):
        wav = dest.with_suffix(".wav")
        subprocess.run(["espeak", "-w", str(wav), text], check=True, timeout=30)
        media.run(["ffmpeg", "-v", "error", "-y", "-i", str(wav), str(dest)])
        wav.unlink()
    else:
        raise RuntimeError("Offline speech voice unavailable: install espeak for local E2E")
    return dest


async def list_accounts():
    return [{"id": f"default:{net}", "profile": "default", "network": net,
             "nickname": "Synthetic local account", "isActive": True} for net in ("youtube", "facebook", "instagram")]


async def list_profiles():
    return ["default"]


async def upload(path, **kwargs):
    # Simulated transport persists its own receipt for crash/reconciliation tests.
    ledger = ROOT / "fake-provider.jsonl"
    rows = ledger.read_text().splitlines() if ledger.exists() else []
    rid = f"fake-{len(rows) + 1}"
    entry = {"request_id": rid, "platforms": kwargs["platforms"], "profile": kwargs["profile"], "file": str(path)}
    with ledger.open("a") as f:
        f.write(json.dumps(entry) + "\n")
        f.flush(); os.fsync(f.fileno())
    if kwargs.get('title')=='Synthetic uncertain delivery':
        raise TimeoutError('Synthetic response lost after provider acceptance')
    return rid


async def get_status(rid):
    rows = [json.loads(x) for x in (ROOT / "fake-provider.jsonl").read_text().splitlines()]
    row = next(x for x in rows if x["request_id"] == rid)
    return {"status": "completed", "results": [{"profile_username": row["profile"], "platform": p,
             "success": True, "post_url": f"https://example.invalid/{rid}/{p}"} for p in row["platforms"]]}


async def upload_images(paths, **kwargs):
    # The independent fixture ledger records the entire ordered carousel.
    rid = await upload(paths[0], **kwargs)
    with (ROOT / "fake-photo-provider.jsonl").open("a") as stream:
        stream.write(json.dumps({"request_id": rid, "files": [str(p) for p in paths]}) + "\n")
        stream.flush(); os.fsync(stream.fileno())
    return rid


tmdb.configured = lambda: True
tmdb.fact_sheet = facts
tmdb.search = lambda q: [title(int(re.search(r"\d+", q)[0]) if re.search(r"\d+", q) else 1)] if q != "EMPTY" else []
tmdb.calendar = lambda: {"upcoming_movies": [title()], "trending": [title()], "on_the_air_tv": []}
stage_gather._download = lambda url, dest: str(shutil.copy(ASSETS / "poster.jpg", dest)) if url else None
gemini.ask_json = ask
SOURCE_URL = "https://apnews.com/article/screen-central-offline-fixture"
SOURCE_TEXT = "Original colored shapes change through six scenes. This is original synthetic test footage for Screen Central, not real film news."
gemini.research = lambda prompt: {"text": "Synthetic test research", "sources": [{"title": "Synthetic offline article", "url": SOURCE_URL}],
    "claims": [{"text": "Original colored shapes change through six scenes.", "sources": [0]}], "queries": []}
def fetch_source(url):
    import hashlib
    if url != SOURCE_URL: raise IsolationError("Unexpected fixture source")
    return {"url": url, "requested_url": url, "text": SOURCE_TEXT, "sha256": hashlib.sha256(SOURCE_TEXT.encode()).hexdigest()}
fact_verification.fetch = fetch_source
imdb.list_videos = lambda imdb_id: [] if imdb_id == "tt0000040" else [{"id": "synthetic-trailer", "name": "Original fixture", "type": "Trailer", "seconds": 12}]
imdb.download = lambda video, dest: shutil.copy(TRAILER, dest)
tts.synth = synth
tts.configured = lambda: True
upload_post.list_accounts = list_accounts
upload_post.list_profiles = list_profiles
upload_post.upload_video = upload
upload_post.upload_images = upload_images
upload_post.get_status = get_status
upload_post.configured = lambda: True
engine.generate_countdown = lambda **kwargs: {"topic": kwargs["topic"], "format": "top5", "entries": [
    {**title(n), "year": 2026, "rank": n, "why_it_ranks": "Owner's synthetic ordering", "score": None} for n in range(1, 6)]}
countdown.market_data = lambda cid: {}
from backend.app.studio.compilations import cinemeta
cinemeta.get_top_movies = lambda: []
stage_render.motion_mode = lambda: "off"  # HyperFrames lane is tested separately
stage_render.END_CARD = 2.0  # test duration only; production value preserved
settings.gemini_api_key = "synthetic-fixture-not-a-key"


def fake_drive(project):
    from backend.app.models import StudioProject
    from backend.app.studio.runner import project_dir
    directory = ROOT / "fake-drive"; directory.mkdir(exist_ok=True)
    shutil.copy(project_dir(project.id) / project.render["file"], directory / f"project-{project.id}.mp4")
    with SessionLocal() as session:
        p = session.get(StudioProject, project.id)
        p.drive = {"status": "saved", "link": "https://example.invalid/fake-drive",
                   "rendered_at": project.render.get("rendered_at")}
        session.commit()
    return None


from backend.app.studio import routes as studio_routes
studio_routes._start_drive = fake_drive


def fake_countdown_drive(cid):
    from backend.app.models import CountdownProject
    with SessionLocal() as session:
        c = session.get(CountdownProject, cid)
        directory = ROOT / "fake-drive"; directory.mkdir(exist_ok=True)
        shutil.copy(c.render["file"], directory / f"countdown-{cid}.mp4")
        c.render = {**c.render, "drive": {"status": "saved", "link": "https://example.invalid/fake-countdown",
                                          "rendered_at": c.render.get("rendered_at")}}
        session.commit()
    return None


countdown.start_drive = fake_countdown_drive
auto.save_settings({"movies_per_day": 20, "tv_per_day": 20})
with SessionLocal() as session:
    session.merge(AppSetting(key="seo", value={"auto": False}))
    session.commit()


@app.get("/api/e2e/isolation")
def isolation():
    import threading
    from backend.app import e2e_safety
    ledger = ROOT / "fake-provider.jsonl"
    return {"safe_e2e_mode": settings.safe_e2e_mode, "data_dir": settings.data_dir,
            "worker_mode": settings.worker_mode, "real_publish_disabled": True,
            "provider_conditions": "offline fixtures + installed local speech", "external_spend_usd": 0,
            "blocked_network_attempts": e2e_safety.blocked_network_attempts,
            "scheduler_threads": [t.name for t in threading.enumerate() if t.name in ("posting_queue_scheduler", "scrapper-worker")],
            "fake_publish_receipts": len(ledger.read_text().splitlines()) if ledger.exists() else 0}


@app.get("/api/e2e/poster.jpg")
def poster():
    from fastapi.responses import FileResponse
    return FileResponse(ASSETS / "poster.jpg")


@app.post("/api/e2e/residential-run")
def residential_run():
    """Only the downloader boundary is synthetic; use the real client over HTTP."""
    from scripts import local_worker
    def download(url, workdir, browser, cancelled=None):
        target=workdir/"synthetic-worker.mp4"
        shutil.copy(TRAILER,target)
        return target,{'webpage_url':url,'title':'Original worker fixture','uploader':'Screen Central offline fixture'}
    previous=local_worker.download
    try:
        local_worker.download=download
        count=local_worker.drain(os.environ['SAFE_E2E_API_URL'],settings.access_token,None)
        return {'recovered':count}
    finally:
        local_worker.download=previous


@app.post("/api/e2e/uncertain-queue")
def uncertain_queue():
    from backend.app.models import QueueItem
    with SessionLocal() as s:
        row=QueueItem(title='Synthetic uncertain delivery',description='Original fixture for uncertain provider response',
            tags='#synthetic',pipeline='Movie Clips',status='review',accounts=['default:youtube'],video_path=str(TRAILER))
        s.add(row);s.commit();return {'id':row.id}


@app.post('/api/e2e/financial-holds')
def financial_holds_fixture():
    import datetime
    from datetime import timedelta
    from uuid import uuid4
    from backend.app import budget_reservations as br
    from backend.app.models import CreditReservation,WorkerLease
    usd=br.reserve('synthetic-finance',.4)
    credit=str(uuid4())
    with SessionLocal() as s:
        s.get(WorkerLease,'budget:'+usd).expires_at=datetime.datetime.now(datetime.timezone.utc)-timedelta(seconds=1)
        s.add(CreditReservation(id=credit,tool='synthetic-credit-tool',credits=5,state='unknown'));s.commit()
    return {'usd':usd,'credit':credit}
