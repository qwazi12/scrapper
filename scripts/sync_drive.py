#!/usr/bin/env python3
"""
Sync videos from a Google Drive folder into the Scrapper queue & library.
Runs from your local Mac (which has your residential IP and Google account auth).

Usage:
    python3 scripts/sync_drive.py --folder-id 1_VLhKfSEYZFyPBXi7kYu4uw94JUronDP --pipeline "Movie Clips"
"""
import argparse
import json
import os
import sys
from pathlib import Path
import httpx

DEFAULT_FOLDER_ID = "1_VLhKfSEYZFyPBXi7kYu4uw94JUronDP"
DEFAULT_PIPELINE = "Movie Clips"

def main():
    parser = argparse.ArgumentParser(description="Sync Google Drive videos to Scrapper")
    parser.add_argument("--folder-id", default=DEFAULT_FOLDER_ID, help="Google Drive folder ID")
    parser.add_argument("--pipeline", default=DEFAULT_PIPELINE, help="Target pipeline channel")
    parser.add_argument("--api-base", default=os.environ.get("SCRAPPER_API_BASE", "https://scrapper-production-d348.up.railway.app"))
    parser.add_argument("--token", default=os.environ.get("SCRAPPER_ACCESS_TOKEN", ""))
    parser.add_argument("--service-account", default=os.environ.get("GOOGLE_SERVICE_ACCOUNT_JSON", ""), help="Path to service account json or raw json")
    args = parser.parse_args()

    api_base = args.api_base.rstrip("/")
    headers = {}
    if args.token:
        headers["x-access-token"] = args.token

    print(f"🔄 Scrapper Google Drive Ingestion Tool")
    print(f"Target Folder ID: {args.folder_id}")
    print(f"Destination Channel: {args.pipeline}")
    print(f"API Target: {api_base}\n")

    # If service account is provided, list via Google Drive API
    if args.service_account and os.path.exists(args.service_account):
        print(f"Using Service Account: {args.service_account}")
        try:
            from google.oauth2 import service_account
            from googleapiclient.discovery import build

            creds = service_account.Credentials.from_service_account_file(
                args.service_account,
                scopes=["https://www.googleapis.com/auth/drive.readonly"],
            )
            service = build("drive", "v3", credentials=creds)

            query = f"'{args.folder_id}' in parents and trashed = false and (mimeType contains 'video/' or name contains '.mp4' or name contains '.mov')"
            results = service.files().list(q=query, fields="files(id, name, webViewLink, size)").execute()
            files = results.get("files", [])
            print(f"Found {len(files)} video files in folder.")

            for f in files:
                print(f"→ Queuing {f['name']} ({f.get('size', '?')} bytes)...")
                item_payload = {
                    "pipeline": args.pipeline,
                    "title": Path(f["name"]).stem.replace("_", " ").replace("-", " ").title(),
                    "source": f"Google Drive ({args.pipeline})",
                    "drive_link": f.get("webViewLink", f"https://drive.google.com/file/d/{f['id']}/view"),
                    "status": "review",
                }
                res = httpx.post(f"{api_base}/api/queue", json=item_payload, headers=headers, timeout=30)
                if res.status_code == 200:
                    print(f"  ✓ Added to queue: {res.json().get('id')}")
                else:
                    print(f"  ⚠️ Error queuing: {res.status_code} {res.text}")

        except ImportError:
            print("To use direct service account, install: pip3 install google-api-python-client google-auth")
            sys.exit(1)
    else:
        print("ℹ️ No local service account JSON specified.")
        print(f"Checking access to folder: https://drive.google.com/drive/folders/{args.folder_id}")
        print("\nTo grant automatic access to this folder:")
        print("1. In Google Drive, right-click the folder → Share")
        print("2. Add your service account email (or set General Access to 'Anyone with the link can view')")
        print("3. Alternatively, supply --service-account /path/to/service_account.json")

if __name__ == "__main__":
    main()
