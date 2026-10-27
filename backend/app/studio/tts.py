"""Narration for LongForm breakdowns and countdowns, cached by text.

Owner (2026-10-13): Google Gemini text-to-speech — model gemini-3.8-flash-tts, prebuilt voice
Charon, every line carrying the style "energetic, fast-paced YouTube recap host".
Gemini API (API key) speech-generation guide: v1beta/interactions is the documented call —
generation_config.speech_config=[{"voice"}], speech_metadata annotation on the line's text; the
audio is the last steps[].content[] audio block (WAV). The "Interactions: not supported" note is
the Vertex/Enterprise page, so generateContent is only a fallback.
The older Chirp 3 HD voice stays available with TTS_ENGINE=chirp.

Cache key = sha1(engine | model | voice | style | spoken text), so editing one sentence
re-voices only that sentence, an unchanged script costs nothing to re-render, and changing
the voice or style re-voices everything once.
"""

from __future__ import annotations

import base64
import hashlib
import pathlib
import random
import re
import shutil
import subprocess
import time

import httpx

from ..config import settings

URL = "https://texttospeech.googleapis.com/v1/text:synthesize"
INTERACTIONS_URL = "https://generativelanguage.googleapis.com/v1beta/interactions"
# Interactions is the Gemini API's documented TTS call; generateContent (the Vertex page's form) is
# the fallback. Whichever the API key works with is remembered.
GENERATE_URL = "https://generativelanguage.googleapis.com/v1beta/models/{model}:generateContent"
_working_api: dict[str, str] = {}

# What the narrator SAYS (manhwa speech_text policy): no quote marks; harsher
# words softened; slurs dropped. The script keeps its words; only audio changes.
_SOFTEN = [(r"\bwhat the fuck\b", "what the hell"), (r"\bfuck(?:ing|in')\b", "freaking"),
           (r"\bfuck(?:ed)\b", "screwed"), (r"\bfuck\b", "screw"), (r"\bbullshit\b", "nonsense"),
           (r"\bshit\b", "crap"), (r"\bbitch(?:es)?\b", "wretch")]
_SOFTEN_RX = [(re.compile(p, re.I), r) for p, r in _SOFTEN]
_QUOTES = str.maketrans({'"': None, "“": None, "”": None})


class TTSError(Exception):
    pass


def speakable(text: str) -> str:
    text = text.translate(_QUOTES)
    for rx, repl in _SOFTEN_RX:
        text = rx.sub(repl, text)
    return " ".join(text.split())


def cache_dir() -> pathlib.Path:
    d = settings.data_path / "studio" / "_ttscache"
    d.mkdir(parents=True, exist_ok=True)
    return d


def engine() -> str:
    return (settings.tts_engine or "gemini").lower()


def configured() -> bool:
    return bool(settings.gemini_api_key if engine() == "gemini" else settings.tts_api_key)


def voice_label() -> str:
    if engine() == "gemini":
        return f"{settings.narration_tts_model} · {settings.narration_voice}"
    return settings.tts_voice


def _cache_key(spoken: str) -> str:
    if engine() == "gemini":
        ident = f"gemini|{settings.narration_tts_model}|{settings.narration_voice}|{settings.narration_style}|{spoken}"
    else:
        ident = f"{settings.tts_voice}|{spoken}"          # unchanged, so existing Chirp cache entries still hit
    return hashlib.sha1(ident.encode()).hexdigest()


def generate_payload(spoken: str) -> dict:
    """generateContent (Google's documented form): style on the line's part, voice in speechConfig."""
    part: dict = {"text": spoken}
    if settings.narration_style.strip():
        part["speechMetadata"] = {"style": settings.narration_style.strip()}
    return {
        "contents": [{"role": "user", "parts": [part]}],
        "generationConfig": {"responseModalities": ["AUDIO"],
                             "speechConfig": {"voiceConfig": {"voice": settings.narration_voice}}},
    }


