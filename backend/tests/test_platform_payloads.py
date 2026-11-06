"""Exact multipart bytes/copy and local refusals; no provider requests."""
import asyncio
import hashlib
import datetime as dt
import json
import subprocess
from email.parser import BytesParser
from email.policy import default

import httpx
import pytest

from backend.app.config import settings
from backend.app.social import platform_payloads as payloads, queue_manager as qm, upload_post
from backend.app.db import SessionLocal
from backend.app.models import QueueItem, PublicationEvent


def multipart(request):
    message = BytesParser(policy=default).parsebytes(
        ('Content-Type: ' + request.headers['content-type'] + '\r\n\r\n').encode() + request.read())
    fields = {}
    for part in message.iter_parts():
        name = part.get_param('name', header='content-disposition')
        fields.setdefault(name, []).append(part.get_payload(decode=True))
    return fields


@pytest.mark.parametrize('platform', ['facebook', 'x', 'twitter'])
def test_actual_multipart_retains_complete_source_and_exact_visible_copy(tmp_path, monkeypatch, platform):
    path = tmp_path / 'complete-source.mp4'
    source = b'ORIGINAL BEGIN\x00' + bytes(range(256)) * 128 + b'ORIGINAL TAIL\xff'
    path.write_bytes(source)
    title, caption = 'Exact title', 'Approved description.\n\n#Public #Synthetic'
    observed = []
    def handler(request):
        fields = multipart(request); observed.append(fields)
        assert fields['video'] == [source]
        assert request.headers['Idempotency-Key'] == 'synthetic-same-attempt'
        assert fields['request_id'] == [b'synthetic-same-attempt']
        assert fields['platform[]'] == [platform.encode()]
        if platform == 'facebook':
            assert fields['facebook_media_type'] == [b'VIDEO']
            assert fields['facebook_title'] == [title.encode()]
            assert fields['facebook_description'] == [caption.encode()]
        else:
            assert fields['x_title'] == [(title + '\n\n' + caption).encode()]
            assert 'x_long_text_as_post' not in fields and 'first_comment' not in fields
        return httpx.Response(200, json={'request_id': 'synthetic-accepted'})
    real = httpx.AsyncClient
    monkeypatch.setattr(settings, 'uploadpost_api_key', 'synthetic-not-a-credential')
    monkeypatch.setattr(upload_post.httpx, 'AsyncClient', lambda **kw: real(transport=httpx.MockTransport(handler), **kw))
    monkeypatch.setattr('backend.app.costs.record_free', lambda *a, **kw: None)
    assert asyncio.run(upload_post.upload_video(path, profile='synthetic', platforms=[platform], title=title,
        description=caption, idempotency_key='synthetic-same-attempt')) == 'synthetic-accepted'
    assert len(observed) == 1 and hashlib.sha256(path.read_bytes()).digest() == hashlib.sha256(source).digest()


@pytest.mark.parametrize('text', ['A' * 281, '界' * 141, 'Visit https://example.invalid/page',
    'Visit www.example.invalid', 'Visit example.invalid/page', 'Caption\x00control'])
def test_x_refusal_occurs_before_intent_and_transport(tmp_path, monkeypatch, text):
    path = tmp_path / 'source.mp4'; path.write_bytes(b'synthetic source bytes')
    calls, intents, results = [], [], []
    async def accounts(): return [{'id': 'synthetic:x', 'profile': 'synthetic'}]
    async def forbidden(*a, **kw): calls.append(kw); pytest.fail('A refused payload must not reach transport')
    monkeypatch.setattr(upload_post, 'list_accounts', accounts)
    monkeypatch.setattr(upload_post, 'upload_video', forbidden)
    entries = asyncio.run(qm.submit_upload(path, ['synthetic:x'], title=text, description='', tags=[],
        on_intent=intents.append, on_result=results.append))
    assert not calls and not intents
    assert entries == results and len(entries) == 1
    assert entries[0]['state'] == 'rejected' and entries[0]['transport_dispatched'] is False
    assert 'request_id' not in entries[0]


def test_full_facebook_caption_can_succeed_while_x_copy_is_held(tmp_path, monkeypatch):
    path = tmp_path / 'source.mp4'; path.write_bytes(b'complete original')
    calls, intents = [], []
    async def accounts(): return [{'id': 'synthetic:' + p, 'profile': 'synthetic'} for p in ['facebook', 'x']]
    async def upload(path, **kw): calls.append(kw); return 'synthetic-fb-receipt'
    monkeypatch.setattr(upload_post, 'list_accounts', accounts)
    monkeypatch.setattr(upload_post, 'upload_video', upload)
    entries = asyncio.run(qm.submit_upload(path, ['synthetic:*'], title='Title', description='A' * 300,
        tags=[], on_intent=intents.append))
    assert len(calls) == len(intents) == 1 and calls[0]['platforms'] == ['facebook']
    assert intents[0]['visible_payload']['facebook_media_type'] == 'VIDEO'
    assert entries[0]['state'] == 'accepted' and entries[1]['transport_dispatched'] is False


