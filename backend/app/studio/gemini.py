"""Gemini calls for the Studio: JSON answers, image understanding, and web
research grounded in Google Search (each statement comes back with the
source pages that support it)."""

from __future__ import annotations

import base64
import json
import logging
import pathlib
import re
import time
from typing import Any

import httpx

from ..config import settings

logger = logging.getLogger("scrapper.studio.gemini")
BASE = "https://generativelanguage.googleapis.com/v1beta/models"
TIMEOUT = 180.0


class GeminiError(Exception):
    pass


def _post(body: dict[str, Any], model: str | None = None, retries: int = 3) -> dict[str, Any]:
    if not settings.gemini_api_key:
        raise GeminiError("GEMINI_API_KEY is not set on the server")
    from .. import control, costs
    url = f"{BASE}/{model or settings.gemini_model}:generateContent"
    last = ""
    for attempt in range(retries):
        control.check()  # Stop lands before the next (slow) model call
        try:
            costs.check_budget()
        except costs.BudgetExceeded as exc:
            raise GeminiError(str(exc)) from exc
        res = httpx.post(url, json=body, headers={"x-goog-api-key": settings.gemini_api_key}, timeout=TIMEOUT)
        if res.is_success:
            data = res.json()
            costs.record_gemini(model or settings.gemini_model, data.get("usageMetadata") or {},
                                grounded="tools" in body)
            return data
        last = f"HTTP {res.status_code}: {res.text[:300]}"
        if res.status_code not in (429, 500, 502, 503, 504):
            break
        time.sleep(2 ** attempt * 2)  # bounded backoff on rate limits / server errors
    raise GeminiError(f"Gemini {model or settings.gemini_model} failed: {last}")


def _text(data: dict[str, Any]) -> str:
    cands = data.get("candidates") or []
    if not cands:
        raise GeminiError(f"Gemini returned no answer: {str(data)[:300]}")
    parts = (cands[0].get("content") or {}).get("parts") or []
    return "".join(p.get("text", "") for p in parts).strip()


def _image_part(path: pathlib.Path) -> dict[str, Any]:
    mime = "image/png" if path.suffix.lower() == ".png" else "image/jpeg"
    return {"inline_data": {"mime_type": mime, "data": base64.b64encode(path.read_bytes()).decode()}}


JSON_ATTEMPTS = 3


def parse_json(text: str) -> Any:
    text = re.sub(r"^```(?:json)?\s*", "", text.strip())
    text = re.sub(r"\s*```$", "", text).strip()
    return json.loads(text)


def ask_json(prompt: str, images: list[pathlib.Path] | None = None, temperature: float = 0.4,
             max_tokens: int = 16384) -> Any:
    """JSON answer. A cut-off or malformed answer is asked again (up to
    JSON_ATTEMPTS). Thinking tokens count toward maxOutputTokens on Gemini 3.x,
    so an answer that stopped on MAX_TOKENS is retried with double the room —
    Lanterns' script failed that way at char 1738 (2026-10-03)."""
    parts: list[dict[str, Any]] = [_image_part(p) for p in (images or [])]
    parts.append({"text": prompt})
    last = ""
    for attempt in range(JSON_ATTEMPTS):
        data = _post({
            "contents": [{"parts": parts}],
            "generationConfig": {"temperature": temperature, "maxOutputTokens": max_tokens,
                                 "responseMimeType": "application/json"},
        })
        finish = ((data.get("candidates") or [{}])[0]).get("finishReason") or "?"
        try:
            return parse_json(_text(data))
        except json.JSONDecodeError as exc:
            last = f"{exc} (finish reason {finish}, attempt {attempt + 1} of {JSON_ATTEMPTS})"
            logger.warning("Gemini returned invalid JSON: %s", last)
            if finish == "MAX_TOKENS":
                max_tokens = min(max_tokens * 2, 65536)
    raise GeminiError(f"Gemini returned invalid JSON: {last}")


def research(prompt: str) -> dict[str, Any]:
    """Web research with Google Search grounding.

    Returns {"text", "sources": [{"title","url"}], "claims": [{"text","sources":[i...]}]}
    where each claim is a span of the answer and the source indexes that support it."""
    data = _post({
        "contents": [{"parts": [{"text": prompt}]}],
        "tools": [{"google_search": {}}],
        "generationConfig": {"temperature": 0.2, "maxOutputTokens": 8192},
    })
    text = _text(data)
    meta = ((data.get("candidates") or [{}])[0]).get("groundingMetadata") or {}
    sources = [{"title": (c.get("web") or {}).get("title", ""), "url": (c.get("web") or {}).get("uri", "")}
               for c in meta.get("groundingChunks", [])]
    claims = [{"text": (s.get("segment") or {}).get("text", ""),
               "sources": s.get("groundingChunkIndices", [])}
              for s in meta.get("groundingSupports", [])]
    return {"text": text, "sources": sources, "claims": claims,
            "queries": meta.get("webSearchQueries", [])}
