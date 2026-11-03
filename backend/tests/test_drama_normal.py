"""Actual synthetic files, real leases/CAS, mocked native/provider boundaries."""
import asyncio
import copy
import datetime as dt
import json
from pathlib import Path
from concurrent.futures import ThreadPoolExecutor
from uuid import uuid4

import pytest
import httpx
from fastapi import HTTPException
from sqlalchemy import text

from backend.app import drama_controls as dc,drama_normal as normal,drama_delivery as delivery
from backend.app import pending_metadata as pm,publication_journal as journal,worker_leases
from backend.app.config import settings
from backend.app.db import SessionLocal,engine
from backend.app.models import QueueItem,DramaRecord,PublicationEvent,DramaDispatchPermit
from backend.app.social import queue_manager as qm,upload_post
from backend.tests.test_drama_controls import archive,original_video,ready,view,inspect,at_slot,reconcile
from backend.tests.test_platform_payloads import multipart

REAL_DELIVERY=dc.delivery_fidelity_verified
NATIVE={'profile':'Dramarama','accounts':['Dramarama:facebook','Dramarama:x'],
    'facebook_connection_id':'synthetic-original-owner','facebook_page_id':'700000000000001',
    'facebook_pinned_page_id':'700000000000001','profile_pinned_page_id':'700000000000001',
    'x_account_id':'700000000000002','x_handle':'synthetic_drama'}


@pytest.fixture
def normal_archive(archive,monkeypatch):
    a=archive;a.native=copy.deepcopy(NATIVE)
    # The legacy fixture deliberately bypasses its old unsupported gate. These
    # v2 tests restore the REAL production gates and create actual permission.
    monkeypatch.setattr(dc,'require_activation_capability',a.real_activation_gate)
    monkeypatch.setattr(dc,'delivery_fidelity_verified',REAL_DELIVERY)
    async def native(page):
        assert page==NATIVE['facebook_page_id']
        return copy.deepcopy(a.native)
    monkeypatch.setattr(upload_post,'canary_target_identity',native)
    for key,value in [('drama_native_reads_enabled',False),('drama_fb_read_token',''),('drama_x_read_token','')]:
        monkeypatch.setattr(settings,key,value)
    return a


def targets(a):
    return {k:a.native[k] for k in ('facebook_page_id','x_account_id','x_handle')}


def permission(a,key=(0,1)):
    qid=a.rows[key][0];v=view(a,key);series=v['evidence']['series_key']
    scope=a.client.get('/api/drama/normal/permissions/'+series).json()
    req={'operation_id':str(uuid4()),**targets(a),'queue_id':qid,'expected_fingerprint':v['fingerprint'],
        'evidence_id':v['evidence']['id'],'expected_permission_fingerprint':scope['fingerprint'],
        'permission_reference':'Existing synthetic user permission for this exact series on Facebook and X',
        'authorization_provenance':'Agent coordinator recording actual synthetic user instruction; no manual wording-review claim',
        'decision':'Record the existing user permission for this exact canonical series on the frozen DramaRama Facebook and X destinations only'}
    r=a.client.post('/api/drama/normal/permissions',json=req)
    assert r.status_code==200,r.text
    return r.json(),req


@pytest.mark.parametrize('field',['permission_reference','authorization_provenance'])
def test_whitespace_only_permission_reference_or_provenance_is_not_authority(normal_archive,field):
    a=normal_archive;ready(a,(0,1));grant,req=permission(a)
    series=view(a,(0,1))['evidence']['series_key'];before=a.client.get('/api/drama/normal/permissions/'+series).json()
    req.update(operation_id=str(uuid4()),expected_permission_fingerprint=before['fingerprint'],
        expected_fingerprint=view(a,(0,1))['fingerprint'])
    req[field]=' \n\t '
    response=a.client.post('/api/drama/normal/permissions',json=req)
    assert response.status_code==422,response.text
    assert a.client.get('/api/drama/normal/permissions/'+series).json()==before
    assert not a.transfers


