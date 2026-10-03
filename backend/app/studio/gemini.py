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


class BatchUnavailable(Exception):
    """Batch Mode didn't work out (refused, failed, too slow): use normal calls."""


BATCH_MAX_BYTES = 19_000_000    # inline batch requests must stay under 20 MB


def ask_json_batch(items: list[dict[str, Any]], max_wait: int = 1800, poll: int = 20,
                   max_tokens: int = 16384) -> dict[str, Any]:
    """Gemini Batch Mode: the same JSON questions at 50% of the price, answered
    asynchronously (Google targets 24 h; usually minutes). For work nobody is
    waiting on (automation runs). items: [{"key", "prompt", "images", "temperature"}].
    Returns {key: parsed JSON} for the answers that came back valid; the caller
    asks the missing ones the normal way. Raises BatchUnavailable when the whole
    batch can't be used (refused, failed, expired, or slower than max_wait —
    then it is cancelled)."""
    if not settings.gemini_api_key:
        raise GeminiError("GEMINI_API_KEY is not set on the server")
    from .. import control, costs
    costs.check_budget()       # DailyCapReached / BudgetExceeded propagate like any paid call
    model = settings.gemini_model
    reqs = []
    for it in items:
        parts = [_image_part(p) for p in (it.get("images") or [])] + [{"text": it["prompt"]}]
        reqs.append({"request": {"contents": [{"parts": parts}],
                                 "generationConfig": {"temperature": it.get("temperature", 0.4),
                                                      "maxOutputTokens": max_tokens,
                                                      "responseMimeType": "application/json"}},
                     "metadata": {"key": str(it["key"])}})
    body = {"batch": {"display_name": f"scrapper-{int(time.time())}",
                      "input_config": {"requests": {"requests": reqs}}}}
    raw = json.dumps(body)
    if len(raw) > BATCH_MAX_BYTES:
        raise BatchUnavailable(f"batch too large for inline mode ({len(raw) // 1_000_000} MB)")
    headers = {"x-goog-api-key": settings.gemini_api_key, "content-type": "application/json"}
    res = httpx.post(f"{BASE}/{model}:batchGenerateContent", content=raw, headers=headers, timeout=TIMEOUT)
    if not res.is_success:
        raise BatchUnavailable(f"batch refused: HTTP {res.status_code}: {res.text[:200]}")
    name = res.json().get("name")
    if not name:
        raise BatchUnavailable(f"batch refused: no job name in {res.text[:200]}")
    url = f"https://generativelanguage.googleapis.com/v1beta/{name}"
    deadline = time.time() + max_wait
    while True:
        control.check()       # Stop works while waiting
        r = httpx.get(url, headers=headers, timeout=60)
        data = r.json() if r.is_success else {}
        state = str((data.get("metadata") or {}).get("state") or data.get("state") or "")
        if "SUCCEEDED" in state or data.get("done") and "FAILED" not in state:
            break
        if any(x in state for x in ("FAILED", "CANCELLED", "EXPIRED")):
            raise BatchUnavailable(f"batch {name} ended {state}")
        if time.time() > deadline:
            httpx.post(f"{url}:cancel", headers=headers, timeout=30)
            raise BatchUnavailable(f"batch {name} not done after {max_wait // 60} min — cancelled")
        control.progress(f"Gemini batch (half price) {state.replace('JOB_STATE_', '').replace('BATCH_STATE_', '').lower() or 'queued'}…")
        time.sleep(poll)
    resp = data.get("response") or data.get("dest") or {}
    inl = resp.get("inlinedResponses") or resp.get("inlined_responses") or {}
    if isinstance(inl, dict):
        inl = inl.get("inlinedResponses") or inl.get("inlined_responses") or []
    out: dict[str, Any] = {}
    for n, item in enumerate(inl):
        key = str(((item.get("metadata") or {}).get("key")) or (items[n]["key"] if n < len(items) else n))
        answer = item.get("response")
        if not answer:
            continue
        costs.record_gemini(model, answer.get("usageMetadata") or {}, batch=True)
        try:
            out[key] = parse_json(_text(answer))
        except (json.JSONDecodeError, GeminiError):
            continue          # asked again the normal way by the caller
    logger.info("Gemini batch %s: %s of %s answers usable", name, len(out), len(items))
    return out


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
