from unittest.mock import patch
from backend.app.studio.compilations.engine import auto_queue_researched_countdown
from backend.app.db import SessionLocal
from backend.app.models import QueueItem

def test_auto_queue_researched_countdown():
    countdown = {
        "id": "test-countdown-1",
        "topic": "Top 5 Greatest Sci-Fi Movies of All Time",
        "format": "top5",
        "why_now": "Trending in sci-fi discussions",
        "title_options": ["Top 5 Greatest Sci-Fi Movies of All Time, Definitively Ranked"],
        "thumbnail_text": "DON'T MISS THESE",
        "hook_script": "Think you know the best sci-fi movies? Here is the definitive list.",
        "closing_question": "Which film was robbed of the number one spot?",
        "entries": [
            {
                "rank": 5,
                "title": "Interstellar",
                "year": 2014,
                "score": 92.5,
                "why_it_ranks": "Groundbreaking physics and emotional core.",
                "fun_fact": "Nolan used real gravitational equations.",
                "where_to_watch": "Paramount+",
                "sources": ["IMDb", "Letterboxd"],
            },
            {
                "rank": 4,
                "title": "Blade Runner 2049",
                "year": 2017,
                "score": 94.0,
                "why_it_ranks": "Atmospheric masterpiece.",
                "fun_fact": "Deakins won an Oscar.",
                "where_to_watch": "Max",
                "sources": ["IMDb", "JustWatch"],
            },
            {
                "rank": 3,
                "title": "The Matrix",
                "year": 1999,
                "score": 96.0,
                "why_it_ranks": "Revolutionized action cinema.",
                "fun_fact": "Bullet time filming.",
                "where_to_watch": "Max",
                "sources": ["IMDb", "Rotten Tomatoes"],
            },
            {
                "rank": 2,
                "title": "Alien",
                "year": 1979,
                "score": 97.5,
                "why_it_ranks": "The ultimate sci-fi horror.",
                "fun_fact": "Cast didn't know about chestburster.",
                "where_to_watch": "Hulu",
                "sources": ["IMDb", "Letterboxd"],
            },
            {
                "rank": 1,
                "title": "2001: A Space Odyssey",
                "year": 1968,
                "score": 99.0,
                "why_it_ranks": "The gold standard of cinema.",
                "fun_fact": "Created before the moon landing.",
                "where_to_watch": "Max",
                "sources": ["IMDb", "BFI"],
            },
        ],
        "honorable_mentions": ["Arrival", "Solaris"],
    }

    with patch("backend.app.studio.runner.busy", return_value={"project_id": 1}), \
         patch("backend.app.studio.runner.start", return_value=None), \
         patch("backend.app.studio.tmdb.search", return_value=[{"id": 101, "media_type": "movie"}]), \
         patch("backend.app.studio.tmdb.configured", return_value=False):
        res = auto_queue_researched_countdown(countdown)

    assert res["ok"] is True
    assert res["entry_count"] == 5
    assert res["created_count"] >= 1
    assert "queue_item_id" in res

    with SessionLocal() as s:
        q = s.get(QueueItem, res["queue_item_id"])
        assert q is not None
        assert q.pipeline == "LongForm"
        assert q.source == "Countdown Studio"
        assert "CHAPTERS:" in q.description
        assert "0:00 Intro & Preview" in q.description
        assert "2001: A Space Odyssey" in q.description
        assert "Interstellar" in q.description
        assert "Which film was robbed" in q.description
        # cleanup
        s.delete(q)
        s.commit()
