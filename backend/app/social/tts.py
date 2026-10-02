"""Gemini 3.8 Flash Text-to-Speech (TTS) integration.

Uses the Gemini Interactions API (https://generativelanguage.googleapis.com/v1beta/interactions)
to synthesize single-speaker and multi-speaker audio with turn-level emotion metadata
and inline point-in-time vocal tags (<gasp>, <sigh>, <short pause>, etc.).
"""

from __future__ import annotations

import base64
import datetime
import json
import logging
import pathlib
import re
import subprocess
import uuid
from typing import Any

import httpx

from ..config import settings

logger = logging.getLogger("scrapper.social.tts")

INTERACTIONS_URL = "https://generativelanguage.googleapis.com/v1beta/interactions"

# Curated Gemini 3.8 Studio Voices
STUDIO_VOICES = [
    {
        "id": "Puck",
        "name": "Puck",
        "gender": "neutral",
        "timbre": "Upbeat & Energetic",
        "description": "Lively, punchy delivery. Best for viral hooks, TikTok/Reels captions, and high-energy shorts.",
        "recommended_for": ["Hooks", "Shorts", "Action", "Comedy"],
    },
    {
        "id": "Charon",
        "name": "Charon",
        "gender": "male",
        "timbre": "Informative & Authoritative",
        "description": "Steady, credible baritone. Ideal for documentaries, topic breakdowns, and lore recaps.",
        "recommended_for": ["Documentary", "Explainer", "News"],
    },
    {
        "id": "Kore",
        "name": "Kore",
        "gender": "female",
        "timbre": "Firm & Confident",
        "description": "Clear, grounded, expressive delivery. Excellent for dramatic narratives and story recaps.",
        "recommended_for": ["Narration", "Drama", "Storytelling"],
    },
    {
        "id": "Fenrir",
        "name": "Fenrir",
        "gender": "male",
        "timbre": "Excitable & Intense",
        "description": "Dynamic, bold, and theatrical. Great for anime/manhwa moments and intense reactions.",
        "recommended_for": ["Anime/Manhwa", "Action", "Hype"],
    },
    {
        "id": "Aoede",
        "name": "Aoede",
        "gender": "female",
        "timbre": "Breezy & Natural",
        "description": "Conversational, warm, and relaxed. Perfect for casual reviews, vlogs, and easy dialogue.",
        "recommended_for": ["Vlog", "Casual", "Conversational"],
    },
    {
        "id": "Zephyr",
        "name": "Zephyr",
        "gender": "female",
        "timbre": "Bright & Crisp",
        "description": "Friendly, modern, and engaging. Great for intros and lifestyle clips.",
        "recommended_for": ["Commercial", "Intro", "Cheerful"],
    },
    {
        "id": "Algenib",
        "name": "Algenib",
        "gender": "male",
        "timbre": "Gravelly & Rugged",
        "description": "Deep raspy tone. Ideal for villains, dark fantasy, grit, and movie trailer lines.",
        "recommended_for": ["Movie Trailer", "Villain", "Dark Fantasy"],
    },
    {
        "id": "Algieba",
        "name": "Algieba",
        "gender": "male",
        "timbre": "Smooth & Refined",
        "description": "Polished, warm corporate tone. Excellent for professional presentations.",
        "recommended_for": ["Promo", "Luxury", "Podcast"],
    },
    {
        "id": "Sulafat",
        "name": "Sulafat",
        "gender": "female",
        "timbre": "Warm & Gentle",
        "description": "Soft, empathetic, and engaging. Best for emotional stories and human interest.",
        "recommended_for": ["Emotional", "Human Interest", "Bedtime"],
    },
    {
        "id": "Sadaltager",
        "name": "Sadaltager",
        "gender": "male",
        "timbre": "Knowledgeable & Measured",
        "description": "Thoughtful, academic cadence. Perfect for deep dives and tutorials.",
        "recommended_for": ["Deep Dive", "Tutorial", "History"],
    },
    {
        "id": "Leda",
        "name": "Leda",
        "gender": "female",
        "timbre": "Youthful & Bright",
        "description": "Young, animated, and friendly.",
        "recommended_for": ["Animation", "Youth", "Gaming"],
    },
    {
        "id": "Orus",
        "name": "Orus",
        "gender": "male",
        "timbre": "Decisive & Firm",
        "description": "Punchy, clear announcements.",
        "recommended_for": ["Announcements", "Direct", "Sports"],
    },
]

RECOMMENDED_STYLES = [
    "urgent and dramatic build-up",
    "high energy and enthusiastic",
    "whispered urgently",
    "calm and authoritative narrator",
    "sarcastic and playful",
    "out of breath and shocked",
    "deep cinematic movie trailer narrator",
    "cheerful and friendly",
]