def plan_request(a,keys,lanes=None):
    v=view(a,keys[0]);lanes=lanes or [v['evidence']['series_key'],None]
    return {'operation_id':str(uuid4()),**targets(a),'expected_plan_fingerprint':a.client.get('/api/drama/plan').json()['fingerprint'],
        'lanes':lanes,'items':[{'id':a.rows[k][0],'expected_fingerprint':view(a,k)['fingerprint']} for k in keys]}


def plan(a,keys,lanes=None):
    req=plan_request(a,keys,lanes);preview=a.client.post('/api/drama/normal/plan-preview',json=req)
    assert preview.status_code==200,preview.text
    body={**req,'acknowledged_preview_sha256':preview.json()['preview_sha256']}
    r=a.client.post('/api/drama/normal/plan',json=body);assert r.status_code==200,r.text
    return r.json(),body


def test_real_rights_bound_one_lane_first_EP1_can_dispatch_without_fabricated_predecessor_proof(normal_archive):
    a=normal_archive;ready(a,(0,1));permission(a);receipt,request=plan(a,[(0,1)])
    assert receipt['plan']['series'][1] is None and receipt['plan']['max_daily_posts_with_current_lanes']==4
    assert a.client.post('/api/drama/normal/plan',json=request).json()['replayed']
    at_slot(a,receipt,(0,1))
    with SessionLocal() as s:asyncio.run(qm.publish_queue_item(a.rows[0,1][0],s,regular=True))
    assert len(a.transfers)==2
    assert a.transfers[0]['platform_overrides']['facebook_page_id']==NATIVE['facebook_page_id']
    with SessionLocal() as s:
        assert not dc.complete(s,s.get(QueueItem,a.rows[0,1][0]))
        with pytest.raises(upload_post.UploadPostError):asyncio.run(qm.publish_queue_item(a.rows[0,1][0],s,regular=True))
    assert len(a.transfers)==2


def test_unapproved_second_lane_is_held_and_never_borrows_approved_quota(normal_archive):
    a=normal_archive;ready(a,(0,1));ready(a,(1,1));permission(a,(0,1))
    b=view(a,(1,1))['evidence']['series_key'];r=a.client.post('/api/drama/normal/plan-preview',
        json=plan_request(a,[(0,1)],[view(a,(0,1))['evidence']['series_key'],b]))
    assert r.status_code==409 and 'permission' in r.json()['detail']
    receipt,_=plan(a,[(0,1)])
    assert len(receipt['plan']['assignments'])==1 and receipt['plan']['series'][1] is None


def test_only_four_fixed_A_slots_per_day_and_episode_numbers_override_titles(normal_archive):
    a=normal_archive
    for ep in range(1,6):ready(a,(0,ep))
    permission(a);receipt,_=plan(a,[(0,ep) for ep in reversed(range(1,6))])
    slots=[dt.datetime.fromisoformat(receipt['plan']['assignments'][str(a.rows[0,ep][0])]['at']).astimezone(dc.TZ) for ep in range(1,6)]
    assert [x.strftime('%H:%M') for x in slots]==['10:00','13:00','16:00','19:00','10:00']
    assert slots[-1].date()==slots[0].date()+dt.timedelta(days=1)


@pytest.mark.parametrize('damage',['position','notes','source','accounts','scheduled','rights','native'])
def test_changed_protected_authority_or_native_state_refuses_dispatch(normal_archive,damage):
    a=normal_archive;ready(a,(0,1));grant,_=permission(a);receipt,_=plan(a,[(0,1)]);at_slot(a,receipt,(0,1));qid=a.rows[0,1][0]
    if damage=='native':a.native['x_account_id']='700000000000003'
    elif damage=='rights':
        series=view(a,(0,1))['evidence']['series_key'];scope=a.client.get('/api/drama/normal/permissions/'+series).json()
        r=a.client.post('/api/drama/normal/permissions/revoke',json={'operation_id':str(uuid4()),'series_key':series,
            'expected_permission_fingerprint':scope['fingerprint'],'rights_id':grant['rights_id'],'reason':'Synthetic revocation'})
        assert r.status_code==200,r.text
    else:
        with SessionLocal() as s:
            item=s.get(QueueItem,qid)
            if damage=='position':item.position=99999
            if damage=='notes':item.notes='Real independent user edit'
            if damage=='source':item.source='Other original source'
            if damage=='accounts':item.accounts=['Dramarama:facebook']
            if damage=='scheduled':item.scheduled_at=a.clock[0]+dt.timedelta(minutes=1)
            s.commit()
    with SessionLocal() as s:
        with pytest.raises((upload_post.UploadPostError,HTTPException)):asyncio.run(qm.publish_queue_item(qid,s,regular=True))
    assert not a.transfers


