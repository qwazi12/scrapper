#!/usr/bin/env python3
"""
Sync videos from Google Drive folders into the Scrapper / SocialPilot posting queue.
Handles parent folder scanning (e.g. 1kuOKRQQRL0ws5aOVqwkdUzdnfj5KQGjo) and automatically
maps subfolders (@VynixAE, @PixelDrift-f3c, etc.) directly into their respective channels.

Usage:
    python3 scripts/sync_drive.py
    python3 scripts/sync_drive.py --folder-id 1kuOKRQQRL0ws5aOVqwkdUzdnfj5KQGjo
"""
import argparse
import json
import os
import re
import sys
from pathlib import Path
import httpx

DEFAULT_FOLDER_ID = "1kuOKRQQRL0ws5aOVqwkdUzdnfj5KQGjo"
DEFAULT_PIPELINE = "Movie Clips"

LOCAL_KEY_CANDIDATES = [
    "/Users/kwasiyeboah/m3/omnistream/service_account.json",
    os.path.expanduser("~/m3/omnistream/service_account.json"),
    "service_account.json",
]


def extract_folder_id(url_or_id: str) -> str:
    raw = (url_or_id or "").strip()
    match = re.search(r"folders/([a-zA-Z0-9_-]+)", raw)
    if match:
        return match.group(1)
    match_param = re.search(r"id=([a-zA-Z0-9_-]+)", raw)
    if match_param:
        return match_param.group(1)
    return raw.split("?")[0].strip("/")


def clean_title(raw_name: str):
    stem = Path(raw_name).stem
    hashtags = re.findall(r"#\w+", stem)
    tags_str = " ".join(hashtags) if hashtags else "#shorts #movieclips"
    title = re.sub(r"#\w+", "", stem)
    title = re.sub(r"_[a-zA-Z0-9_-]{10,12}$", "", title)
    title = title.replace("_", " ").strip()
    title = re.sub(r"\s+", " ", title)
    return title or stem, tags_str


def main():
    parser = argparse.ArgumentParser(description="Sync Google Drive videos to Scrapper / SocialPilot")
    parser.add_argument("--folder-id", default=DEFAULT_FOLDER_ID, help="Google Drive folder ID or link")
    parser.add_argument("--pipeline", default=DEFAULT_PIPELINE, help="Fallback pipeline channel")
    parser.add_argument("--api-base", default=os.environ.get("SCRAPPER_API_BASE", "https://scrapper-production-d348.up.railway.app"))
    parser.add_argument("--token", default=os.environ.get("SCRAPPER_ACCESS_TOKEN", ""))
    parser.add_argument("--service-account", default=os.environ.get("GOOGLE_SERVICE_ACCOUNT_JSON", ""), help="Path to service account json")
    parser.add_argument("--auto-approve", action="store_true", help="Set status to 'ready' instead of 'review'")
    args = parser.parse_args()

    api_base = args.api_base.rstrip("/")
    folder_id = extract_folder_id(args.folder_id)
    headers = {}
    auth_token = args.token or os.environ.get("SCRAPPER_ACCESS_TOKEN") or os.environ.get("ACCESS_TOKEN")
    if not auth_token:
        try:
            import subprocess
            res_tok = subprocess.check_output(
                "railway variables --kv 2>/dev/null | grep '^ACCESS_TOKEN=' | cut -d= -f2-",
                shell=True
            ).decode().strip()
            if res_tok:
                auth_token = res_tok
        except Exception:
            pass

    if auth_token:
        headers["x-access-token"] = auth_token

    print("═════════════════════════════════════════════════════════════")
    print(" ☁️  SocialPilot AI Google Drive Ingestion Tool")
    print(f" Target Folder ID: {folder_id}")
    print(f" API Base:         {api_base}")
    print("═════════════════════════════════════════════════════════════\n")

    # Locate credentials
    key_path = args.service_account
    if not key_path or not os.path.exists(key_path):
        for cand in LOCAL_KEY_CANDIDATES:
            if os.path.exists(cand):
                key_path = cand
                break

    if not key_path or not os.path.exists(key_path):
        print("❌ Service account JSON not found. Please provide via --service-account.")
        sys.exit(1)

    print(f"🔑 Using Service Account: {key_path}")

    try:
        from google.oauth2 import service_account
        from googleapiclient.discovery import build

        creds = service_account.Credentials.from_service_account_file(
            key_path,
            scopes=["https://www.googleapis.com/auth/drive.readonly"],
        )
        service = build("drive", "v3", credentials=creds)

        # 1. Discover subfolders (channel handles)
        sub_query = f"'{folder_id}' in parents and trashed = false and mimeType = 'application/vnd.google-apps.folder'"
        sub_resp = service.files().list(q=sub_query, fields="files(id, name)", pageSize=100).execute()
        subfolders = sub_resp.get("files", [])

        targets = []
        if subfolders:
            print(f"📁 Found {len(subfolders)} channel subfolders:")
            for sf in subfolders:
                print(f"   • {sf['name']} ({sf['id']})")
                targets.append((sf["id"], sf["name"]))
        else:
            print(f"📁 Scanning single folder as '{args.pipeline}'")
            targets.append((folder_id, args.pipeline))

        # 2. Iterate each folder & ingest
        total_queued = 0
        total_skipped = 0

        for fid, channel_name in targets:
            print(f"\nScanning channel: {channel_name}...")
            video_query = (
                f"'{fid}' in parents and trashed = false and "
                f"(mimeType contains 'video/' or name contains '.mp4' or name contains '.mov' or name contains '.webm')"
            )

            channel_videos = []
            page_token = None
            while True:
                resp = service.files().list(
                    q=video_query,
                    fields="nextPageToken, files(id, name, webViewLink, thumbnailLink, size)",
                    pageToken=page_token,
                    pageSize=100
                ).execute()
                channel_videos.extend(resp.get("files", []))
                page_token = resp.get("nextPageToken")
                if not page_token:
                    break

            print(f"  Found {len(channel_videos)} videos in {channel_name}")

            for f in channel_videos:
                title, tags = clean_title(f["name"])
                web_link = f.get("webViewLink") or f"https://drive.google.com/file/d/{f['id']}/view"
                payload = {
                    "pipeline": channel_name,
                    "title": title,
                    "description": title,
                    "tags": tags,
                    "source": f"Google Drive ({channel_name})",
                    "drive_link": web_link,
                    "status": "ready" if args.auto_approve else "review",
                }

                try:
                    res = httpx.post(f"{api_base}/api/queue", json=payload, headers=headers, timeout=20)
                    if res.status_code == 200:
                        total_queued += 1
                        print(f"  ✓ Queued: {title[:40]}…")
                    else:
                        print(f"  ⚠️ Warning: {res.status_code} {res.text[:80]}")
                except Exception as err:
                    print(f"  ❌ Network error adding {title[:30]}: {err}")

        print(f"\n🎉 Done! Added {total_queued} items to the SocialPilot posting queue.")

    except ImportError:
        print("❌ Missing packages. Run: pip3 install google-api-python-client google-auth")
        sys.exit(1)


if __name__ == "__main__":
    main()
