"""Single shared-token auth with HMAC signed URLs and rate limiting.

Security features:
  - Fail-closed in production (refuses open access if ACCESS_TOKEN is missing)
  - Timing-attack safe token comparison (hmac.compare_digest)
  - Short-lived HMAC signed URLs for media and downloads (?exp=...&sig=...) so master token is never leaked in URLs
  - Per-IP rate limiting (120 req/min)
"""

from __future__ import annotations

import hashlib
import hmac
import os
import threading
import time
from typing import Any

from fastapi import Header, HTTPException, Query, Request, status

from .config import settings

_rate_limits: dict[str, list[float]] = {}
_rl_lock = threading.Lock()


def is_production() -> bool:
    """Return True if running in a production or deployed cloud environment."""
    return bool(
        os.getenv("RAILWAY_ENVIRONMENT")
        or os.getenv("VERCEL")
        or os.getenv("ENVIRONMENT", "").lower() in ("production", "prod")
    )


def sign_url(path: str, expires_in_seconds: int = 86400) -> str:
    """Generate HMAC-SHA256 signature parameters for media/downloads."""
    secret = settings.access_token or "dev-secret-scrapper"
    exp = int(time.time()) + expires_in_seconds
    msg = f"{path}:{exp}".encode()
    sig = hmac.new(secret.encode(), msg, hashlib.sha256).hexdigest()[:32]
    return f"exp={exp}&sig={sig}"


def verify_signature(path: str, exp: Any, sig: Any) -> bool:
    """Verify HMAC signature and expiration timestamp in constant time."""
    if not exp or not sig or not isinstance(sig, str):
        return False
    try:
        exp_int = int(exp)
    except (ValueError, TypeError):
        return False
    if exp_int < int(time.time()):
        return False
    secret = settings.access_token or "dev-secret-scrapper"
    msg = f"{path}:{exp_int}".encode()
    expected = hmac.new(secret.encode(), msg, hashlib.sha256).hexdigest()[:32]
    return hmac.compare_digest(sig, expected)


def client_ip(request: Request) -> str:
    """The real visitor. Behind Railway's proxy request.client is the proxy, so
    every visitor shared one bucket; the first X-Forwarded-For hop is the client."""
    fwd = request.headers.get("x-forwarded-for", "")
    if fwd:
        return fwd.split(",")[0].strip()
    return request.client.host if request.client else "unknown"


_bad_tokens: dict[str, list[float]] = {}
BAD_TOKEN_LIMIT, BAD_TOKEN_WINDOW, BAD_TOKEN_BLOCK = 20, 600.0, 900.0


def _check_lockout(ip: str) -> None:
    now = time.time()
    with _rl_lock:
        hits = [t for t in _bad_tokens.get(ip, []) if t > now - BAD_TOKEN_BLOCK]
        _bad_tokens[ip] = hits
        recent = [t for t in hits if t > now - BAD_TOKEN_WINDOW]
        if len(recent) >= BAD_TOKEN_LIMIT:
            raise HTTPException(status.HTTP_429_TOO_MANY_REQUESTS,
                                "Too many wrong access tokens from this address; try again in 15 minutes.",
                                headers={"Retry-After": "900"})


def _note_bad_token(ip: str) -> None:
    with _rl_lock:
        _bad_tokens.setdefault(ip, []).append(time.time())


def check_rate_limit(request: Request, max_per_minute: int = 600) -> None:
    """Sliding-window in-memory rate limiter per client IP. 600/min: the site's
    own polling (jobs 3 s, queue 4 s, Studio 3 s) plus bulk actions stay far below."""
    ip = client_ip(request)
    now = time.time()
    cutoff = now - 60.0
    with _rl_lock:
        timestamps = _rate_limits.setdefault(ip, [])
        _rate_limits[ip] = [t for t in timestamps if t > cutoff]
        if len(_rate_limits[ip]) >= max_per_minute:
            raise HTTPException(
                status.HTTP_429_TOO_MANY_REQUESTS,
                "Rate limit exceeded. Please wait a moment before sending more requests.",
                headers={"Retry-After": "60"},
            )
        _rate_limits[ip].append(now)


def require_token(
    request: Request,
    authorization: str | None = Header(default=None),
    x_access_token: str | None = Header(default=None),
    token: str | None = Query(default=None),
    exp: int | None = Query(default=None),
    sig: str | None = Query(default=None),
) -> None:
    # 1. Fail-closed: in production, missing ACCESS_TOKEN is a critical misconfiguration
    if is_production() and not settings.access_token:
        raise HTTPException(
            status.HTTP_500_INTERNAL_SERVER_ERROR,
            "Server security error: ACCESS_TOKEN must be configured in production environments.",
        )

    # 2. In local dev with no access_token set, allow open access
    if not settings.access_token:
        return

    # 3. Check HMAC signed URL (used for media, thumbnail, or download links)
    exp_val = exp if isinstance(exp, (int, float, str)) else None
    sig_val = sig if isinstance(sig, str) else None
    if exp_val is not None and sig_val is not None:
        if verify_signature(request.url.path, exp_val, sig_val):
            check_rate_limit(request)
            return

    # 4. Check token from header or query string
    tok_val = token if isinstance(token, str) else None
    hdr_val = x_access_token if isinstance(x_access_token, str) else None
    supplied = hdr_val or tok_val
    if authorization and isinstance(authorization, str) and authorization.lower().startswith("bearer "):
        supplied = authorization[7:]

    ip = client_ip(request)
    _check_lockout(ip)
    if not supplied or not hmac.compare_digest(supplied.encode(), settings.access_token.encode()):
        _note_bad_token(ip)
        raise HTTPException(status.HTTP_401_UNAUTHORIZED, "invalid or missing access token")

    check_rate_limit(request)