@pytest.mark.parametrize('delta',[-1,300])
def test_no_early_or_missed_slot_catchup(normal_archive,delta):
    a=normal_archive;ready(a,(0,1));permission(a);receipt,_=plan(a,[(0,1)]);at_slot(a,receipt,(0,1));a.clock[0]+=dt.timedelta(seconds=delta)
    with SessionLocal() as s:
        with pytest.raises(upload_post.UploadPostError):asyncio.run(qm.publish_queue_item(a.rows[0,1][0],s,regular=True))
    assert not a.transfers


def test_provider_success_and_preview_URL_cannot_advance_unverified_predecessor(normal_archive):
    a=normal_archive;ready(a,(0,1));ready(a,(0,2));permission(a);receipt,_=plan(a,[(0,1),(0,2)])
    at_slot(a,receipt,(0,1))
    with SessionLocal() as s:asyncio.run(qm.publish_queue_item(a.rows[0,1][0],s,regular=True))
    reconcile(a);at_slot(a,receipt,(0,2))
    with SessionLocal() as s:
        assert not dc.complete(s,s.get(QueueItem,a.rows[0,1][0]))
        with pytest.raises(upload_post.UploadPostError,match='predecessor'):asyncio.run(qm.publish_queue_item(a.rows[0,2][0],s,regular=True))
    assert len(a.transfers)==2


def test_legacy_status_writer_and_unversioned_permit_cannot_bypass_normal_fence(normal_archive):
    a=normal_archive;ready(a,(0,1));permission(a);plan(a,[(0,1)]);qid=a.rows[0,1][0]
    with engine.connect() as connection:
        with pytest.raises(Exception,match='Normal Drama v2 atomic permit'):
            connection.execute(text("UPDATE queue_items SET status='posting',publish_requests=:value WHERE id=:id"),
                {'id':qid,'value':json.dumps([{'client_request_id':'legacy-write'}])})
        connection.rollback()
    with SessionLocal() as s:
        item=s.get(QueueItem,qid)
        s.add(DramaDispatchPermit(queue_id=qid,queue_created_at=item.created_at,attempt_id='legacy-write',
            expires_at=worker_leases.clock(s)+dt.timedelta(minutes=1),protocol=None));s.commit()
    with engine.connect() as connection:
        with pytest.raises(Exception,match='Normal Drama v2 atomic permit'):
            connection.execute(text("UPDATE queue_items SET status='posting',publish_requests=:value WHERE id=:id"),
                {'id':qid,'value':json.dumps([{'client_request_id':'legacy-write'}])})
        connection.rollback()


def test_permission_update_and_plan_cannot_race_live_publication_generation(normal_archive):
    a=normal_archive;ready(a,(0,1));permission(a);req=plan_request(a,[(0,1)])
    preview=a.client.post('/api/drama/normal/plan-preview',json=req);assert preview.status_code==200
    with worker_leases.scope(f'publication:{a.rows[0,1][0]}'):
        r=a.client.post('/api/drama/normal/plan',json={**req,'acknowledged_preview_sha256':preview.json()['preview_sha256']})
        assert r.status_code==409
    assert a.client.get('/api/drama/plan').json()['plan'] is None


