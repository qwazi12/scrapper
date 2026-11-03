import asyncio

from backend.app.models import QueueItem
from backend.app.social import ai_bulk, ick_room_seo, queue_manager as qm


def ick(session, name, **kw):
    it = QueueItem(pipeline=qm.ICK_ROOM, source="The Ick Room / The ICK Room", video_name=name, title=name,
                   status=kw.pop("status", "review"), accounts=["TheIckRoom:*"], **kw)
    session.add(it)
    session.commit()
    return it


def test_source_title_drops_date_tiktok_id_and_underscores():
    it = QueueItem(video_name="20261030_Seven Years Together_ And She Still Had to Ask Him for a Ring_7702492954069437710.mp4")
    assert ick_room_seo.source_title(it) == "Seven Years Together And She Still Had to Ask Him for a Ring"


def test_ick_room_rows_get_ick_room_seo_not_tmdb(session, monkeypatch):
    it = ick(session, "20261031_She Says He Cheated Twice_ Then Admits She Hit Him_7702842546774494477.mp4")
    prompts = []

    def fake(prompt, temperature=0.4):
        prompts.append(prompt)
        return {"title": "She says he cheated twice… 😬 then admits she hit him", "caption": "c",
                "hashtags": ["relationships", "#cheating"]}
    monkeypatch.setattr(ick_room_seo.gemini, "ask_json", fake)
    monkeypatch.setattr("backend.app.social.clip_research.generate",
                        lambda *a, **k: (_ for _ in ()).throw(AssertionError("TMDB path used")))
    res, research = asyncio.run(ai_bulk.ai_for(it, session))
    assert "She Says He Cheated Twice Then Admits She Hit Him" in prompts[0]
    assert "Tonight" not in prompts[0]                       # style only, never their name
    assert res["hashtags"] == ["#relationships", "#cheating", "#tonightsconversation", "#theickroom"]
    assert research == {"kind": "ick_room", "from": "She Says He Cheated Twice Then Admits She Hit Him"}
    ai_bulk.apply_ai(it, res, research)
    assert it.tags == "#relationships #cheating #tonightsconversation #theickroom" and not ick_room_seo.needs_seo(it)


def test_scheduler_starts_seo_only_for_ick_rows_without_it(session, monkeypatch):
    a = ick(session, "a.mp4")
    done = ick(session, "b.mp4", research={"kind": "ick_room"})
    posted = ick(session, "c.mp4", status="archived")
    failed = ick(session, "d.mp4")
    clip = QueueItem(pipeline="@VynixAE", source="Movie Clips / @VynixAE", title="t", status="review")
    session.add(clip)
    session.commit()
    monkeypatch.setitem(ick_room_seo._failed, failed.id, __import__("time").time())   # failed just now: waits 24 h
    started = []
    monkeypatch.setattr(ai_bulk, "start", lambda ids: started.append(ids))
    monkeypatch.setitem(ai_bulk.status, "running", False)
    assert qm.start_ick_room_seo(session) == 1
    assert started == [[a.id]]
    monkeypatch.setitem(ai_bulk.status, "running", True)            # one bulk run at a time
    assert qm.start_ick_room_seo(session) == 0
    assert done.id not in started[0] and posted.id not in started[0] and clip.id not in started[0]