def test_platform_overrides_cannot_discard_copy_or_change_unrelated_targets():
    with pytest.raises(upload_post.PayloadBlocked):
        upload_post.prepare_video_fields('Dramarama', ['facebook'], 'Title', 'Caption', {'facebook_media_type': 'STORIES'})
    with pytest.raises(upload_post.PayloadBlocked):
        upload_post.prepare_video_fields('Dramarama', ['x'], 'Title', 'Caption', {'x_long_text_as_post': 'true'})
    with pytest.raises(upload_post.PayloadBlocked):
        upload_post.prepare_video_fields('Other', ['x'], 'Title', 'Caption', {'x_title': 'Different'})
    assert payloads.video_fields('Title', 'Caption', ['youtube', 'instagram', 'tiktok']) == {}
    assert payloads.weight_upper_bound('A' * 280) == 280
    assert payloads.weight_upper_bound('界' * 140) == 280


def test_facebook_video_transport_preserves_real_long_duration_tail_and_audio(tmp_path, monkeypatch):
    path=tmp_path/'synthetic-150-seconds.mp4'
    subprocess.run(['ffmpeg','-v','error','-y','-f','lavfi','-i',
        "color=c=blue:s=320x180:r=12:d=150,drawbox=x=10:y=10:w=30:h=30:color=white:t=fill:enable='gte(t,145)'",
        '-f','lavfi','-i','sine=f=620:d=150','-c:v','libx264','-preset','ultrafast','-pix_fmt','yuv420p',
        '-c:a','aac','-ar','48000','-ac','2','-b:a','96k','-shortest',str(path)],check=True)
    original=path.read_bytes(); received=[]
    def handler(request):
        fields=multipart(request)
        assert fields['facebook_media_type']==[b'VIDEO']
        received.append(fields['video'][0]);return httpx.Response(200,json={'request_id':'synthetic-long-fb'})
    real=httpx.AsyncClient
    monkeypatch.setattr(settings,'uploadpost_api_key','synthetic-not-a-credential')
    monkeypatch.setattr(upload_post.httpx,'AsyncClient',lambda **kw:real(transport=httpx.MockTransport(handler),**kw))
    monkeypatch.setattr('backend.app.costs.record_free',lambda *a,**kw:None)
    asyncio.run(upload_post.upload_video(path,profile='synthetic',platforms=['facebook'],title='Tail marker',description='Full synthetic media'))
    assert received==[original] and path.read_bytes()==original
    delivered=tmp_path/'received.mp4';delivered.write_bytes(received[0])
    probe=json.loads(subprocess.check_output(['ffprobe','-v','error','-show_format','-show_streams','-of','json',str(delivered)]))
    assert abs(float(probe['format']['duration'])-150)<0.1
    assert {s['codec_type'] for s in probe['streams']}=={'audio','video'}
    subprocess.run(['ffmpeg','-v','error','-i',str(delivered),'-f','null','-'],check=True)


def test_new_delivery_warning_is_held_without_reposting_but_old_receipts_are_unchanged(session, monkeypatch, tmp_path):
    now=dt.datetime.now(dt.timezone.utc)
    entry={'profile':'synthetic','platforms':['facebook'],'request_id':'synthetic-fb-result',
        'client_request_id':'synthetic-attempt','state':'accepted','submitted_at':now.isoformat(),
        'transport_contract':'facebook-x-visible-v1'}
    status={'status':'completed','results':[{'profile_username':'synthetic','platform':'facebook','success':True,
        'post_url':'https://example.invalid/published','changes':{'duration':{'from':150,'to':90}}}]}
    path=tmp_path/'source.mp4';path.write_bytes(b'original')
    row=QueueItem(title='Synthetic warning',pipeline='Movie Clips',accounts=['synthetic:facebook'],status='posting',
        video_path=str(path),publish_requests=[entry])
    session.add(row);session.commit();qid=row.id
    monkeypatch.setattr(qm,'_fetch_statuses',lambda entries:{'synthetic-fb-result':status})
    assert qm.reconcile_posting(session,now)==1
    session.refresh(row)
    assert row.status=='review' and row.published_at is None
    assert row.catchup['done']==[['synthetic:facebook','https://example.invalid/published']]
    assert session.query(PublicationEvent).filter_by(queue_id=qid,kind='delivery_hold').count()==1
    assert qm.reconcile_posting(session,now)==0
    row.publish_requests=[];row.status='ready';session.commit()  # Simulate accidental requeue/field clearing.
    with pytest.raises(upload_post.UploadPostError,match='without reposting'):
        asyncio.run(qm.publish_queue_item(qid,session))
    legacy={k:v for k,v in entry.items() if k!='transport_contract'}
    assert 'delivery_holds' not in qm.resolve_results([legacy],{'synthetic-fb-result':status},now)
    clean={'status':'completed','results':[{**status['results'][0],'changes':{},'video_was_transcoded':True}]}
    assert 'delivery_holds' not in qm.resolve_results([entry],{'synthetic-fb-result':clean},now)