INLINE_VOCAL_TAGS = [
    {"tag": "<short pause>", "desc": "0.5s natural hesitation"},
    {"tag": "<long pause>", "desc": "1.2s dramatic silence"},
    {"tag": "<gasp>", "desc": "Sharp intake of surprise"},
    {"tag": "<sigh>", "desc": "Exhale of relief or frustration"},
    {"tag": "<chuckle>", "desc": "Subtle laugh"},
    {"tag": "<laughter>", "desc": "Audible laughter"},
    {"tag": "<cough>", "desc": "Cough / throat clearing"},
    {"tag": "<throat-clearing>", "desc": "Polite hesitation"},
    {"tag": "<groan>", "desc": "Frustrated sound"},
    {"tag": "<breath>", "desc": "Audible breath"},
]


def _get_audio_dir() -> pathlib.Path:
    p = settings.data_path / "audio"
    p.mkdir(parents=True, exist_ok=True)
    return p


def _probe_duration_wav(wav_bytes: bytes) -> float:
    """Read duration from WAV header (24000 Hz, 16-bit mono = 48000 bytes/sec)."""
    try:
        if len(wav_bytes) > 44 and wav_bytes[:4] == b"RIFF":
            # Data length is total minus 44 header bytes
            pcm_bytes = max(0, len(wav_bytes) - 44)
            # Default Gemini TTS sample rate is 24000 Hz, 1 channel, 16 bits (2 bytes/sample)
            return round(pcm_bytes / (24000 * 2), 2)
    except Exception:
        pass
    return 0.0


class TTSError(Exception):
    """Raised when Gemini TTS fails to synthesize speech."""


async def synthesize_speech(
    text: str,
    voice: str = "Puck",
    style: str | None = None,
    model: str = "gemini-3.8-flash-tts",
    save_file: bool = True,
) -> tuple[bytes, str | None, float]:
    """Synthesize speech using Gemini 3.8 Flash TTS.

    Args:
        text: The verbatim transcript to read. Inline tags like <gasp>, <sigh>, <short pause> are supported.
        voice: Prebuilt studio voice name (Puck, Kore, Charon, Fenrir, etc.) or custom voice_... ID.
        style: Turn-level delivery style (e.g. "urgent and dramatic", "whispered urgently").
        model: "gemini-3.8-flash-tts" (max fidelity) or "gemini-3.8-flash-lite-tts" (fast bulk).
        save_file: If True, writes WAV file to data/audio/ and returns filename.

    Returns:
        (wav_bytes, filename_or_none, duration_seconds)
    """
    if not settings.gemini_api_key:
        raise TTSError("GEMINI_API_KEY is not configured on the server")

    clean_text = text.strip()
    if not clean_text:
        raise TTSError("Text cannot be empty")

    content_item: dict[str, Any] = {
        "type": "text",
        "text": clean_text,
    }

    if style and style.strip():
        content_item["annotations"] = [{
            "type": "speech_metadata",
            "style": style.strip(),
        }]

    payload: dict[str, Any] = {
        "model": model,
        "input": [{
            "type": "user_input",
            "content": [content_item],
        }],
        "response_format": {
            "type": "audio",
            "mime_type": "audio/wav",
        },
        "generation_config": {
            "speech_config": [
                {"voice": voice}
            ]
        }
    }

    url = INTERACTIONS_URL

    try:
        async with httpx.AsyncClient(timeout=45.0) as client:
            res = await client.post(
                url,
                json=payload,
                headers={
                    "x-goog-api-key": settings.gemini_api_key,
                    "content-type": "application/json",
                },
            )

            if not res.is_success:
                logger.error("Gemini TTS API error (%s): %s", res.status_code, res.text)
                raise TTSError(f"Gemini {model} returned HTTP {res.status_code}: {res.text[:300]}")

            data = res.json()

            # Extract base64 audio payload from steps
            audio_b64: str | None = None
            for step in data.get("steps", []):
                if step.get("type") == "model_output":
                    for item in step.get("content", []):
                        if item.get("type") == "audio" and item.get("data"):
                            audio_b64 = item["data"]
                            break

            if not audio_b64:
                # Also check top-level convenience output_audio if present in response
                out_audio = data.get("output_audio") or {}
                audio_b64 = out_audio.get("data")

            if not audio_b64:
                logger.error("Gemini TTS response missing audio: %s", json.dumps(data)[:500])
                raise TTSError("Gemini TTS returned no audio data in response")

            wav_bytes = base64.b64decode(audio_b64)
            duration = _probe_duration_wav(wav_bytes)

            filename = None
            if save_file:
                ts = datetime.datetime.now().strftime("%Y%m%d_%H%M%S")
                short_id = uuid.uuid4().hex[:8]
                filename = f"tts_{ts}_{short_id}.wav"
                out_path = _get_audio_dir() / filename
                out_path.write_bytes(wav_bytes)
                logger.info("Saved Gemini TTS audio (%s, %.1fs) to %s", voice, duration, out_path)

            return wav_bytes, filename, duration

    except TTSError:
        raise
    except Exception as exc:
        logger.exception("Gemini TTS synthesis failed: %s", exc)
        raise TTSError(f"Failed to synthesize speech: {exc}") from exc


