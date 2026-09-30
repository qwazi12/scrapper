"""Outstand API client for managing social accounts and publishing posts.

Docs: https://www.outstand.so/docs/
Endpoints:
  GET  /v1/social-accounts
  POST /v1/media/upload
  PUT  {upload_url}
  POST /v1/media/{mediaId}/confirm
  POST /v1/posts/
"""

from __future__ import annotations

import logging
import mimetypes
import pathlib
from typing import Any

import httpx

from ..config import settings

logger = logging.getLogger("scrapper.social.outstand")


class OutstandError(Exception):
    """Custom exception for Outstand API errors."""
    def __init__(self, message: str, status_code: int = 500, details: Any = None):
        super().__init__(message)
        self.message = message
        self.status_code = status_code
        self.details = details


def _get_headers() -> dict[str, str]:
    if not settings.outstand_api_key:
        raise OutstandError(
            "OUTSTAND_API_KEY is not configured in environment variables.",
            status_code=400,
        )
    return {
        "Authorization": f"Bearer {settings.outstand_api_key}",
        "Content-Type": "application/json",
    }


async def list_social_accounts() -> list[dict[str, Any]]:
    """Retrieve connected social accounts from Outstand."""
    url = f"{settings.outstand_base_url.rstrip('/')}/social-accounts"
    headers = _get_headers()

    async with httpx.AsyncClient(timeout=20.0) as client:
        res = await client.get(url, headers=headers)
        if not res.is_success:
            logger.error("Outstand list accounts failed: %s %s", res.status_code, res.text)
            raise OutstandError(
                f"Failed to fetch accounts from Outstand: {res.text}",
                status_code=res.status_code,
            )
        data = res.json()
        # Data format is typically {"data": [...]}
        return data.get("data", [])


async def upload_media(file_path: pathlib.Path) -> dict[str, Any]:
    """Upload media file to Outstand via 3-step presigned flow.

    1. POST /v1/media/upload -> get upload_url + id
    2. PUT {upload_url} -> raw bytes
    3. POST /v1/media/{id}/confirm -> get public media URL
    """
    if not file_path.exists():
        raise OutstandError(f"Video file not found: {file_path}", status_code=404)

    file_size = file_path.stat().st_size
    filename = file_path.name
    content_type, _ = mimetypes.guess_type(str(file_path))
    if not content_type:
        content_type = "video/mp4"

    headers = _get_headers()
    base = settings.outstand_base_url.rstrip("/")

    async with httpx.AsyncClient(timeout=300.0) as client:
        # Step 1: Request upload URL
        step1_res = await client.post(
            f"{base}/media/upload",
            headers=headers,
            json={"filename": filename, "content_type": content_type},
        )
        if not step1_res.is_success:
            logger.error("Outstand media upload step 1 failed: %s %s", step1_res.status_code, step1_res.text)
            raise OutstandError(
                f"Outstand upload request failed: {step1_res.text}",
                status_code=step1_res.status_code,
            )
        step1_json = step1_res.json()
        media_data = step1_json.get("data", {})
        media_id = media_data.get("id")
        upload_url = media_data.get("upload_url")

        if not media_id or not upload_url:
            raise OutstandError(
                f"Invalid upload response from Outstand: {step1_json}",
                status_code=502,
            )

        # Step 2: PUT raw binary bytes to upload_url
        with open(file_path, "rb") as f:
            file_bytes = f.read()

        put_res = await client.put(
            upload_url,
            headers={"Content-Type": content_type},
            content=file_bytes,
        )
        if not put_res.is_success:
            logger.error("Outstand S3/R2 binary upload failed: %s %s", put_res.status_code, put_res.text)
            raise OutstandError(
                f"Failed to upload binary file to storage: {put_res.status_code}",
                status_code=put_res.status_code,
            )

        # Step 3: Confirm upload
        step3_res = await client.post(
            f"{base}/media/{media_id}/confirm",
            headers=headers,
            json={"size": file_size},
        )
        if not step3_res.is_success:
            logger.error("Outstand media confirm failed: %s %s", step3_res.status_code, step3_res.text)
            raise OutstandError(
                f"Outstand media confirmation failed: {step3_res.text}",
                status_code=step3_res.status_code,
            )

        confirm_data = step3_res.json()
        public_url = confirm_data.get("url") or (confirm_data.get("data", {}).get("url"))
        return {
            "media_id": media_id,
            "filename": filename,
            "size": file_size,
            "url": public_url,
            "raw": confirm_data,
        }


async def create_social_post(
    account_ids: list[str],
    content: str,
    media_url: str | None = None,
    filename: str = "video.mp4",
    scheduled_at: str | None = None,
) -> dict[str, Any]:
    """Create and publish or schedule a post on Outstand.

    Args:
        account_ids: List of account IDs (from list_social_accounts())
        content: Caption / text of post
        media_url: Public Outstand media URL (if video/image attached)
        filename: Filename of the media
        scheduled_at: ISO 8601 string for future posting (optional)
    """
    if not account_ids:
        raise OutstandError("At least one target social account ID is required.", status_code=400)

    container: dict[str, Any] = {"content": content}
    if media_url:
        container["media"] = [{"url": media_url, "filename": filename}]

    payload: dict[str, Any] = {
        "content": content,
        "containers": [container],
        "accounts": account_ids,
    }
    if scheduled_at:
        payload["scheduledAt"] = scheduled_at

    url = f"{settings.outstand_base_url.rstrip('/')}/posts/"
    headers = _get_headers()

    async with httpx.AsyncClient(timeout=30.0) as client:
        res = await client.post(url, headers=headers, json=payload)
        if not res.is_success:
            logger.error("Outstand create post failed: %s %s", res.status_code, res.text)
            raise OutstandError(
                f"Outstand post creation failed: {res.text}",
                status_code=res.status_code,
            )
        return res.json()