def test_screen_central_x_over_280_posts_title_and_hashtags_only():
    from backend.app.social import queue_payloads as qp
    title,desc='Young Bruce Wayne Never Flinched','A long recap. '*30
    r=qp.preview(title,desc,'#shorts #movieclips',['youtube','facebook','x'])
    assert r['values']['x_title']==title+'\n\n#shorts #movieclips' and r['x_short_form'] and not r['x_body_unchanged']
    assert desc.strip() in r['values']['facebook_description']                   # other platforms keep full copy
    short=qp.preview(title,'Short.','#shorts',['x'])
    assert short['values']['x_title']==title+'\n\nShort.\n\n#shorts' and not short['x_short_form']
    with pytest.raises(ValueError):                                              # ICK still needs a reviewed variant
        qp.preview(title,desc,'#tonightsconversation #theickroom',['x'],ick=True)
    with pytest.raises(ValueError):                                              # never truncates the title
        qp.preview('T'*300,desc,'#shorts',['x'])


def test_short_x_keeps_two_hashtags_specific_first():
    from backend.app.social import queue_payloads as qp
    title,desc='Boyka vs Chambers','A long edit. '*30
    r=qp.preview(title,desc,'#Boyka #Chambers #ScreenCentral #Shorts',['x'])
    assert r['values']['x_title']==title+'\n\n#Boyka #Chambers'
    r=qp.preview(title,desc,'#shorts #Recap #ScreenCentral',['x'])
    assert r['values']['x_title']==title+'\n\n#shorts #ScreenCentral'             # generic dropped first, order kept


def test_regular_ick_x_without_a_saved_variant_posts_title_and_both_brand_tags():
    from backend.app.social import queue_payloads as qp
    r = qp.preview("Short hook", "D" * 400, "#love #tonightsconversation #theickroom", ["x"], ick=True, ick_short_x=True)
    assert r["values"]["x_title"] == "Short hook\n\n#tonightsconversation #theickroom" and r["x_short_form"]


def test_ick_x_without_the_flag_still_needs_a_reviewed_variant():
    import pytest
    from backend.app.social import queue_payloads as qp
    with pytest.raises(ValueError):
        qp.preview("Short hook", "D" * 400, "#love #tonightsconversation #theickroom", ["x"], ick=True)


def _clip(path, seconds):
    subprocess.run(['ffmpeg','-v','error','-y','-f','lavfi','-i',f'color=c=red:s=180x320:r=6:d={seconds}',
        '-f','lavfi','-i',f'sine=f=440:d={seconds}','-c:v','libx264','-preset','ultrafast','-pix_fmt','yuv420p',
        '-c:a','aac','-shortest',str(path)],check=True)
    return path


def test_r7_facebook_route_by_duration(tmp_path):
    assert qm.facebook_media_type(_clip(tmp_path/'short.mp4', 89))[0] == 'REELS'
    assert qm.facebook_media_type(_clip(tmp_path/'long.mp4', 92))[0] == 'VIDEO'
    bad = tmp_path/'bad.mp4'; bad.write_bytes(b'not a video')
    assert qm.facebook_media_type(bad) == ('VIDEO', None)              # unreadable keeps the full-length path


@pytest.mark.parametrize('seconds,mode', [(30, 'REELS'), (120, 'VIDEO')])
def test_r7_facebook_send_uses_the_routed_type_and_records_it(tmp_path, monkeypatch, seconds, mode):
    path = _clip(tmp_path/'clip.mp4', seconds)
    calls, intents = [], []
    async def accounts(): return [{'id': 'synthetic:facebook', 'profile': 'synthetic'}]
    async def upload(path, **kw): calls.append(kw); return 'synthetic-fb'
    monkeypatch.setattr(upload_post, 'list_accounts', accounts)
    monkeypatch.setattr(upload_post, 'upload_video', upload)
    asyncio.run(qm.submit_upload(path, ['synthetic:facebook'], title='T', description='D', tags=[], on_intent=intents.append))
    assert calls[0]['facebook_media_type'] == mode
    assert intents[0]['facebook_route']['facebook_media_type'] == mode and intents[0]['facebook_route']['duration_seconds']


def test_r7_reels_reaches_the_provider_field(tmp_path, monkeypatch):
    path = tmp_path/'s.mp4'; path.write_bytes(b'synthetic')
    seen = []
    def handler(request):
        seen.append(multipart(request)['facebook_media_type']); return httpx.Response(200, json={'request_id': 'r'})
    real = httpx.AsyncClient
    monkeypatch.setattr(settings, 'uploadpost_api_key', 'synthetic-not-a-credential')
    monkeypatch.setattr(upload_post.httpx, 'AsyncClient', lambda **kw: real(transport=httpx.MockTransport(handler), **kw))
    monkeypatch.setattr('backend.app.costs.record_free', lambda *a, **kw: None)
    asyncio.run(upload_post.upload_video(path, profile='synthetic', platforms=['facebook'], title='T', description='D',
        facebook_media_type='REELS'))
    assert seen == [[b'REELS']]
