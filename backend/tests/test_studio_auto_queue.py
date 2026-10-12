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

    fake_search = lambda title: [{"tmdb_id": 100 + len(title), "media_type": "movie", "title": title, "date": ""}]
    with patch("backend.app.studio.tmdb.search", side_effect=fake_search), \
         patch("backend.app.studio.tmdb.configured", return_value=True):
        res = auto_queue_researched_countdown(countdown)
        again = auto_queue_researched_countdown(countdown)

    assert res["ok"] is True
    assert res["entry_count"] == 5
    assert res["created_count"] == 5
    assert again["already_queued"] and again["queue_item_id"] == res["queue_item_id"]   # never queued twice

    with SessionLocal() as s:
        q = s.get(QueueItem, res["queue_item_id"])
        assert q is not None
        assert q.pipeline == "LongForm"
        assert q.source == "Countdown Studio"
        assert q.status == "review" and not q.video_path          # never Ready without a video
        assert (q.research["production"]["state"]) == "making"
        assert "CHAPTERS:" in q.description
        assert "2001: A Space Odyssey" in q.description
        assert "Interstellar" in q.description
        assert "Which film was robbed" in q.description
        s.delete(q)
        s.commit()


def test_auto_queue_refuses_entries_that_are_not_titles():
    import pytest
    countdown = {"id": "theories", "topic": "Spider-Man 4 theories", "format": "top5",
                 "entries": [{"rank": 2, "title": "Ned Leeds Becomes Hobgoblin"},
                             {"rank": 1, "title": "Mayor Fisk Enacts the Anti-Vigilante Act"}]}
    with patch("backend.app.studio.tmdb.search", return_value=[]), \
         patch("backend.app.studio.tmdb.configured", return_value=True):
        with pytest.raises(ValueError, match="aren't movies or shows"):
            auto_queue_researched_countdown(countdown)
