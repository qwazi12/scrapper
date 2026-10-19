"""Start an isolated local stack; Ctrl-C stops both processes, retaining evidence.

Run from repo root with the backend Python environment. No .env files or live
database are copied. The returned directory contains only synthetic test data.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import os
from pathlib import Path
import shutil
import signal
import subprocess
import sys
import tempfile
import time
import urllib.request

REPO = Path(__file__).resolve().parents[2]
parser = argparse.ArgumentParser()
parser.add_argument("--api-port", type=int, default=8802)
parser.add_argument("--web-port", type=int, default=3302)
parser.add_argument("--artifact-dir", default="artifacts/e2e")
parser.add_argument("--source-index", action="store_true", help="Freeze the staged backend instead of the current working files")
args = parser.parse_args()
artifacts = (REPO / args.artifact_dir).resolve()
if not artifacts.is_relative_to(REPO / "artifacts"):
    raise SystemExit("Artifacts must stay inside the ignored artifacts directory")
artifacts.mkdir(parents=True, exist_ok=True)
root = Path(tempfile.mkdtemp(prefix="scrapper-e2e-"))
(root / ".scrapper-e2e").touch()
# Python imports some modules lazily. Freeze one complete source generation so
# continuing local edits cannot mix new files with already-loaded old models.
source = root / "source"
source.mkdir()
listed = subprocess.check_output(["git", "ls-files", "-z", *([] if args.source_index else ["--cached", "--others", "--exclude-standard"]), "backend", "scripts"], cwd=REPO).split(b"\0")
for raw in sorted(set(listed)):
    if not raw: continue
    relative = Path(os.fsdecode(raw))
    if any(part.startswith(".env") or part in (".venv", "__pycache__", ".pytest_cache") for part in relative.parts): continue
    original = REPO / relative
    if not args.source_index and (not original.is_file() or original.is_symlink()): continue
    target = source / relative
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_bytes(subprocess.check_output(["git", "show", ":" + relative.as_posix()], cwd=REPO)
                      if args.source_index else original.read_bytes())
source_hash = hashlib.sha256()
for path in sorted(source.rglob("*")):
    if path.is_file():
        source_hash.update(path.relative_to(source).as_posix().encode() + b"\0" + path.read_bytes())
env = {k: os.environ[k] for k in ("PATH", "HOME", "TMPDIR", "LANG", "SYSTEMROOT") if k in os.environ}
env.update({"SAFE_E2E_MODE": "true", "SAFE_E2E_ROOT": str(root), "DATA_DIR": str(root / "data"),
            "SAFE_E2E_API_URL": f"http://127.0.0.1:{args.api_port}",
            "DATABASE_URL": "", "ACCESS_TOKEN": "synthetic-e2e-token", "WORKER_MODE": "web_only",
            "AUTO_UPDATE_YTDLP": "false", "BACKUP_ENABLED": "false", "DISK_FREE_AT_PERCENT": "101",
            "MAX_DISK_USAGE_PERCENT": "101", "WARN_DISK_USAGE_PERCENT": "100", "NEXT_TELEMETRY_DISABLED": "1",
            "DO_NOT_TRACK": "1", "HYPERFRAMES_SKIP_SKILLS": "1", "PYTHONPATH": str(source)})
commands = [([sys.executable, "-m", "uvicorn", "scripts.e2e.app:app", "--host", "127.0.0.1", "--port", str(args.api_port)], source, "backend.log"),
            ([shutil.which("node"), "node_modules/next/dist/bin/next", "start", "--hostname", "127.0.0.1", "--port", str(args.web_port)], REPO / "frontend", "frontend.log")]
processes, logs = [], []


def stop_stack(signum, frame):
    # Background launchers may inherit ignored SIGINT; always enter finally.
    raise KeyboardInterrupt


signal.signal(signal.SIGINT, stop_stack)
signal.signal(signal.SIGTERM, stop_stack)
try:
    for command, cwd, name in commands:
        if sys.platform == "darwin":
            command = ["sandbox-exec", "-p", '(version 1)(allow default)(deny network-outbound)(allow network-outbound (remote ip "localhost:*"))', *command]
        elif not os.environ.get("SCRAPPER_E2E_CONTAINER_OFFLINE"):
            raise SystemExit("Linux E2E requires an externally network-isolated container (SCRAPPER_E2E_CONTAINER_OFFLINE=1)")
        log = (artifacts / f"{root.name}-{name}").open("x"); logs.append(log)
        processes.append(subprocess.Popen(command, cwd=cwd, env=env, stdout=log, stderr=subprocess.STDOUT, start_new_session=True))
    manifest = {"root": str(root), "api": f"http://127.0.0.1:{args.api_port}", "web": f"http://127.0.0.1:{args.web_port}",
                "token": "synthetic-e2e-token", "pids": [p.pid for p in processes], "external_spend_usd": 0,
                "logs": [log.name for log in logs], "backend_source": str(source),
                "backend_source_sha256": source_hash.hexdigest(), "source_kind": "git_index" if args.source_index else "working_copy"}
    (artifacts / "stack.json").write_text(json.dumps(manifest, indent=2))
    for url in (manifest["api"] + "/api/health", manifest["web"]):
        deadline = time.monotonic() + 90
        while True:
            if any(p.poll() is not None for p in processes): raise RuntimeError("Stack exited: inspect artifact logs")
            try:
                with urllib.request.urlopen(url, timeout=1) as response:
                    if response.status == 200: break
            except Exception:
                if time.monotonic() > deadline: raise RuntimeError("Stack readiness timed out")
                time.sleep(.3)
    print(json.dumps({**manifest, "ready": True}), flush=True)
    while all(p.poll() is None for p in processes): time.sleep(.5)
except KeyboardInterrupt:
    pass
finally:
    for process in processes:
        if process.poll() is None: os.killpg(process.pid, signal.SIGTERM)
    for process in processes:
        try: process.wait(timeout=10)
        except subprocess.TimeoutExpired: os.killpg(process.pid, signal.SIGKILL)
    for log in logs: log.close()