@pytest.mark.parametrize('cross_cutoff',[False,True])
def test_preview_ack_survives_elapsed_seconds_but_not_changed_eligible_slot(normal_archive,cross_cutoff):
    a=normal_archive;ready(a,(0,1));permission(a)
    a.clock[0]=dt.datetime.combine(a.clock[0].astimezone(dc.TZ).date(),dt.time(9,58,30),dc.TZ).astimezone(dc.UTC)
    req=plan_request(a,[(0,1)])
    preview=a.client.post('/api/drama/normal/plan-preview',json=req);assert preview.status_code==200,preview.text
    a.clock[0]+=dt.timedelta(seconds=45 if cross_cutoff else 10)
    response=a.client.post('/api/drama/normal/plan',json={**req,'acknowledged_preview_sha256':preview.json()['preview_sha256']})
    assert response.status_code==(409 if cross_cutoff else 200),response.text
    if cross_cutoff:assert a.client.get('/api/drama/plan').json()['plan'] is None
    else:assert response.json()['plan']==preview.json()['plan']


def test_replacement_cannot_omit_unresolved_assignments_or_clear_their_schedule(normal_archive):
    a=normal_archive;ready(a,(0,1));ready(a,(0,2));permission(a)
    plan(a,[(0,1),(0,2)]);old=a.client.get('/api/drama/plan').json()
    before=view(a,(0,2));response=a.client.post('/api/drama/normal/plan-preview',json=plan_request(a,[(0,1)]))
    assert response.status_code==409 and 'omits an unresolved' in response.json()['detail']
    assert view(a,(0,2))==before and a.client.get('/api/drama/plan').json()==old


def test_two_native_bound_lanes_are_independent_when_one_predecessor_is_held(normal_archive):
    a=normal_archive
    for key in [(0,1),(0,2),(1,1)]:ready(a,key)
    permission(a,(0,1));permission(a,(1,1))
    lanes=[view(a,(lane,1))['evidence']['series_key'] for lane in range(2)]
    receipt,_=plan(a,[(0,1),(0,2),(1,1)],lanes)
    at_slot(a,receipt,(0,1))
    with SessionLocal() as s:asyncio.run(qm.publish_queue_item(a.rows[0,1][0],s,regular=True))
    reconcile(a);at_slot(a,receipt,(1,1))
    with SessionLocal() as s:asyncio.run(qm.publish_queue_item(a.rows[1,1][0],s,regular=True))
    assert len(a.transfers)==4
    at_slot(a,receipt,(0,2))
    with SessionLocal() as s:
        with pytest.raises(upload_post.UploadPostError,match='predecessor'):
            asyncio.run(qm.publish_queue_item(a.rows[0,2][0],s,regular=True))
    assert len(a.transfers)==4


@pytest.mark.parametrize('day,offset',[('2031-03-09',-4),('2031-11-02',-5)])
def test_fixed_new_york_slots_survive_both_dst_transitions(normal_archive,day,offset):
    a=normal_archive;ready(a,(0,1));ready(a,(0,2));permission(a)
    a.clock[0]=dt.datetime.combine(dt.date.fromisoformat(day),dt.time(8),dc.TZ).astimezone(dc.UTC)
    receipt,_=plan(a,[(0,1),(0,2)])
    slots=[dt.datetime.fromisoformat(receipt['plan']['assignments'][str(a.rows[0,ep][0])]['at']).astimezone(dc.TZ) for ep in (1,2)]
    assert [v.strftime('%H:%M') for v in slots]==['10:00','13:00']
    assert all(v.utcoffset()==dt.timedelta(hours=offset) for v in slots)


def test_competing_plan_applications_use_one_generation_and_cas(normal_archive):
    a=normal_archive;ready(a,(0,1));permission(a)
    requests=[]
    for _ in range(2):
        req=plan_request(a,[(0,1)]);preview=a.client.post('/api/drama/normal/plan-preview',json=req)
        assert preview.status_code==200,preview.text
        requests.append({**req,'acknowledged_preview_sha256':preview.json()['preview_sha256']})
    def apply(req):return a.client.post('/api/drama/normal/plan',json=req).status_code
    with ThreadPoolExecutor(2) as pool:codes=list(pool.map(apply,requests))
    assert sorted(codes)==[200,409]


