"""Upload-Post client — the only social publisher.

Facts verified against the live API in the manhwa migration
(github.com/qwazi12/manhwa docs/UPLOAD_POST_MIGRATION.md):
  base   https://api.upload-post.com/api
  auth   Authorization: Apikey <UPLOADPOST_API_KEY>
  POST   /upload                    multipart; user, title, platform[], video; async_upload=true -> request_id
  GET    /uploadposts/status        ?request_id= -> {status, results:[{profile_username, platform,
                                                       success, post_url, error_message}]}
  GET    /uploadposts/users         -> profiles[].username + profiles[].social_accounts{network: details}

One request publishes to ONE profile (`user`). Account ids here are
"<profile>:<network>", so targets are grouped by profile and each profile gets
its own upload.
"""

from __future__ import annotations

import logging
import pathlib
from collections import defaultdict
from typing import Any

import httpx

from ..config import settings

logger = logging.getLogger("scrapper.social.upload_post")

API_BASE = "https://api.upload-post.com/api"
TIMEOUT = 30.0
UPLOAD_TIMEOUT = 1200.0  # multipart body carries the whole video
MANAGE_URL = "https://app.upload-post.com/manage-users"


class UploadPostError(Exception):
    def __init__(self, message: str, status_code: int = 502):
        super().__init__(message)
        self.message = message
        self.status_code = status_code


def configured() -> bool:
    return bool(settings.uploadpost_api_key)


def _headers() -> dict[str, str]:
    if not settings.uploadpost_api_key:
        raise UploadPostError("UPLOADPOST_API_KEY is not set on the server", status_code=400)
    return {"Authorization": f"Apikey {settings.uploadpost_api_key}", "Accept": "application/json"}


def _raise_for(res: httpx.Response, what: str) -> None:
    if not res.is_success:
        # Body only — never headers (they carry the key).
        raise UploadPostError(f"{what} failed ({res.status_code}): {res.text[:400]}", status_code=res.status_code)


def split_account(account_id: str) -> tuple[str, str]:
    """"<profile>:<network>" -> (profile, network)."""
    profile, sep, network = account_id.partition(":")
    if not sep or not profile or not network:
        raise UploadPostError(f"Not an Upload-Post account id: {account_id!r} (expected profile:network)", 400)
    return profile, network


def expand_targets(targets: list[str], connected: list[dict[str, Any]]) -> list[str]:
    """Turn picked targets into concrete "<profile>:<network>" accounts.

    "<profile>:*" means every channel connected to that profile right now.
    Raises if a target isn't connected (nothing is sent)."""
    by_profile: dict[str, list[str]] = defaultdict(list)
    for a in connected:
        by_profile[a["profile"]].append(a["id"])
    ids = {a["id"] for a in connected}
    out: list[str] = []
    missing: list[str] = []
    for t in targets:
        profile, network = split_account(t)
        if network == WHOLE_PROFILE:
            if not by_profile.get(profile):
                missing.append(f"profile '{profile}' (no channels connected)")
            out.extend(a for a in by_profile.get(profile, []) if a not in out)
        elif t in ids:
            if t not in out:
                out.append(t)
        else:
            missing.append(t)
    if missing:
        raise UploadPostError(f"Not connected in Upload-Post: {', '.join(missing)}. Re-pick accounts.", 400)
    return out


def group_by_profile(account_ids: list[str]) -> dict[str, list[str]]:
    groups: dict[str, list[str]] = defaultdict(list)
    for acc in account_ids:
        profile, network = split_account(acc)
        if network not in groups[profile]:
            groups[profile].append(network)
    return dict(groups)


WHOLE_PROFILE = "*"  # "<profile>:*" targets every channel connected to the profile


async def _profiles_raw() -> list[dict[str, Any]]:
    async with httpx.AsyncClient(timeout=TIMEOUT) as client:
        res = await client.get(f"{API_BASE}/uploadposts/users", headers=_headers())
    _raise_for(res, "Upload-Post list profiles")
    return res.json().get("profiles") or []


async def list_profiles() -> list[str]:
    """Every Upload-Post profile name, including ones with nothing connected."""
    return [p.get("username") or "" for p in await _profiles_raw() if p.get("username")]


