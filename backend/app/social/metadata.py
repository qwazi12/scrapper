"""AI Metadata generation for social media posts (TikTok, Reels, Shorts, X).

Uses Gemini 2.0 / 1.5 Flash via REST API (zero extra dependencies),
with a deterministic template fallback when GEMINI_API_KEY is not configured.
"""

from __future__ import annotations

import json
import logging
import re
from typing import Any

import httpx

from ..config import settings

logger = logging.getLogger("scrapper.social.metadata")


def _fallback_metadata(clip_titles: list[str]) -> dict[str, Any]:
    """Generate clean, engaging fallback metadata when LLM is unavailable."""
    clean_titles = [t.strip() for t in clip_titles if t and t.strip()]
    main_title = clean_titles[0] if clean_titles else "Viral Moments Compilation"

    # Clean title
    short_title = re.sub(r"[#@|\[\]]", "", main_title).strip()
    if len(short_title) > 60:
        short_title = short_title[:57] + "..."

    caption = (
        f"You won't believe how this ended! 👀🔥\n\n"
        f"Featuring: {', '.join(clean_titles[:3]) if clean_titles else 'Top moments'}\n\n"
        f"Drop your reaction in the comments! 👇"
    )

    hashtags = ["#viral", "#trending", "#reels", "#fyp", "#shorts", "#explore"]

    full_text = f"{short_title}\n\n{caption}\n\n{' '.join(hashtags)}"

    return {
        "title": short_title,
        "caption": caption,
        "hashtags": hashtags,
        "full_text": full_text,
        "model": "template",
    }


async def generate_social_metadata(
    clip_titles: list[str],
    user_prompt: str | None = None,
) -> dict[str, Any]:
    """Generate viral social media metadata using Gemini or fallback."""
    if not settings.gemini_api_key:
        return _fallback_metadata(clip_titles)

    prompt = (
        "You are an elite short-form social media editor and growth strategist. "
        "Create viral, high-converting metadata for a video compilation made of the following clips:\n\n"
        f"Clips in this compilation:\n"
        + "\n".join(f"- {t}" for t in clip_titles if t)
        + "\n\n"
    )
    if user_prompt:
        prompt += f"Special User Instructions: {user_prompt}\n\n"

    prompt += (
        "Respond ONLY with a valid, raw JSON object (no markdown code fences, no extra text) "
        "with these exact keys:\n"
        "{\n"
        '  "title": "A short, punchy 5-8 word hook title",\n'
        '  "caption": "An engaging 2-4 sentence caption with emojis and a call to action asking viewers to comment",\n'
        '  "hashtags": ["#tag1", "#tag2", "#tag3", "#tag4", "#tag5", "#tag6"],\n'
        '  "full_text": "The full formatted post ready to copy or post directly"\n'
        "}"
    )

    url = f"https://generativelanguage.googleapis.com/v1beta/models/gemini-2.0-flash:generateContent?key={settings.gemini_api_key}"

    payload = {
        "contents": [
            {
                "parts": [{"text": prompt}]
            }
        ],
        "generationConfig": {
            "temperature": 0.7,
            "maxOutputTokens": 800,
        },
    }

    try:
        async with httpx.AsyncClient(timeout=25.0) as client:
            res = await client.post(url, json=payload)
            if not res.is_success:
                logger.warning("Gemini API call failed (%s): %s", res.status_code, res.text)
                return _fallback_metadata(clip_titles)

            data = res.json()
            candidates = data.get("candidates", [])
            if not candidates:
                return _fallback_metadata(clip_titles)

            parts = candidates[0].get("content", {}).get("parts", [])
            if not parts:
                return _fallback_metadata(clip_titles)

            raw_text = parts[0].get("text", "").strip()

            # Remove any markdown code fences if Gemini added them
            raw_text = re.sub(r"^```(?:json)?\s*", "", raw_text)
            raw_text = re.sub(r"\s*```$", "", raw_text).strip()

            parsed = json.loads(raw_text)
            parsed["model"] = "gemini-2.0-flash"
            return parsed
    except Exception as exc:
        logger.exception("Failed to parse Gemini response: %s", exc)
        return _fallback_metadata(clip_titles)