def derivative(a):
    from backend.app import drama_media as dm
    proof,_=inspect(a,(0,1));qid=a.rows[0,1][0]
    made=a.client.post(f'/api/drama/items/{qid}/derivatives/prepare',json={'operation_id':str(uuid4()),
        'expected_fingerprint':view(a,(0,1))['fingerprint'],'evidence_id':proof['evidence_id'],'recipe':dm.RECIPE})
    assert made.status_code==200,made.text
    choice={'derivative_id':made.json()['derivative_id'],'document_sha256':made.json()['document_sha256'],
        'decision':'I reviewed the exact original and derivative video and every audio channel, and approve this exact lineage artifact without cuts or content changes'}
    req={'operation_id':str(uuid4()),'expected_fingerprint':view(a,(0,1))['fingerprint'],
        'evidence_id':proof['evidence_id'],'approved_revision':'Synthetic exact normal derivative QA',
        'approved_workbook_sha256':'1'*64,'approved_proposal_sha256':pm.digest({k:view(a,(0,1))[k] for k in pm.PUBLIC_FIELDS}),
        'decision':'Accept this exact canonical episode and public copy for guarded release','derivative':choice}
    preview=a.client.post(f'/api/drama/items/{qid}/review-preview',json=req);assert preview.status_code==200,preview.text
    r=a.client.post(f'/api/drama/items/{qid}/review',json={**req,'acknowledged_fingerprint':preview.json()['acknowledged_fingerprint']})
    assert r.status_code==200,r.text
    r=a.client.post(f'/api/drama/items/{qid}/ready',json={'operation_id':str(uuid4()),'expected_fingerprint':view(a,(0,1))['fingerprint'],
        'evidence_id':proof['evidence_id'],'review_id':req['operation_id']});assert r.status_code==200,r.text
    return made.json(),dm.path(choice['derivative_id'])


def test_actual_regular_derivative_multipart_uses_reviewed_artifact_and_original_identity(normal_archive,monkeypatch):
    from backend.app import drama_media as dm
    a=normal_archive;made,path=derivative(a);permission(a);receipt,_=plan(a,[(0,1)]);at_slot(a,receipt,(0,1));calls=[];real=httpx.AsyncClient
    artifact=path.read_bytes();assert dm.file_hash(path)==made['document']['artifact']['sha256']
    assert dm.file_hash(path)!=made['document']['original']['sha256']
    def handler(req):
        fields=multipart(req);calls.append(fields);assert fields['video']==[artifact]
        if fields['platform[]']==[b'facebook']:assert fields['facebook_page_id']==[NATIVE['facebook_page_id'].encode()]
        return httpx.Response(200,json={'request_id':'synthetic-real-normal-'+str(len(calls))})
    monkeypatch.setattr(settings,'uploadpost_api_key','synthetic-only')
    # The archive fixture replaces upload_video, so take the actual adapter from
    # its undecorated source module captured before fixture application below.
    monkeypatch.setattr(upload_post,'upload_video',REAL_UPLOAD_VIDEO)
    monkeypatch.setattr(httpx,'AsyncClient',lambda **kw:real(transport=httpx.MockTransport(handler),**kw))
    monkeypatch.setattr('backend.app.costs.record_free',lambda *a,**kw:None)
    with SessionLocal() as s:asyncio.run(qm.publish_queue_item(a.rows[0,1][0],s,regular=True))
    assert len(calls)==2


@pytest.mark.parametrize('damage',['artifact','original','review'])
def test_wrong_artifact_original_or_old_review_never_dispatches(normal_archive,monkeypatch,damage):
    a=normal_archive;made,path=derivative(a);permission(a);receipt,_=plan(a,[(0,1)]);at_slot(a,receipt,(0,1))
    if damage=='artifact':path.write_bytes(path.read_bytes()+b'changed actual artifact')
    elif damage=='original':
        from backend.app import drive_sync
        real=drive_sync.download_drive_file
        def changed(*args,**kw):
            p=real(*args,**kw);p.write_bytes(p.read_bytes()+b'changed original');return p
        monkeypatch.setattr(drive_sync,'download_drive_file',changed)
    else:
        with SessionLocal() as s:
            item=s.get(QueueItem,a.rows[0,1][0]);old=dc.latest(s,item,'review')
            s.add(DramaRecord(operation_id=str(uuid4()),kind='review',queue_id=item.id,queue_identity=journal.identity(item),
                request_hash='2'*64,data=copy.deepcopy(old.data)));s.commit()
    with SessionLocal() as s:
        with pytest.raises((upload_post.UploadPostError,HTTPException)):asyncio.run(qm.publish_queue_item(a.rows[0,1][0],s,regular=True))
    assert not a.transfers


