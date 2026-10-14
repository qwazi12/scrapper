"""Narration for LongForm breakdowns and countdowns, cached by text.

Owner (2026-10-13): Google Gemini text-to-speech — model gemini-3.8-flash-tts, prebuilt voice
Charon, every line carrying the style "energetic, fast-paced YouTube recap host" — through the
Gemini v1beta/interactions endpoint:
  generation_config.speech_config = [{"voice": "Charon"}]
  the line's text carries an annotation {"type": "speech_metadata", "style": "<style>"}.
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
    for step in data.get("steps") or []:
        if step.get("type") == "model_output":
            for item in step.get("content") or []:
                if item.get("type") == "audio" and item.get("data"):
                    return item["data"]
    return (data.get("output_audio") or {}).get("data")


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


def _gemini(spoken: str, dest: pathlib.Path) -> None:
    from .. import costs
    if not settings.gemini_api_key:
        raise TTSError("GEMINI_API_KEY is not set on the server (Gemini text-to-speech)")
    payload = gemini_payload(spoken)
    res = None
    for attempt in range(4):                              # bounded retries with jitter on rate limits / server errors
        try:
            res = httpx.post(INTERACTIONS_URL, json=payload, timeout=90,
                             headers={"x-goog-api-key": settings.gemini_api_key, "content-type": "application/json"})
        except httpx.HTTPError as exc:
            if attempt == 3:
                raise TTSError(f"Gemini text-to-speech unreachable: {exc}") from exc
            time.sleep(2 ** attempt + random.random())
            continue
        if res.status_code in (429, 500, 502, 503, 504) and attempt < 3:
            time.sleep(2 ** (attempt + 1) + random.random())
            continue
        break
    if res is None or not res.is_success:
        # The key is in a header, never in the URL or this message.
        raise TTSError(f"Gemini text-to-speech failed ({getattr(res, 'status_code', '?')}): {getattr(res, 'text', '')[:300]}")
    data = res.json()
    b64 = _audio_from(data)
    if not b64:
        raise TTSError("Gemini text-to-speech returned no audio")
    seconds = _to_mp3(base64.b64decode(b64), dest)
    costs.record_gemini_tts(settings.narration_tts_model, data.get("usage") or data.get("usageMetadata") or {},
                            seconds, len(spoken))


def _chirp(spoken: str, dest: pathlib.Path) -> None:
    from .. import costs
    if not settings.tts_api_key:
        raise TTSError("TTS_API_KEY is not set on the server (Google Text-to-Speech key)")
    lang = "-".join(settings.tts_voice.split("-")[:2])
    res = httpx.post(URL, params={"key": settings.tts_api_key}, timeout=60, json={
        "input": {"text": spoken},
        "voice": {"languageCode": lang, "name": settings.tts_voice},
        "audioConfig": {"audioEncoding": "MP3"},
    })
    if not res.is_success:
        # Never echo the URL (it carries the key) — body only.
        raise TTSError(f"Text-to-Speech failed ({res.status_code}): {res.text[:300]}")
    costs.record_tts(len(spoken), settings.tts_voice)  # only real synthesis is billed, never a cache hit
    dest.write_bytes(base64.b64decode(res.json()["audioContent"]))


def synth(text: str, dest: pathlib.Path) -> pathlib.Path:
    """Voice one line to MP3 at dest (from cache when possible)."""
    from .. import control
    control.check()  # Stop between sentences
    spoken = speakable(text)
    cached = cache_dir() / f"{_cache_key(spoken)}.mp3"
    if not (cached.exists() and cached.stat().st_size > 0):
        from .. import costs
        try:
            costs.check_budget()
        except costs.BudgetExceeded as exc:
            raise TTSError(str(exc)) from exc
        tmp = cached.with_suffix(".tmp.mp3")
        (_gemini if engine() == "gemini" else _chirp)(spoken, tmp)
        tmp.replace(cached)  # atomic: no half-written cache entries
    shutil.copyfile(cached, dest)
    return dest
