"""Single shared-token auth. If ACCESS_TOKEN is unset (local dev), auth is open.

Accepts the token three ways so every access path works:
  - `x-access-token` header (used by the app's fetch/XHR calls)
  - `Authorization: Bearer <token>` header
  - `?token=<token>` query param (needed for browser-native GETs that can't set
    headers: <a href> downloads, <img src> thumbnails, and the SSE log stream)
"""

from __future__ import annotations

from fastapi import Header, HTTPException, Query, status

from .config import settings


def require_token(
    authorization: str | None = Header(default=None),
    x_access_token: str | None = Header(default=None),
    token: str | None = Query(default=None),
) -> None:
    if not settings.access_token:
        return  # open in local dev
    supplied = x_access_token or token
    if authorization and authorization.lower().startswith("bearer "):
        supplied = authorization[7:]
    if supplied != settings.access_token:
        raise HTTPException(status.HTTP_401_UNAUTHORIZED, "invalid or missing access token")