def test_partial_derivative_retry_only_failed_destination_in_new_future_slot(normal_archive,monkeypatch):
    a=normal_archive;_,path=derivative(a);permission(a);receipt,_=plan(a,[(0,1)]);at_slot(a,receipt,(0,1));sent=[]
    async def upload(p,**kw):
        assert Path(p).read_bytes()==path.read_bytes();rid='partial-'+str(len(sent));sent.append({'id':rid,**kw});return rid
    async def status(rid):
        call=next(x for x in sent if x['id']==rid);net=call['platforms'][0]
        return {'status':'completed','results':[{'platform':net,'profile_username':'Dramarama',
            'success':not(net=='x' and rid=='partial-1'),'post_url':'https://example.invalid/'+rid}]}
    monkeypatch.setattr(upload_post,'upload_video',upload);monkeypatch.setattr(upload_post,'get_status',status)
    with SessionLocal() as s:asyncio.run(qm.publish_queue_item(a.rows[0,1][0],s,regular=True))
    reconcile(a)
    with SessionLocal() as s:
        item=s.get(QueueItem,a.rows[0,1][0]);assert item.status=='retry' and item.accounts==['Dramarama:x']
    a.clock[0]+=dt.timedelta(minutes=6);later,_=plan(a,[(0,1)]);at_slot(a,later,(0,1))
    with SessionLocal() as s:asyncio.run(qm.publish_queue_item(a.rows[0,1][0],s,regular=True))
    assert [c['platforms'] for c in sent]==[['facebook'],['x'],['x']]


def native_delivery_http(a,monkeypatch,source,damage=None):
    real=httpx.AsyncClient;calls=[]
    with SessionLocal() as s:payload=dc.payload(s,s.get(QueueItem,a.rows[0,1][0]))
    async def status(rid):
        call=next(x for x in a.transfers if x['id']==rid);network=call['platforms'][0]
        url=('https://www.facebook.com/'+NATIVE['facebook_page_id']+'/videos/700000000000111'
            if network=='facebook' else 'https://x.com/synthetic_drama/status/700000000000222')
        return {'status':'completed','results':[{'success':True,'profile_username':'Dramarama','platform':network,'post_url':url}]}
    monkeypatch.setattr(upload_post,'get_status',status)
    monkeypatch.setattr(settings,'drama_native_reads_enabled',True)
    monkeypatch.setattr(settings,'drama_fb_read_token','synthetic-fb-read')
    monkeypatch.setattr(settings,'drama_x_read_token','synthetic-x-read')
    def handler(req):
        calls.append(req.url.host)
        if req.url.host=='graph.facebook.com':
            body={'id':'700000000000111','from':{'id':NATIVE['facebook_page_id']},
                'title':payload['facebook_title'],'description':payload['facebook_description'],
                'source':'https://video.xx.fbcdn.net/served.mp4?secret=must-not-retain','length':3.0,
                'published':True,'permalink_url':'/'+NATIVE['facebook_page_id']+'/videos/700000000000111'}
            if damage=='facebook_title':body['title']='Changed native title'
            if damage=='facebook_owner':body['from']['id']='700000000000099'
            return httpx.Response(200,json=body)
        if req.url.host=='api.x.com':
            body={'data':{'id':'700000000000222','author_id':NATIVE['x_account_id'],
                'text':payload['x_title'],'attachments':{'media_keys':['7_synthetic']}},
                'includes':{'users':[{'id':NATIVE['x_account_id'],'username':NATIVE['x_handle']}],
                    'media':[{'media_key':'7_synthetic','type':'video','duration_ms':3000,
                        'variants':[{'content_type':'video/mp4','bit_rate':128000,
                            'url':'https://video.twimg.com/served.mp4?secret=must-not-retain'}]}]}}
            if damage=='x_copy':body['data']['text']='Different native text'
            if damage=='x_owner':body['data']['author_id']='700000000000099'
            if damage=='x_preview':body['includes']['media'][0]['variants']=[]
            return httpx.Response(200,json=body)
        assert req.url.host in ('video.xx.fbcdn.net','video.twimg.com')
        assert 'authorization' not in req.headers
        return httpx.Response(200,content=source,headers={'content-type':'video/mp4'})
    monkeypatch.setattr(httpx,'AsyncClient',lambda **kw:real(transport=httpx.MockTransport(handler),**kw))
    return calls