def gemini_payload(spoken: str) -> dict:
    """The interactions request: Charon via speech_config, the style on the line's text."""
    item = {"type": "text", "text": spoken}
    if settings.narration_style.strip():
        item["annotations"] = [{"type": "speech_metadata", "style": settings.narration_style.strip()}]
    return {
        "model": settings.narration_tts_model,
        "input": [{"type": "user_input", "content": [item]}],
        "response_format": {"type": "audio", "mime_type": "audio/wav"},
        "generation_config": {"speech_config": [{"voice": settings.narration_voice}]},
    }


def _audio_from(data: dict) -> str | None:
    for cand in data.get("candidates") or []:              # generateContent
        for part in (cand.get("content") or {}).get("parts") or []:
            inline = part.get("inlineData") or part.get("inline_data") or {}
            if inline.get("data"):
                return inline["data"]
    last = None                                            # interactions: the LAST audio block
    for step in data.get("steps") or []:
        if step.get("type") == "model_output":
            for item in step.get("content") or []:
                if item.get("type") == "audio" and item.get("data"):
                    last = item["data"]
    return last or (data.get("output_audio") or {}).get("data")


def _to_mp3(audio: bytes, dest: pathlib.Path) -> float:
    """WAV (or raw 24 kHz 16-bit mono PCM) → MP3 at dest; returns seconds."""
    src = ["-i", "pipe:0"] if audio[:4] == b"RIFF" else ["-f", "s16le", "-ar", "24000", "-ac", "1", "-i", "pipe:0"]
    proc = subprocess.run(["ffmpeg", "-v", "error", "-y", *src, "-c:a", "libmp3lame", "-b:a", "128k", str(dest)],
                          input=audio, capture_output=True, timeout=120)
    if proc.returncode != 0:
        raise TTSError(f"could not convert the Gemini audio: {proc.stderr.decode(errors='ignore')[-300:]}")
    out = subprocess.run(["ffprobe", "-v", "error", "-show_entries", "format=duration", "-of", "csv=p=0", str(dest)],
                         capture_output=True, text=True, timeout=30).stdout.strip()
    return float(out or 0)


def tts_usage(data: dict) -> dict:
    u = data.get("usageMetadata") or data.get("usage") or {}
    inp = u.get("promptTokenCount") or u.get("input_tokens") or u.get("total_input_tokens")
    out = u.get("candidatesTokenCount") or u.get("output_tokens") or u.get("total_output_tokens")
    return {"input_tokens": int(inp), "output_tokens": int(out)} if inp and out else {}


def _spoken_text(payload: dict) -> str:
    parts = [c.get("text", "") for i in payload.get("input") or [] for c in i.get("content") or []]
    parts += [p.get("text", "") for c in payload.get("contents") or [] for p in c.get("parts") or []]
    return " ".join(x for x in parts if x)


def _post(url: str, payload: dict) -> httpx.Response | None:
    """POST with bounded retries + jitter on rate limits / server errors."""
    res = None
    for attempt in range(4):
        try:
            from .. import costs, budget_reservations as br
            model = payload.get("model") or settings.narration_tts_model
            with br.attempt("gemini-tts", br.request_bound("gemini-tts", payload, model)):
                res = httpx.post(url, json=payload, timeout=90,
                                 headers={"x-goog-api-key": settings.gemini_api_key, "content-type": "application/json"})
                if res.is_success:
                    data = res.json()
                    # Token counts under either API's names; otherwise the measured audio length
                    # (24 kHz 16-bit mono) — so the cost is real and the budget hold settles (2026-10-19:
                    # every voice line left a $0.02 hold stuck and was recorded at ~$0).
                    b64 = _audio_from(data) or ""
                    costs.record_gemini_tts(model, tts_usage(data), len(b64) * 3 / 4 / 48000, len(_spoken_text(payload)))
                else:
                    br.not_billed()                   # error answers are not billed

        except httpx.HTTPError as exc:
            if attempt == 3:
                raise TTSError(f"Gemini text-to-speech unreachable: {exc}") from exc
            time.sleep(2 ** attempt + random.random())
            continue
        if res.status_code in (429, 500, 502, 503, 504) and attempt < 3:
            time.sleep(2 ** (attempt + 1) + random.random())
            continue
        break
    return res


