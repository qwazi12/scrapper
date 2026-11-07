"""Per-platform text on a queue row (owner, 2026-11-07). Facebook keeps the row's title/description; YouTube,
Instagram and X may each carry their own words. Hashtags are never stored here: the live caption code adds each
platform's cut at send (hashtag_rules)."""
from __future__ import annotations

from . import hashtag_rules
from .platform_payloads import weight_upper_bound

FIELDS = {"youtube": ("title", "description"), "instagram": ("caption",), "x": ("text",)}
YOUTUBE_TITLE_MAX = 100
X_MAX = 280


def clean(value: dict) -> dict | None:
    """The stored form ({} -> None), or ValueError naming what is wrong."""
    if not isinstance(value, dict):
        raise ValueError("platform_copy must be an object")
    out: dict = {}
    for net, fields in value.items():
        if net not in FIELDS or not isinstance(fields, dict) or set(fields) - set(FIELDS[net]):
            raise ValueError(f"platform_copy allows only {FIELDS}")
        kept = {}
        for name, text in fields.items():
            if not isinstance(text, str):
                raise ValueError(f"platform_copy.{net}.{name} must be text")
            text, tags = hashtag_rules.split_trailing(text.strip())
            if tags:
                raise ValueError(f"platform_copy.{net}.{name}: leave hashtags out, they are added per platform at send")
            if text:
                kept[name] = text
        if net == "youtube" and len(kept.get("title", "")) > YOUTUBE_TITLE_MAX:
            raise ValueError(f"YouTube title is over {YOUTUBE_TITLE_MAX} characters")
        if net == "x" and weight_upper_bound(kept.get("text", "")) > X_MAX:
            raise ValueError(f"X text is over {X_MAX} weighted characters before hashtags")
        if kept:
            out[net] = kept
    return out or None


def fit_x(text: str, with_tags: str) -> str:
    """X keeps its hashtags only while the post stays within 280; otherwise the words go alone."""
    return with_tags if weight_upper_bound(with_tags) <= X_MAX else text