def verify_request(a):
    v=view(a,(0,1));return {'operation_id':str(uuid4()),'expected_fingerprint':v['fingerprint'],'ready_id':v['ready_id']}


@pytest.mark.parametrize('poll_first',[False,True])
def test_actual_native_media_and_copy_evidence_can_complete_both_destinations_without_booleans(normal_archive,monkeypatch,poll_first):
    a=normal_archive;ready(a,(0,1));permission(a);receipt,_=plan(a,[(0,1)]);at_slot(a,receipt,(0,1));qid=a.rows[0,1][0]
    with SessionLocal() as s:asyncio.run(qm.publish_queue_item(qid,s,regular=True))
    # Use the exact scenario's reviewed file, including its unique harmless free atom.
    source=a.download(view(a,(0,1))['evidence']['drive_file_id']);data=source.read_bytes();source.unlink()
    calls=native_delivery_http(a,monkeypatch,data)
    if poll_first:reconcile(a)
    req=verify_request(a);r=a.client.post(f'/api/drama/normal/items/{qid}/verify-delivery',json=req)
    assert r.status_code==200,r.text
    assert r.json()['delivery_verified'] and all(r.json()['destination_verified'].values())
    assert 'graph.facebook.com' in calls and 'api.x.com' in calls and 'video.twimg.com' in calls
    with SessionLocal() as s:
        item=s.get(QueueItem,qid);assert item.status=='archived' and dc.complete(s,item)
        record=dc.latest(s,item,'normal_delivery');assert 'must-not-retain' not in json.dumps(record.data)
    assert a.client.post(f'/api/drama/normal/items/{qid}/verify-delivery',json=req).json()['replayed']
    assert len(a.transfers)==2


