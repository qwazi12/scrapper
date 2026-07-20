"""Single shared-token auth. If ACCESS_TOKEN is unset (local dev), auth is open."""

from __future__ import annotations

from fastapi import Header, HTTPException, status

from .config import settings


def require_token(authorization: str | None = Header(default=None),
                  x_access_token: str | None = Header(default=None)) -> None:
    if not settings.access_token:
        return  # open in local dev
    supplied = x_access_token
    if authorization and authorization.lower().startswith("bearer "):
        supplied = authorization[7:]
    if supplied != settings.access_token:
        raise HTTPException(status.HTTP_401_UNAUTHORIZED, "invalid or missing access token")
