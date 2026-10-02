"""Chirp 3 HD narration (same voice + REST call as manhwa), cached by text.

Cache key = sha1(voice | spoken text), so editing one sentence re-voices only
that sentence and an unchanged script costs nothing to re-render.
"""

from __future__ import annotations

import base64
import hashlib
import pathlib
import re
import shutil

import httpx

from ..config import settings

URL = "https://texttospeech.googleapis.com/v1/text:synthesize"

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


def synth(text: str, dest: pathlib.Path) -> pathlib.Path:
    """Voice one sentence to MP3 at dest (from cache when possible)."""
    from .. import control
    control.check()  # Stop between sentences
    spoken = speakable(text)
    key = hashlib.sha1(f"{settings.tts_voice}|{spoken}".encode()).hexdigest()
    cached = cache_dir() / f"{key}.mp3"
    if not (cached.exists() and cached.stat().st_size > 0):
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
        tmp = cached.with_suffix(".tmp")
        tmp.write_bytes(base64.b64decode(res.json()["audioContent"]))
        tmp.replace(cached)  # atomic: no half-written cache entries
    shutil.copyfile(cached, dest)
    return dest
