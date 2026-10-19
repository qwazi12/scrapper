"""Canonical film/TV matching. Popularity is never identity evidence."""
from __future__ import annotations

import unicodedata


def normalized(title: str) -> str:
    return "".join(c for c in unicodedata.normalize("NFKD", title).casefold() if c.isalnum())


def select(title: str, results: list[dict], year=None, medium: str | None = None, tmdb_id=None) -> dict | None:
    if not isinstance(title, str) or not normalized(title):
        return None
    if year is not None and (type(year) is bool or not str(year).isdigit() or len(str(year)) != 4):
        return None
    if medium and medium not in ("movie", "tv"):
        return None
    if tmdb_id is not None and (type(tmdb_id) is bool or not str(tmdb_id).isdigit() or int(tmdb_id) <= 0):
        return None
    matches = {}
    for result in results:
        aliases = [result.get("title"), result.get("original_title"), *(result.get("alternate_titles") or [])]
        if not any(isinstance(a, str) and normalized(a) == normalized(title) for a in aliases):
            continue
        if result.get("media_type") not in ("movie", "tv") or (medium and result.get("media_type") != medium):
            continue
        if year is not None and str(result.get("date") or "")[:4] != str(year):
            continue
        rid = result.get("tmdb_id", result.get("id"))
        if type(rid) is bool or not str(rid).isdigit() or int(rid) <= 0:
            continue
        if tmdb_id is not None and int(rid) != int(tmdb_id):
            continue
        matches[(result["media_type"], int(rid))] = result
    return next(iter(matches.values())) if len(matches) == 1 else None
