#!/usr/bin/env python3
"""Download the links the cloud server can't, from your home connection.

Why this exists: Railway runs on a datacenter IP, and YouTube (and often TikTok)
refuse those outright with "Sign in to confirm you're not a bot" — no account or
cookie fixes that reliably, and pushing account cookies through a datacenter IP
is what gets Google accounts flagged. Your home connection is not blocked, so
this claims the blocked URLs, downloads them here, and uploads them back.

    python3 scripts/local_worker.py              # drain the queue once and exit
    python3 scripts/local_worker.py --watch      # keep polling (Ctrl-C to stop)
    python3 scripts/local_worker.py --browser chrome   # also send browser cookies

Config comes from data/deploy.env (SERVER_URL / ACCESS_TOKEN) or the flags.
Install it as a background job with scripts/install_local_worker.sh.
"""

from __future__ import annotations

import argparse
import datetime
import json
import contextlib
import hashlib
import threading
import pathlib
import subprocess
import sys
import tempfile
import time
import urllib.error
import urllib.request
import uuid

REPO = pathlib.Path(__file__).resolve().parent.parent
ENV_FILE = REPO / "data" / "deploy.env"
VERBOSE = False
WORKER_ID = uuid.uuid4().hex


def log_msg(msg: str) -> None:
    ts = datetime.datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    print(f"[{ts}] {msg}", flush=True)


def _ssl_ctx():
    """python.org builds on macOS ship no CA store; use certifi's when present."""
    try:
        import certifi
        import ssl
        return ssl.create_default_context(cafile=certifi.where())
    except Exception:
        return None


def load_env() -> dict:
    env = {}
    if ENV_FILE.exists():
        for line in ENV_FILE.read_text().splitlines():
            line = line.strip()
            if line and not line.startswith("#") and "=" in line:
                k, v = line.split("=", 1)
                env[k.strip()] = v.strip().strip('"').strip("'")
    return env


def api_get(server: str, token: str, path: str):
    req = urllib.request.Request(f"{server.rstrip('/')}{path}",
                                 headers={"x-access-token": token})
    with urllib.request.urlopen(req, timeout=60, context=_ssl_ctx()) as r:
        return json.loads(r.read().decode())


def api_post(server,token,path,data):
    req=urllib.request.Request(f"{server.rstrip('/')}{path}",data=json.dumps(data).encode(),method='POST',
        headers={'x-access-token':token,'Content-Type':'application/json'})
    with urllib.request.urlopen(req,timeout=60,context=_ssl_ctx()) as response:
        return json.load(response)


@contextlib.contextmanager
def claimed(server,token,claim):
    stopped,lost=threading.Event(),threading.Event()
    def beat():
        while not stopped.wait(claim['renew_after_seconds']):
            try:
                api_post(server,token,f"/api/worker-transfers/{claim['token']}/renew",
                         {'worker_id':WORKER_ID,'generation':claim['generation']})
            except Exception:
                lost.set();return
    thread=threading.Thread(target=beat,daemon=True,name='residential-lease-renew')
    thread.start()
    try: yield lost
    finally: stopped.set();thread.join(timeout=1)


def js_runtime_args() -> list[str]:
    """Enable a JavaScript runtime for YouTube signature deciphering.

    YouTube requires JS execution to sign media URLs; without a runtime yt-dlp
    warns and every download 403s. Only `deno` is enabled by default, so find
    whichever runtime is actually installed and point yt-dlp at it.
    """
    import shutil
    for name in ("deno", "node", "bun"):          # yt-dlp's priority order
        path = shutil.which(name)
        if path:
            return ["--js-runtimes", f"{name}:{path}"]
    return []


def pacing_args(url: str) -> list[str]:
    """Mirror of the server's pacing rule (backend/core/scraper.py).

    `-t sleep` pauses 10-20s before each download — good manners on X, but fatal
    on YouTube, whose media URLs return 403 when fetched that long after
    extraction. YouTube keeps only the between-request politeness.
    """
    if "youtube.com" in url or "youtu.be" in url:
        return ["--sleep-requests", "0.75"]
    return ["-t", "sleep"]