@pytest.mark.parametrize('change',['none','renewed_permission','missing_proof','uncertain','partial','failure','different_native_id','held_native','identity_revision','revoked_rights'])
def test_day_three_predecessor_is_durable_without_native_rereads_but_later_contradictions_hold(normal_archive,monkeypatch,change):
    a=normal_archive;ready(a,(0,1));ready(a,(0,2));grant,_=permission(a)
    receipt,_=plan(a,[(0,1)]);at_slot(a,receipt,(0,1));qid=a.rows[0,1][0]
    with SessionLocal() as s:asyncio.run(qm.publish_queue_item(qid,s,regular=True))
    path=a.download(view(a,(0,1))['evidence']['drive_file_id']);data=path.read_bytes();path.unlink()
    calls=native_delivery_http(a,monkeypatch,data)
    if change!='missing_proof':
        response=a.client.post(f'/api/drama/normal/items/{qid}/verify-delivery',json=verify_request(a))
        assert response.status_code==200,response.text
        assert response.json()['delivery_verified']
    # New native reads must never be necessary to retain this historical fact.
    a.clock[0]+=dt.timedelta(days=3);count=len(calls)
    if change=='renewed_permission':permission(a)
    if change in ('uncertain','partial','failure','different_native_id'):
        with SessionLocal() as s:
            item=s.get(QueueItem,qid);_,r=dc.readiness(s,item)
            request=next(v['entry'] for v in delivery.requests_for(s,item,r).values() if v['entry']['platforms']==['x'])
            status={'status':'processing'} if change=='uncertain' else {'status':'completed','results':[]}
            if change in ('failure','different_native_id'):
                status['results']=[{'profile_username':'Dramarama','platform':'x','success':change!='failure',
                    'post_url':'https://x.com/synthetic_drama/status/700000000000999'}]
            journal.record(s,item,{**request,'observed_status':status},'provider_observation');s.commit()
    if change=='held_native':
        native_delivery_http(a,monkeypatch,data,'x_copy')
        response=a.client.post(f'/api/drama/normal/items/{qid}/verify-delivery',json=verify_request(a))
        assert response.status_code==200 and not response.json()['delivery_verified']
        count=len(calls)
    if change=='identity_revision':
        with SessionLocal() as s:
            item=s.get(QueueItem,qid);item.description='Independent revised copy';s.commit()
    if change=='revoked_rights':
        series=view(a,(0,1))['evidence']['series_key'];scope=a.client.get('/api/drama/normal/permissions/'+series).json()
        response=a.client.post('/api/drama/normal/permissions/revoke',json={'operation_id':str(uuid4()),'series_key':series,
            'expected_permission_fingerprint':scope['fingerprint'],'rights_id':grant['rights_id'],'reason':'Synthetic current revocation'})
        assert response.status_code==200,response.text
    with SessionLocal() as s:
        assert dc.complete(s,s.get(QueueItem,qid))==(change in ('none','renewed_permission'))
    assert len(calls)==count
    if change in ('none','renewed_permission'):
        later,_=plan(a,[(0,2)]);at_slot(a,later,(0,2))
        with SessionLocal() as s:asyncio.run(qm.publish_queue_item(a.rows[0,2][0],s,regular=True))
        assert len(a.transfers)==4 and len(calls)==count
    else:
        response=a.client.post('/api/drama/normal/plan-preview',json=plan_request(a,[(0,2)]))
        assert response.status_code==409,response.text
        assert len(a.transfers)==2 and len(calls)==count


def test_native_json_stream_stops_at_bound_without_reading_entire_body(monkeypatch):
    from backend.app import drama_native
    monkeypatch.setattr(settings,'drama_native_reads_enabled',True)
    monkeypatch.setattr(settings,'drama_x_read_token','synthetic-read-only')
    chunks=[];real=httpx.AsyncClient
    class Huge(httpx.AsyncByteStream):
        async def __aiter__(self):
            for i in range(1000):
                chunks.append(i);yield b' '*65536
    def handler(req):return httpx.Response(200,stream=Huge())
    monkeypatch.setattr(httpx,'AsyncClient',lambda **kw:real(transport=httpx.MockTransport(handler),**kw))
    result=asyncio.run(drama_native.read('x','https://x.com/synthetic_drama/status/700000000000222',NATIVE,{}))
    assert not result['native_observed'] and result['state']=='held'
    assert len(chunks)==17 and 'synthetic-read-only' not in json.dumps(result)


@pytest.mark.parametrize('damage',['facebook_title','facebook_owner','x_copy','x_owner','x_preview','not_configured'])
def test_missing_or_wrong_native_proof_cannot_certify_both_successes(normal_archive,monkeypatch,damage):
    a=normal_archive;ready(a,(0,1));permission(a);receipt,_=plan(a,[(0,1)]);at_slot(a,receipt,(0,1));qid=a.rows[0,1][0]
    with SessionLocal() as s:asyncio.run(qm.publish_queue_item(qid,s,regular=True))
    path=a.download(view(a,(0,1))['evidence']['drive_file_id']);data=path.read_bytes();path.unlink()
    calls=native_delivery_http(a,monkeypatch,data,damage)
    if damage=='not_configured':monkeypatch.setattr(settings,'drama_native_reads_enabled',False)
    r=a.client.post(f'/api/drama/normal/items/{qid}/verify-delivery',json=verify_request(a))
    assert r.status_code==200,r.text
    assert not r.json()['delivery_verified']
    with SessionLocal() as s:assert not dc.complete(s,s.get(QueueItem,qid))
    if damage=='not_configured':assert not calls
    extra={**verify_request(a),'delivery_verified':True}
    assert a.client.post(f'/api/drama/normal/items/{qid}/verify-delivery',json=extra).status_code==422


REAL_UPLOAD_VIDEO=upload_post.upload_video