async def list_accounts() -> list[dict[str, Any]]:
    """Connected social accounts, one row per profile+network."""
    accounts = []
    for p in await _profiles_raw():
        profile = p.get("username") or ""
        for network, details in (p.get("social_accounts") or {}).items():
            if not details:
                continue  # network present but not connected
            if isinstance(details, dict):
                name = details.get("display_name") or details.get("username") or profile
                handle = details.get("username") or name
            else:
                name = handle = str(details)
            accounts.append({
                "id": f"{profile}:{network}",
                "profile": profile,
                "network": network,
                "nickname": name,
                "username": handle,
                "isActive": True,
            })
    return accounts


async def upload_video(
    video_path: pathlib.Path,
    *,
    profile: str,
    platforms: list[str],
    title: str,
    description: str = "",
    tags: list[str] | None = None,
    privacy: str = "public",
    scheduled_date: str | None = None,
    thumbnail: pathlib.Path | None = None,
) -> str:
    """Submit one async upload for ONE profile. Returns the request_id.

    Acceptance is not publication: poll get_status(request_id) for the real
    per-platform result."""
    if not platforms:
        raise UploadPostError("No platforms selected", status_code=400)
    if not video_path.exists() or video_path.stat().st_size == 0:
        raise UploadPostError(f"Video file missing or empty: {video_path.name}", status_code=404)

    title = (title or "New video").strip()
    data: dict[str, Any] = {
        "user": profile,
        "title": title,
        "async_upload": "true",
        "platform[]": platforms,
    }
    if description:
        data["description"] = description
    if scheduled_date:
        data["scheduled_date"] = scheduled_date
    if "youtube" in platforms:
        data["youtube_title"] = title[:100]  # YouTube's title limit
        data["youtube_description"] = description
        data["privacyStatus"] = privacy
        data["selfDeclaredMadeForKids"] = "false"
        data["containsSyntheticMedia"] = "false"
        if tags:
            data["tags[]"] = tags
    if "tiktok" in platforms:
        data["tiktok_title"] = title
        data["privacy_level"] = "SELF_ONLY" if privacy == "private" else "PUBLIC_TO_EVERYONE"
    if "instagram" in platforms:
        data["instagram_title"] = title
        data["media_type"] = "REELS"

    with open(video_path, "rb") as fh:
        files: dict[str, Any] = {"video": (video_path.name, fh, "video/mp4")}
        thumb_fh = None
        # Custom thumbnail (JPG/PNG, <= 2 MB); YouTube applies it only on verified channels.
        if thumbnail and thumbnail.exists() and thumbnail.stat().st_size <= 2_000_000:
            thumb_fh = open(thumbnail, "rb")
            files["thumbnail"] = (thumbnail.name, thumb_fh, "image/jpeg")
        try:
            async with httpx.AsyncClient(timeout=UPLOAD_TIMEOUT) as client:
                res = await client.post(f"{API_BASE}/upload", headers=_headers(), data=data, files=files)
        finally:
            if thumb_fh:
                thumb_fh.close()
    _raise_for(res, f"Upload-Post upload to profile '{profile}'")
    body = res.json()
    request_id = body.get("request_id")
    if not request_id:
        raise UploadPostError(f"Upload-Post returned no request_id: {str(body)[:300]}")
    logger.info("Upload-Post accepted %s -> %s (%s)", video_path.name, profile, ",".join(platforms))
    return str(request_id)


async def get_status(request_id: str) -> dict[str, Any]:
    async with httpx.AsyncClient(timeout=TIMEOUT) as client:
        res = await client.get(f"{API_BASE}/uploadposts/status", headers=_headers(),
                               params={"request_id": request_id})
    _raise_for(res, "Upload-Post status")
    return res.json()


async def connect_url(profile: str) -> str:
    """Hosted page where the owner connects social accounts to a profile."""
    async with httpx.AsyncClient(timeout=TIMEOUT) as client:
        res = await client.post(f"{API_BASE}/uploadposts/users/generate-jwt", headers=_headers(),
                                json={"username": profile, "connect_title": "Scrapper Publishing"})
    _raise_for(res, "Upload-Post connect link")
    return res.json().get("access_url") or MANAGE_URL