def download(url: str, workdir: pathlib.Path, browser: str | None, cancelled=None) -> tuple[pathlib.Path, dict]:
    """Fetch one URL locally. Returns (video_path, info_dict)."""
    out_tpl = str(workdir / "%(uploader_id)s_%(id)s_%(title).60B.%(ext)s")
    cmd = [
        "yt-dlp",
        *js_runtime_args(),                # YouTube needs JS to sign media URLs
        *pacing_args(url),                 # same platform-aware pacing as the server
        "--output", out_tpl,
        "--format", "bv*+ba/b",
        "--format-sort", "res:1080",
        "--merge-output-format", "mp4",
        "--write-info-json",
        "--write-thumbnail",
        "--convert-thumbnails", "jpg",
        "--no-progress",
        "--retries", "5",
    ]
    if browser:
        cmd += ["--cookies-from-browser", browser]
    cmd.append(url)

    if VERBOSE:
        print("     $ " + " ".join(cmd))
    child=subprocess.Popen(cmd,stdout=subprocess.PIPE,stderr=subprocess.PIPE,text=True)
    deadline=time.monotonic()+3600
    while True:
        try:
            stdout,stderr=child.communicate(timeout=.5);break
        except subprocess.TimeoutExpired:
            if (cancelled and cancelled.is_set()) or time.monotonic()>deadline:
                child.kill();child.communicate();raise RuntimeError('Download stopped: worker lease lost or one-hour timeout')
    proc=subprocess.CompletedProcess(cmd,child.returncode,stdout,stderr)
    videos = sorted(workdir.glob("*.mp4"))
    if proc.returncode != 0 or not videos:
        out = (proc.stderr or proc.stdout).strip()
        if VERBOSE:
            print("     --- yt-dlp output ---")
            for line in out.splitlines()[-15:]:
                print("     " + line)
        errs = [l for l in out.splitlines() if l.startswith("ERROR")]
        raise RuntimeError(errs[-1] if errs else (out.splitlines() or ["yt-dlp failed"])[-1])

    video = videos[0]
    info_path = video.with_suffix(".info.json")
    info = json.loads(info_path.read_text()) if info_path.exists() else {}
    return video, info


def upload(server: str, token: str, clip_id: int, video: pathlib.Path, info: dict, claim: dict) -> dict:
    """Multipart-upload the video (+thumb, +metadata) back to the server."""
    thumb = video.with_suffix(".jpg")
    keep=("id","title","uploader","uploader_id","duration","width","height","extractor","webpage_url","original_url")
    fields={'meta':json.dumps({k:info[k] for k in keep if k in info}), 'worker_token':claim['token'],
            'worker_id':WORKER_ID,'worker_generation':str(claim['generation'])}
    return multipart_video(server,token,f"/api/clips/{clip_id}/upload",video,fields,thumbnail=thumb)


def multipart_video(server,token,endpoint,video,fields,*,max_bytes=500*1024*1024,thumbnail=None):
    """Bounded streaming transport shared by clip recovery and channel archives."""
    boundary=f"----scrapperLocalWorker{uuid.uuid4().hex}"
    pieces=[]
    for field,path,ctype in [('file',video,'video/mp4'),*([('thumb',thumbnail,'image/jpeg')] if thumbnail and thumbnail.exists() else [])]:
        if path.stat().st_size > (max_bytes if field=='file' else 5*1024*1024):
            raise RuntimeError('Local media exceeds the bounded worker upload limit')
        # The generated local filename is not a transport header supplied by a website.
        header=f'--{boundary}\r\nContent-Disposition: form-data; name="{field}"; filename="{field}.bin"\r\nContent-Type: {ctype}\r\n\r\n'.encode()
        pieces.extend([header,path,b'\r\n'])
    for name,value in fields.items():
        pieces.append(f'--{boundary}\r\nContent-Disposition: form-data; name="{name}"\r\n\r\n{value}\r\n'.encode())
    pieces.append(f'--{boundary}--\r\n'.encode())
    length=sum(x.stat().st_size if isinstance(x,pathlib.Path) else len(x) for x in pieces)
    def body():
        for piece in pieces:
            if isinstance(piece,pathlib.Path):
                with piece.open('rb') as stream:
                    while chunk:=stream.read(1024*1024):yield chunk
            else:yield piece

    req = urllib.request.Request(
        f"{server.rstrip('/')}{endpoint}",
        data=body(),
        method="POST",
        headers={
            "Content-Type": f"multipart/form-data; boundary={boundary}",
            "x-access-token": token,
            "Content-Length": str(length),
        },
    )
    with urllib.request.urlopen(req, timeout=1800, context=_ssl_ctx()) as r:
        return json.loads(r.read().decode())