async def synthesize_dialogue(
    turns: list[dict[str, str]],
    speakers: list[dict[str, str]],
    model: str = "gemini-3.8-flash-tts",
    save_file: bool = True,
) -> tuple[bytes, str | None, float]:
    """Synthesize multi-speaker conversation with natural cadence and overlapping pipes.

    Args:
        turns: list of {"speaker": "SpeakerName", "text": "...", "style": "optional style"}
        speakers: list of {"speaker": "SpeakerName", "voice": "Puck"} (up to 2 speakers)
    """
    if not settings.gemini_api_key:
        raise TTSError("GEMINI_API_KEY is not configured on the server")

    if not turns:
        raise TTSError("Turns cannot be empty")
    if len(speakers) < 2:
        raise TTSError("Multi-speaker dialogue requires at least 2 configured speakers")

    content_list = []
    for turn in turns:
        item: dict[str, Any] = {
            "type": "text",
            "text": turn["text"],
            "annotations": [{
                "type": "speech_metadata",
                "speaker": turn["speaker"],
                **({"style": turn["style"]} if turn.get("style") else {})
            }]
        }
        content_list.append(item)

    payload: dict[str, Any] = {
        "model": model,
        "input": [{
            "type": "user_input",
            "content": content_list,
        }],
        "response_format": {
            "type": "audio",
            "mime_type": "audio/wav",
        },
        "generation_config": {
            "speech_config": {
                "mode": "conversational",
                "speakers": [
                    {"speaker": sp["speaker"], "voice": sp["voice"]}
                    for sp in speakers[:2]
                ]
            }
        }
    }

    try:
        async with httpx.AsyncClient(timeout=60.0) as client:
            res = await client.post(
                INTERACTIONS_URL,
                json=payload,
                headers={
                    "x-goog-api-key": settings.gemini_api_key,
                    "content-type": "application/json",
                },
            )

            if not res.is_success:
                raise TTSError(f"Multi-speaker Gemini TTS error ({res.status_code}): {res.text[:300]}")

            data = res.json()
            audio_b64 = None
            for step in data.get("steps", []):
                if step.get("type") == "model_output":
                    for item in step.get("content", []):
                        if item.get("type") == "audio" and item.get("data"):
                            audio_b64 = item["data"]
                            break

            if not audio_b64:
                raise TTSError("No audio data returned from multi-speaker Gemini TTS")

            wav_bytes = base64.b64decode(audio_b64)
            duration = _probe_duration_wav(wav_bytes)

            filename = None
            if save_file:
                ts = datetime.datetime.now().strftime("%Y%m%d_%H%M%S")
                short_id = uuid.uuid4().hex[:8]
                filename = f"dialogue_{ts}_{short_id}.wav"
                out_path = _get_audio_dir() / filename
                out_path.write_bytes(wav_bytes)

            return wav_bytes, filename, duration
    except TTSError:
        raise
    except Exception as exc:
        raise TTSError(f"Multi-speaker synthesis failed: {exc}") from exc


async def generate_hook_voiceover(
    title: str,
    description: str | None = None,
    voice: str = "Puck",
    style: str = "high energy and enthusiastic",
    model: str = "gemini-3.8-flash-tts",
) -> tuple[str, str, float]:
    """Generate a viral 4-6 second spoken hook from title/description and synthesize it.

    Returns:
        (script_text, filename, duration_seconds)
    """
    if not settings.gemini_api_key:
        raise TTSError("GEMINI_API_KEY is not configured")

    prompt = (
        "You are an elite short-form video creator. Write a punchy, viral 1-sentence spoken hook "
        "(under 15 words) for a video with the following title:\n"
        f"Title: {title}\n"
        + (f"Description: {description}\n" if description else "")
        + "\n"
        "Guidelines:\n"
        "- Hook the viewer in the first 2 seconds.\n"
        "- Include 1 natural inline vocal tag like <gasp>, <sigh>, or <short pause> to sound extremely human.\n"
        "- Do NOT include stage directions, speaker names, quotes, or markdown. Output ONLY the raw spoken sentence.\n"
    )

    url = f"https://generativelanguage.googleapis.com/v1beta/models/{settings.gemini_model}:generateContent"
    payload = {
        "contents": [{"parts": [{"text": prompt}]}],
        "generationConfig": {
            "temperature": 0.8,
            "maxOutputTokens": 200,
        },
    }

    async with httpx.AsyncClient(timeout=20.0) as client:
        res = await client.post(url, json=payload, headers={"x-goog-api-key": settings.gemini_api_key})
        if not res.is_success:
            raise TTSError(f"LLM hook generation failed: {res.text[:200]}")

        data = res.json()
        parts = data.get("candidates", [{}])[0].get("content", {}).get("parts", [{}])
        script_text = parts[0].get("text", "").strip()
        script_text = re.sub(r'^["\']|["\']$', '', script_text).strip()

    if not script_text:
        script_text = f"Wait... <short pause> you have to see this! {title[:30]}"

    _, filename, duration = await synthesize_speech(
        text=script_text,
        voice=voice,
        style=style,
        model=model,
        save_file=True,
    )

    return script_text, filename or "", duration