def _gemini(spoken: str, dest: pathlib.Path) -> None:
    from .. import costs
    if not settings.gemini_api_key:
        raise TTSError("GEMINI_API_KEY is not set on the server (Gemini text-to-speech)")
    model = settings.narration_tts_model
    routes = {"generate": (GENERATE_URL.format(model=model), generate_payload(spoken)),
              "interactions": (INTERACTIONS_URL, gemini_payload(spoken))}
    order = [_working_api["api"]] if _working_api.get("api") else ["interactions", "generate"]
    errors = []
    for api in order:
        url, payload = routes[api]
        res = _post(url, payload)
        if res is not None and res.is_success:
            data = res.json()
            b64 = _audio_from(data)
            if b64:
                _working_api["api"] = api
                seconds = _to_mp3(base64.b64decode(b64), dest)
                return
            errors.append(f"{api}: no audio in the response")
            continue
        # The key is in a header, never in the URL or this message.
        errors.append(f"{api}: HTTP {getattr(res, 'status_code', '?')} {getattr(res, 'text', '')[:200]}")
        if res is not None and res.status_code in (401, 403, 429):
            break                                           # key/quota problem: the other form won't help
    raise TTSError("Gemini text-to-speech failed — " + " | ".join(errors))


def _chirp(spoken: str, dest: pathlib.Path) -> None:
    from .. import costs
    if not settings.tts_api_key:
        raise TTSError("TTS_API_KEY is not set on the server (Google Text-to-Speech key)")
    lang = "-".join(settings.tts_voice.split("-")[:2])
    from .. import budget_reservations as br
    bound = len(spoken) / 1e6 * costs.settings_dict()["tts_usd_per_million_chars"]
    with br.attempt("tts", bound):
        res = httpx.post(URL, params={"key": settings.tts_api_key}, timeout=60, json={
            "input": {"text": spoken},
            "voice": {"languageCode": lang, "name": settings.tts_voice},
            "audioConfig": {"audioEncoding": "MP3"},
        })
        if res.is_success: costs.record_tts(len(spoken), settings.tts_voice)
        else: br.not_billed()
    if not res.is_success:
        # Never echo the URL (it carries the key) — body only.
        raise TTSError(f"Text-to-Speech failed ({res.status_code}): {res.text[:300]}")
    dest.write_bytes(base64.b64decode(res.json()["audioContent"]))


def synth(text: str, dest: pathlib.Path) -> pathlib.Path:
    """Voice one line to MP3 at dest (from cache when possible)."""
    from .. import control
    control.check()  # Stop between sentences
    spoken = speakable(text)
    cached = cache_dir() / f"{_cache_key(spoken)}.mp3"
    from uuid import uuid4
    from .. import cache_use,file_use
    tmp = cached.with_suffix(f".{uuid4().hex}.tmp.mp3")
    # Recheck after ownership: another process may have completed the same
    # voice while we waited. The partial provider file is protected from LRU
    # too; consumers receive an independent copy before the claim is released.
    with cache_use.writer([cached,tmp,dest]):
        try:
            if not (cached.exists() and cached.stat().st_size > 0):
                from .. import costs
                try:costs.check_budget()
                except costs.BudgetExceeded as exc:raise TTSError(str(exc)) from exc
                (_gemini if engine() == "gemini" else _chirp)(spoken, tmp)
                control.check();file_use.assert_current()
                tmp.replace(cached)
            control.check();file_use.assert_current()
            shutil.copyfile(cached, dest)
        finally:
            tmp.unlink(missing_ok=True)  # atomic: no half-written cache entries
    return dest