_last_empty_log = 0.0


def drain(server: str, token: str, browser: str | None) -> int:
    global _last_empty_log
    try:
        blocked = api_get(server, token, "/api/clips/blocked")
    except Exception as exc:
        log_msg(f"! cannot reach server: {exc}")
        return 0
    if not blocked:
        now = time.time()
        if now - _last_empty_log >= 120:
            log_msg("checked queue: 0 blocked URLs")
            _last_empty_log = now
        return 0

    log_msg(f"{len(blocked)} blocked URL(s) to fetch locally")
    done = 0
    for item in blocked:
        url, cid = item["source_url"], item["id"]
        log_msg(f"→ [{cid}] {url}")
        claim=None
        try:
            claim=api_post(server,token,f'/api/clips/{cid}/worker-claim',{'worker_id':WORKER_ID})
            if claim.get('protocol')!=2: raise RuntimeError('Server does not support the safe worker protocol')
            with claimed(server,token,claim) as lost, tempfile.TemporaryDirectory() as tmp:
                video,info=download(url,pathlib.Path(tmp),browser,lost)
                if lost.is_set(): raise RuntimeError('Worker lease was lost; media was not uploaded')
                with video.open('rb') as stream: sha=hashlib.file_digest(stream,'sha256').hexdigest()
                for attempt in range(3):
                    try:
                        result=upload(server,token,cid,video,info,claim)
                    except Exception:
                        # Never retry an ambiguous upload before asking for its receipt.
                        status=api_get(server,token,f"/api/worker-transfers/{claim['token']}")
                        if status['status']=='done': result=status['receipt']
                        elif status['status']=='claimed' and not lost.is_set() and attempt<2: continue
                        else: raise RuntimeError('Upload unresolved or claim expired; no blind retry')
                    if result.get('sha256')!=sha: raise RuntimeError('Server receipt does not match the uploaded file')
                    done+=1;log_msg(f"   ✓ {info.get('title',url)[:60]} (verified receipt)");break
        except Exception as exc:
            log_msg(f"   ✗ {exc}")
        finally:
            if claim:
                with contextlib.suppress(Exception):
                    api_post(server,token,f"/api/worker-transfers/{claim['token']}/release",
                             {'worker_id':WORKER_ID,'generation':claim['generation']})
    return done


def drain_archives(server,token):
    """Only explicit owner handoffs; challenge failures do not auto-loop."""
    if str(REPO) not in sys.path:sys.path.insert(0,str(REPO))
    from scripts import archive_worker
    done=0
    channels=api_get(server,token,'/api/channel-archives')['channels']
    for channel in channels:
        if channel['status']!='awaiting_worker' or channel['active_lease']:continue
        try:done+=archive_worker.drain(server,token,channel['channel_id'],REPO/'data'/'archive-worker')
        except Exception as exc:log_msg(f"Archive {channel['channel_id']} stopped: {exc}")
    return done


def main() -> int:
    env = load_env()
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--server", default=env.get("SERVER_URL", ""))
    ap.add_argument("--token", default=env.get("ACCESS_TOKEN", ""))
    ap.add_argument("--browser", default="", help="chrome | firefox | safari | edge | brave")
    ap.add_argument("--verbose", action="store_true", help="print the yt-dlp command and full output")
    ap.add_argument("--watch", action="store_true", help="keep polling instead of exiting")
    ap.add_argument("--interval", type=int, default=15, help="seconds between polls in --watch")
    args = ap.parse_args()

    if not args.server or not args.token:
        sys.exit("Need --server and --token (or SERVER_URL/ACCESS_TOKEN in data/deploy.env)")

    global VERBOSE
    VERBOSE = args.verbose
    browser = args.browser or None
    if not args.watch:
        n = drain(args.server, args.token, browser)
        archived = drain_archives(args.server,args.token)
        log_msg(f"verified archive files: {archived}")
        log_msg(f"done — {n} clip(s) recovered")
        return 0

    log_msg(f"watching {args.server} every {args.interval}s (Ctrl-C to stop)")
    while True:
        try:
            drain(args.server, args.token, browser)
            drain_archives(args.server,args.token)
        except KeyboardInterrupt:
            return 0
        except Exception as exc:
            log_msg(f"! {exc}")
        time.sleep(args.interval)


if __name__ == "__main__":
    sys.exit(main())
