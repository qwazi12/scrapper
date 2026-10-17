import asyncio

from backend.app.social import upload_post as up


def _countdown_description():
    chapters = "\n".join(f"{m}:{s:02d} - #{n}: Some Long Horror Movie Title Number {n}" for n, (m, s) in
                         zip(range(15, 0, -1), [(i, 30) for i in range(1, 16)]))
    intro = "Counting down the 15 scariest horror movies ever made. " * 6
    tags = " ".join(f"#tag{i}" for i in range(40))
    return f"{intro}\n\nTIMESTAMPS:\n0:00 - Intro & Preview\n{chapters}\n\n" + ("Attribution text. " * 80) + f"\n\n{tags}"


def test_instagram_caption_fits_2200_and_30_hashtags():
    d = _countdown_description()
    assert len(d) > 2200
    ig = up.fit_instagram(d)
    assert len(ig) <= 2200
    assert len(up._HASHTAG.findall(ig)) == 30
    assert "Intro & Preview" not in ig and "Counting down" in ig            # timestamps dropped, intro kept


def test_short_caption_is_untouched():
    assert up.fit_instagram("Great scene #movies") == "Great scene #movies"


def test_youtube_limits():
    assert len(up.fit_youtube("x" * 6000)) <= 5000
    assert "<" not in up.fit_youtube("a <b> c")
    tags = up.fit_youtube_tags([f"long tag number {i}" for i in range(60)])
    assert sum(len(t) + 3 for t in tags) <= 500 + len(tags)


def test_upload_sends_full_text_to_youtube_and_facebook_fitted_to_instagram(monkeypatch, tmp_path):
    sent = {}

    class Res:
        status_code = 200
        is_success = True
        def json(self): return {"request_id": "r1"}

    class Client:
        def __init__(self, *a, **k): pass
        async def __aenter__(self): return self
        async def __aexit__(self, *a): return False
        async def post(self, url, headers=None, data=None, files=None):
            sent.update(data)
            return Res()

    monkeypatch.setattr(up.httpx, "AsyncClient", Client)
    monkeypatch.setattr(up, "_raise_for", lambda res, what: None)
    monkeypatch.setattr(up, "_headers", lambda: {})
    v = tmp_path / "v.mp4"
    v.write_bytes(b"x")
    d = _countdown_description()
    asyncio.run(up.upload_video(v, profile="Cinama", platforms=["youtube", "facebook", "instagram"],
                                title="15 Scariest", description=d, tags=["horror"]))
    assert len(sent["description"]) <= 2200                                 # Instagram's copy
    assert sent["facebook_description"] == d and "Intro & Preview" in sent["youtube_description"]
