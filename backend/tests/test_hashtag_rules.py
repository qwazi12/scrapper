"""Per-platform hashtag rule (owner, 2026-11-07)."""
import asyncio

from backend.app.models import QueueItem
from backend.app.social import hashtag_rules as hr
from backend.app.social import queue_manager as qm
from backend.app.social import upload_post as up

CINAMA = "#shorts #Disturbia #ShiaLaBeouf #Thriller #fyp #Mystery #viral #HouseArrest #Neighbor #SerialKiller #Paranoia #movieclips #Suspenseful".split()


def tags(net, pool=CINAMA, **kw):
    kw.setdefault("brand", kw.get("profile", "Cinama"))
    kw.setdefault("profile", "Cinama")
    return hr.platform_tags(pool, net, **kw)


def test_youtube_short_film_niche_character_then_rest_with_shorts_inside_ten():
    yt = tags("youtube", shorts=True)
    assert yt[:3] == ["#Disturbia", "#MovieClips", "#ShiaLaBeouf"]
    assert len(yt) == 10 and yt[-1] == "#shorts" and not {"#fyp", "#viral", "#movieclips"} & set(yt)


def test_caps_per_platform_and_no_shorts_outside_youtube():
    assert tags("instagram", shorts=True) == ["#Disturbia", "#MovieClips", "#ShiaLaBeouf", "#Thriller", "#Mystery"]
    assert tags("facebook", shorts=True) == ["#Disturbia", "#MovieClips", "#ShiaLaBeouf"]
    assert tags("x", shorts=True) == ["#Disturbia", "#MovieClips"]
    assert tags("youtube") [-1] != "#shorts"                             # long-form YouTube: no #shorts


def test_longform_film_then_trailer_breakdown():
    pool = "#TulsaKing #TulsaKingtrailer #SylvesterStallone #MovieClips".split()
    assert tags("youtube", pool, brand="LongForm") == ["#TulsaKing", "#TrailerBreakdown", "#TulsaKingtrailer", "#SylvesterStallone"]


def test_ick_facebook_topic_plus_brand_and_x_brand_plus_topic():
    pool = "#relationships #trustissues #tonightsconversation #theickroom".split()
    assert tags("facebook", pool, profile="TheIckRoom") == ["#relationships", "#tonightsconversation", "#theickroom"]
    assert tags("x", pool, profile="TheIckRoom") == ["#theickroom", "#relationships"]


def test_dramarama_series_short_drama_alternate_title_and_no_other_brand_tags():
    lead = hr.drama_lead("DramaRama / I WOKE UP WITH MY PRIME SUSPECT [12]", "20261025_X_EP01_Waking Up Beside a Suspect - EP01 123")
    assert lead == ["#IWokeUpWithMyPrimeSuspect", "#WakingUpBesideASuspect"]
    yt = tags("youtube", "#DramaRama #FamilyDrama #movieclips #ScreenCentral".split(), profile="Dramarama", lead=lead)
    assert yt == ["#IWokeUpWithMyPrimeSuspect", "#ShortDrama", "#WakingUpBesideASuspect", "#DramaRama", "#FamilyDrama"]
    assert "#DramaRama" not in tags("youtube", ["#Vikings", "#DramaRama"])     # another brand's name, on Cinama


def test_source_channel_names_are_removed():
    assert tags("youtube", ["#Vikings", "#ArliStoryTime", "#Netflix"], other_names=["ArliStoryTime"]) == ["#Vikings", "#MovieClips", "#Netflix"]


def test_reviewed_words_stay_and_inline_hashtags_count_toward_the_cap():
    text = "Watch #Disturbia tonight.\n\nWhat would you do?\n\n#shorts #ShiaLaBeouf #Thriller"
    out = hr.apply(text, [], "facebook", brand="Cinama", profile="Cinama")
    assert out == "Watch #Disturbia tonight.\n\nWhat would you do?\n\n#MovieClips #ShiaLaBeouf"


def test_post_sends_each_platform_its_own_tags(session, monkeypatch):
    sent = {}

    async def list_accounts():
        return [{"id": f"Cinama:{n}", "profile": "Cinama"} for n in ("youtube", "facebook", "x")]

    async def upload_video(path, *, profile, platforms, title, description, **kw):
        sent[platforms[0]] = (title, description)
        return "r"
    monkeypatch.setattr(up, "list_accounts", list_accounts)
    monkeypatch.setattr(up, "upload_video", upload_video)
    session.add(QueueItem(pipeline="ArliStoryTime — UC123", source="Movie Clips / x", status="archived", title="t", video_path=__file__))
    it = QueueItem(pipeline="Movie Clips", source="Movie Clips / test", status="ready", accounts=["Cinama:*"],
                   title="A title", description="Body.", tags=" ".join(CINAMA + ["#ArliStoryTime"]), video_path=__file__)
    session.add(it)
    session.commit()
    asyncio.run(qm.publish_queue_item(it.id, session, regular=False))
    assert sent["facebook"] == ("A title", "Body.\n\n#Disturbia #MovieClips #ShiaLaBeouf")
    assert sent["x"] == ("A title\n\nBody.\n\n#Disturbia #MovieClips", "")
    yt = sent["youtube"][1].split("\n\n")[-1].split()
    assert len(yt) == 10 and yt[-1] == "#shorts" and "#ArliStoryTime" not in yt
