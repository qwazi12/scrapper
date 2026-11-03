"""Explicit original synthetic EP1 grants; no real authorization or public posts."""
import asyncio
import copy
import datetime as dt
import threading
import time
from concurrent.futures import ThreadPoolExecutor
from uuid import uuid4

import httpx
import pytest

from backend.app import drama_canary as canary, drama_canary_routes as routes, drama_controls as dc, drama_archive, pending_metadata as pm
from backend.app.db import SessionLocal
from backend.app.models import DramaRecord, QueueItem, PublicationEvent, AppSetting
from backend.app.social import queue_manager as qm, upload_post
from backend.tests.test_drama_controls import archive, original_video, ready, inspect, view, reconcile


DECISION='I specifically authorize ONE public EP1 canary to the exact reviewed Facebook Page and X account at this future slot, and confirm rights for this exact source and copy'


@pytest.fixture
def candidate(archive,monkeypatch):
    a=archive
    # Shared append-only test DB; only the server policy namespace is isolated
    # per synthetic scenario. Production has one fixed non-client scope key.
    monkeypatch.setattr(canary,'SCOPE','synthetic-canary-'+a.mid)
    with SessionLocal() as s:
        s.get(QueueItem,a.rows[0,1][0]).accounts=['Dramarama:*'];s.commit()
    async def connected():
        return [{'id':'Dramarama:'+p,'profile':'Dramarama'} for p in ('facebook','x','youtube')]
    monkeypatch.setattr(upload_post,'list_accounts',connected)
    saved,_=ready(a,(0,1));inspect(a,(1,1))
    monkeypatch.setattr(dc,'require_activation_capability',a.real_activation_gate)
    monkeypatch.setattr(dc,'delivery_fidelity_verified',lambda *args:False)
    target={'profile':'Dramarama','accounts':['Dramarama:facebook','Dramarama:x'],
        'facebook_connection_id':'synthetic-meta-owner','facebook_page_id':'700000000000001',
        'facebook_page_name':'Original Synthetic Page','facebook_pinned_page_id':'700000000000001',
        'profile_pinned_page_id':'700000000000001','pin_evidence':'matching native profile and Page/pin GET responses',
        'x_account_id':'synthetic-native-x-owner','x_handle':'synthetic_original_account',
        'x_entitlement':'unverified; conservative baseline only'}
    reads=[]
    async def native(page):
        assert page=='700000000000001';reads.append(copy.deepcopy(target));return copy.deepcopy(target)
    monkeypatch.setattr(upload_post,'canary_target_identity',native)
    with SessionLocal() as s:visible=dc.payload(s,s.get(QueueItem,a.rows[0,1][0]))
    req={'operation_id':str(uuid4()),'expected_fingerprint':view(a,(0,1))['fingerprint'],
        'evidence_id':view(a,(0,1))['evidence']['id'],'review_id':view(a,(0,1))['review_id'],
        'ready_id':saved['ready_id'],'series':[view(a,(n,1))['evidence']['series_key'] for n in (0,1)],
        'slot':(a.clock[0]+dt.timedelta(hours=2)).isoformat(),
        'targets':{'facebook_page_id':target['facebook_page_id'],'x_account_id':target['x_account_id'],'x_handle':target['x_handle']},
        'authorization':{'authorizer':'Synthetic original-media owner','authorization_reference':'Synthetic fixture-specific authorization only',
            'authorized_public_payload_sha256':pm.digest(visible),'rights_basis':'owned_original',
            'authorized_source_sha256':view(a,(0,1))['evidence']['sha256'],
            'rights_reference':'Original locally generated synthetic H264/AAC fixture, not third-party media','decision':DECISION}}
    a.canary_req=req;a.native_target=target;a.native_reads=reads
    return a


def grant(a,req=None):
    req=copy.deepcopy(req or a.canary_req);qid=a.rows[0,1][0]
    preview=a.client.post(f'/api/drama/canary/items/{qid}/preview',json=req)
    assert preview.status_code==200,preview.text
    req['acknowledged_fingerprint']=preview.json()['acknowledged_fingerprint']
    saved=a.client.post(f'/api/drama/canary/items/{qid}/grants',json=req)
    assert saved.status_code==200,saved.text
    return saved.json(),req


def dispatch_request(a,saved):
    return {'operation_id':str(uuid4()),'expected_fingerprint':a.canary_req['expected_fingerprint'],
        'grant_id':saved['grant_id'],'grant_sha256':saved['grant_sha256']}


def dispatch(a,req):
    return a.client.post(f'/api/drama/canary/items/{a.rows[0,1][0]}/dispatch',json=req)


def at_slot(a):a.clock[0]=dt.datetime.fromisoformat(a.canary_req['slot'])


def test_preview_and_grant_are_exact_owner_evidence_only_without_queue_or_schedule_changes(candidate):
    a=candidate;qid=a.rows[0,1][0];before=view(a,(0,1))
    saved,req=grant(a)
    assert view(a,(0,1))==before and not a.transfers
    assert a.client.get('/api/drama/plan').json()['plan'] is None
    status=a.client.get('/api/drama/canary/grants/'+saved['grant_id']).json()
    assert not status['revoked'] and status['dispatch_id'] is None
    assert status['document']['authorization']['rights_reference']==req['authorization']['rights_reference']
    assert status['document']['target_identity']['facebook_page_id']==a.native_target['facebook_page_id']
    assert status['fidelity_verified'] is False and not status['automatic_plan_activated']
    count=len(a.native_reads)
    replay=a.client.post(f'/api/drama/canary/items/{qid}/grants',json=req)
    assert replay.status_code==200 and replay.json()['replayed'] and len(a.native_reads)==count
    req['authorization']['rights_reference']='Different authorization evidence'
    assert a.client.post(f'/api/drama/canary/items/{qid}/grants',json=req).status_code==409
    ordinary=a.client.post('/api/drama/plan',json={'operation_id':str(uuid4()),
        'expected_plan_fingerprint':a.client.get('/api/drama/plan').json()['fingerprint'],
        'series':a.canary_req['series'],'items':[{'id':qid,'expected_fingerprint':before['fingerprint']}]})
    assert ordinary.status_code==409 and not a.transfers


def test_reviewable_preview_does_not_require_fabricating_owner_rights_or_grant_authority(candidate):
    a=candidate;req=copy.deepcopy(a.canary_req);req['authorization']=None;qid=a.rows[0,1][0]
    preview=a.client.post(f'/api/drama/canary/items/{qid}/preview',json=req)
    assert preview.status_code==200,preview.text
    assert not preview.json()['document']['authorization_complete']
    assert preview.json()['document']['authorization'] is None
    req['acknowledged_fingerprint']=preview.json()['acknowledged_fingerprint']
    denied=a.client.post(f'/api/drama/canary/items/{qid}/grants',json=req)
    assert denied.status_code==422
    assert a.client.get('/api/drama/canary/contract').json()['grant_count']==0
    assert not a.transfers


@pytest.mark.parametrize('damage',['missing_rights','blank_reference','broad_rollout','payload_hash','source_hash','ep2','other_series','youtube','naive_slot','wrong_slot'])
def test_specific_source_copy_rights_authorization_and_slot_cannot_be_inferred(candidate,damage):
    a=candidate;req=copy.deepcopy(a.canary_req);qid=a.rows[0,1][0]
    if damage=='missing_rights':req['authorization'].pop('rights_basis')
    elif damage=='blank_reference':req['authorization']['rights_reference']=' '
    elif damage=='broad_rollout':req['authorization']['decision']='Deploy approved; checked=true'
    elif damage=='payload_hash':req['authorization']['authorized_public_payload_sha256']='0'*64
    elif damage=='source_hash':req['authorization']['authorized_source_sha256']='0'*64
    elif damage=='ep2':
        qid=a.rows[0,2][0];req['expected_fingerprint']=view(a,(0,2))['fingerprint']
    elif damage=='other_series':req['series'].reverse()
    elif damage=='youtube':req['targets']['youtube_account']='Dramarama:youtube'
    elif damage=='naive_slot':req['slot']=dt.datetime.fromisoformat(req['slot']).replace(tzinfo=None).isoformat()
    elif damage=='wrong_slot':req['slot']=(dt.datetime.fromisoformat(req['slot'])+dt.timedelta(minutes=30)).isoformat()
    result=a.client.post(f'/api/drama/canary/items/{qid}/preview',json=req)
    assert result.status_code in (409,422),result.text
    assert not a.transfers


def test_one_shot_dispatch_uses_original_request_keys_then_only_replays_and_never_advances(candidate):
    a=candidate;saved,_=grant(a);req=dispatch_request(a,saved);at_slot(a)
    result=dispatch(a,req)
    assert result.status_code==200,result.text
    assert len(a.transfers)==2 and [t['platforms'] for t in a.transfers]==[['facebook'],['x']]
    assert a.transfers[0]['platform_overrides']['facebook_page_id']==a.native_target['facebook_page_id']
    keys=[t['idempotency_key'] for t in a.transfers];assert len(set(keys))==2
    for _ in range(2):
        replay=dispatch(a,req);assert replay.status_code==200 and replay.json()['replayed']
    assert len(a.transfers)==2
    another={**req,'operation_id':str(uuid4())}
    assert dispatch(a,another).status_code==409 and len(a.transfers)==2
    assert reconcile(a)==1
    with SessionLocal() as s:
        item=s.get(QueueItem,a.rows[0,1][0]);assert item.status=='review' and item.published_at is None
        assert not dc.complete(s,item)
        assert len(dc.attempted_today(s,None,a.clock[0]))==1
        claimed=s.query(DramaRecord).filter_by(operation_id=req['operation_id']).one()
        assert set(claimed.data['request_keys'].values())==set(keys)
        assert s.query(PublicationEvent).filter_by(queue_id=item.id,kind='batch_intent').count()==1
    assert a.client.get('/api/drama/plan').json()['plan'] is None
    assert dispatch(a,req).json()['replayed'] and len(a.transfers)==2


@pytest.mark.parametrize('change',['copy','drive','accounts','youtube','source','status'])
def test_stale_grant_protected_row_changes_refuse_before_transport(candidate,change):
    a=candidate;saved,_=grant(a);req=dispatch_request(a,saved);at_slot(a)
    with SessionLocal() as s:
        row=s.get(QueueItem,a.rows[0,1][0])
        if change=='copy':row.title+=' Newer independent words'
        elif change=='drive':row.drive_link='https://drive.google.com/file/d/different-source/view'
        elif change=='accounts':row.accounts=['Dramarama:x']
        elif change=='youtube':row.accounts=['Dramarama:facebook','Dramarama:x','Dramarama:youtube']
        elif change=='source':row.source='Independent source revision'
        elif change=='status':row.status='review'
        s.commit()
    result=dispatch(a,req);assert result.status_code==409,result.text
    assert not a.transfers


@pytest.mark.parametrize('change',['page','pin','x_id','x_handle','expiry','revoked','future'])
def test_expired_revoked_or_changed_native_target_grant_never_dispatches(candidate,change):
    a=candidate;saved,_=grant(a);req=dispatch_request(a,saved);at_slot(a)
    if change=='page':a.native_target['facebook_page_id']='700000000000002'
    elif change=='pin':a.native_target['facebook_pinned_page_id']='700000000000002'
    elif change=='x_id':a.native_target['x_account_id']='different-native-owner'
    elif change=='x_handle':a.native_target['x_handle']='another_account'
    elif change=='expiry':a.clock[0]+=dt.timedelta(minutes=5)
    elif change=='future':a.clock[0]-=dt.timedelta(minutes=1)
    elif change=='revoked':
        r=a.client.post('/api/drama/canary/grants/'+saved['grant_id']+'/revoke',json={
            'operation_id':str(uuid4()),'grant_sha256':saved['grant_sha256'],'reason':'Synthetic explicit owner revocation'})
        assert r.status_code==200,r.text
    result=dispatch(a,req);assert result.status_code==409,result.text
    assert not a.transfers


@pytest.mark.parametrize('outcome',['partial','unknown'])
def test_partial_or_unknown_outcome_keeps_original_operation_without_successful_destination_resend(candidate,monkeypatch,outcome):
    a=candidate;saved,_=grant(a);req=dispatch_request(a,saved);at_slot(a)
    original_upload=upload_post.upload_video;original_status=upload_post.get_status
    async def upload(path,**kw):
        if outcome=='unknown' and kw['platforms']==['x']:raise TimeoutError('Synthetic response lost after possible acceptance')
        return await original_upload(path,**kw)
    async def status(rid):
        value=await original_status(rid)
        if outcome=='partial' and value['results'][0]['platform']=='x':value['results'][0].update(success=False,error_message='Synthetic confirmed failure')
        else:value['results'][0]['post_id']='synthetic-native-post-'+rid
        return value
    monkeypatch.setattr(upload_post,'upload_video',upload);monkeypatch.setattr(upload_post,'get_status',status)
    assert dispatch(a,req).status_code==200
    count=len(a.transfers);reconcile(a)
    assert dispatch(a,req).json()['replayed'] and len(a.transfers)==count
    with SessionLocal() as s:
        item=s.get(QueueItem,a.rows[0,1][0]);assert item.status==('posting' if outcome=='unknown' else 'review')
        assert not dc.complete(s,item)
    assert dispatch(a,{**req,'operation_id':str(uuid4())}).status_code==409


def test_concurrent_same_dispatch_claim_has_one_durable_operation(candidate,monkeypatch):
    a=candidate;saved,_=grant(a);req=dispatch_request(a,saved);at_slot(a)
    entered,finish=threading.Event(),threading.Event();original=upload_post.upload_video
    async def held(path,**kw):
        if kw['platforms']==['facebook']:
            entered.set();assert finish.wait(10)
        return await original(path,**kw)
    monkeypatch.setattr(upload_post,'upload_video',held)
    with ThreadPoolExecutor(max_workers=2) as pool:
        first=pool.submit(dispatch,a,req);assert entered.wait(10)
        replay=dispatch(a,req);assert replay.status_code==200 and replay.json()['replayed']
        finish.set();result=first.result(timeout=20);assert result.status_code==200,result.text
    assert len(a.transfers)==2


def test_concurrent_different_dispatch_cannot_spend_the_same_one_shot_grant(candidate,monkeypatch):
    a=candidate;saved,_=grant(a);req=dispatch_request(a,saved);at_slot(a)
    entered,finish=threading.Event(),threading.Event();original=upload_post.upload_video
    async def held(path,**kw):
        if kw['platforms']==['facebook']:entered.set();assert finish.wait(10)
        return await original(path,**kw)
    monkeypatch.setattr(upload_post,'upload_video',held)
    with ThreadPoolExecutor(max_workers=1) as pool:
        first=pool.submit(dispatch,a,req);assert entered.wait(10)
        refused=dispatch(a,{**req,'operation_id':str(uuid4())})
        assert refused.status_code==409
        finish.set();assert first.result(timeout=20).status_code==200
    assert len(a.transfers)==2


def test_grant_does_not_enable_generic_manual_publish_or_scheduler_admission(candidate):
    a=candidate;grant(a);at_slot(a)
    with SessionLocal() as s,pytest.raises(upload_post.UploadPostError):
        asyncio.run(qm.publish_queue_item(a.rows[0,1][0],s))
    assert not a.transfers
    assert a.client.get('/api/drama/canary/contract').json()['consumed_grant_count']==0


def test_target_change_between_destinations_holds_x_without_resending_facebook(candidate,monkeypatch):
    a=candidate;saved,_=grant(a);req=dispatch_request(a,saved);at_slot(a)
    original=upload_post.upload_video
    async def changed(path,**kw):
        result=await original(path,**kw)
        if kw['platforms']==['facebook']:a.native_target['x_account_id']='different-native-x'
        return result
    monkeypatch.setattr(upload_post,'upload_video',changed)
    assert dispatch(a,req).status_code==200
    assert [t['platforms'] for t in a.transfers]==[['facebook']]
    with SessionLocal() as s:
        row=s.get(QueueItem,a.rows[0,1][0]);x=next(e for e in row.publish_requests if e['platforms']==['x'])
        assert x['state']=='rejected' and x['transport_dispatched'] is False
    reconcile(a)
    assert dispatch(a,req).json()['replayed'] and len(a.transfers)==1


def test_independent_protected_edit_between_destinations_holds_x_and_preserves_new_state(candidate,monkeypatch):
    a=candidate;saved,_=grant(a);req=dispatch_request(a,saved);at_slot(a)
    original=upload_post.upload_video
    async def changed(path,**kw):
        result=await original(path,**kw)
        if kw['platforms']==['facebook']:
            with SessionLocal() as s:
                s.get(QueueItem,a.rows[0,1][0]).source='Newer independent protected source';s.commit()
        return result
    monkeypatch.setattr(upload_post,'upload_video',changed)
    assert dispatch(a,req).status_code==200
    assert [t['platforms'] for t in a.transfers]==[['facebook']]
    with SessionLocal() as s:
        item=s.get(QueueItem,a.rows[0,1][0])
        x=next(e for e in item.publish_requests if e['platforms']==['x'])
        assert x['state']=='rejected' and x['transport_dispatched'] is False
        assert item.source=='Newer independent protected source'
        assert s.query(PublicationEvent).filter_by(queue_id=item.id,kind='intent').count()==1
    assert dispatch(a,req).json()['replayed'] and len(a.transfers)==1


def test_revoke_during_first_transport_stops_unsent_second_target_without_rewinding_consumption(candidate,monkeypatch):
    a=candidate;saved,_=grant(a);req=dispatch_request(a,saved);at_slot(a)
    entered,finish=threading.Event(),threading.Event();original=upload_post.upload_video
    async def held(path,**kw):
        if kw['platforms']==['facebook']:entered.set();assert finish.wait(10)
        return await original(path,**kw)
    monkeypatch.setattr(upload_post,'upload_video',held)
    with ThreadPoolExecutor(max_workers=1) as pool:
        pending=pool.submit(dispatch,a,req);assert entered.wait(10)
        revoked=a.client.post('/api/drama/canary/grants/'+saved['grant_id']+'/revoke',json={
            'operation_id':str(uuid4()),'grant_sha256':saved['grant_sha256'],'reason':'Synthetic owner withdraws remaining unsent public operation'})
        assert revoked.status_code==200,revoked.text
        finish.set();assert pending.result(timeout=20).status_code==200
    assert [t['platforms'] for t in a.transfers]==[['facebook']]
    status=a.client.get('/api/drama/canary/grants/'+saved['grant_id']).json()
    assert status['revoked'] and status['dispatch_id']==req['operation_id']
    assert dispatch(a,req).json()['replayed'] and len(a.transfers)==1


def test_source_hash_or_independent_copy_change_at_final_boundary_leaves_no_canary_claim(candidate,monkeypatch):
    a=candidate;saved,_=grant(a);req=dispatch_request(a,saved);at_slot(a)
    from backend.app.studio import media_quality
    original=media_quality.file_hash;changed=[]
    def external(path):
        result=original(path)
        if not changed:
            changed.append(True)
            with SessionLocal() as s:
                s.get(QueueItem,a.rows[0,1][0]).description+=' Newer independent edit';s.commit()
        return result
    monkeypatch.setattr(media_quality,'file_hash',external)
    result=dispatch(a,req);assert result.status_code==409,result.text
    with SessionLocal() as s:
        assert s.query(DramaRecord).filter_by(operation_id=req['operation_id']).first() is None
        assert s.get(QueueItem,a.rows[0,1][0]).description.endswith('Newer independent edit')
    assert not a.transfers


def test_actual_downloaded_asset_change_fails_before_durable_dispatch(candidate,monkeypatch):
    a=candidate;saved,_=grant(a);req=dispatch_request(a,saved);at_slot(a)
    original=drama_archive.drive_sync.download_drive_file
    def changed(fid,**kw):
        path=original(fid,**kw);path.write_bytes(path.read_bytes()+b'synthetic independent source revision');return path
    monkeypatch.setattr(drama_archive.drive_sync,'download_drive_file',changed)
    result=dispatch(a,req);assert result.status_code==409,result.text
    assert not a.transfers
    assert a.client.get('/api/drama/canary/grants/'+saved['grant_id']).json()['dispatch_id'] is None


def test_crash_after_atomic_claim_is_recoverable_by_original_receipt_without_transport_retry(candidate,monkeypatch):
    a=candidate;saved,_=grant(a);req=dispatch_request(a,saved);at_slot(a)
    async def crash_submit(*args,**kwargs):raise SystemExit('Synthetic process exit after durable batch claim')
    monkeypatch.setattr(qm,'submit_upload',crash_submit)
    value=canary.Dispatch(req['operation_id'],routes.hashed('dispatch',routes.Dispatch.model_validate(req),a.rows[0,1][0]),
        saved['grant_id'],a.rows[0,1][0],req['expected_fingerprint'],saved['grant_sha256'],copy.deepcopy(a.native_target),time.monotonic())
    with SessionLocal() as s,canary.dispatch_scope(value),pytest.raises(SystemExit):
        asyncio.run(qm.publish_queue_item(a.rows[0,1][0],s))
    replay=dispatch(a,req);assert replay.status_code==200 and replay.json()['replayed']
    with SessionLocal() as s:
        item=s.get(QueueItem,a.rows[0,1][0]);assert item.status=='posting'
        assert s.query(PublicationEvent).filter_by(queue_id=item.id,kind='batch_intent').count()==1
    assert not a.transfers


def test_independent_process_can_recover_committed_claim_without_transport_or_target_read(candidate,monkeypatch):
    import json
    import subprocess
    import sys
    a=candidate;saved,_=grant(a);req=dispatch_request(a,saved);at_slot(a)
    assert dispatch(a,req).status_code==200
    digest=routes.hashed('dispatch',routes.Dispatch.model_validate(req),a.rows[0,1][0])
    # A fresh interpreter has no ContextVar, provider fake, grant cache or lease.
    # It only reads the original durable receipt from this isolated fixture DB.
    script='''
import json,sys
from backend.app.e2e_safety import install_network_guard
install_network_guard()
from backend.app.db import SessionLocal
from backend.app.drama_controls import replay
with SessionLocal() as s:
    print(json.dumps(replay(s,sys.argv[1],sys.argv[2])))
'''
    result=json.loads(subprocess.check_output([sys.executable,'-c',script,req['operation_id'],digest],text=True))
    assert result['replayed'] and result['dispatch_id']==req['operation_id']
    assert result['resend_authorized'] is False and len(a.transfers)==2


def test_revoked_unconsumed_grant_can_renew_only_same_original_ep1_scope(candidate):
    a=candidate;first,_=grant(a)
    revoke={'operation_id':str(uuid4()),'grant_sha256':first['grant_sha256'],'reason':'Synthetic owner withdraws unconsumed grant'}
    assert a.client.post('/api/drama/canary/grants/'+first['grant_id']+'/revoke',json=revoke).status_code==200
    req=copy.deepcopy(a.canary_req);req['operation_id']=str(uuid4())
    replacement,_=grant(a,req)
    assert replacement['grant_id']!=first['grant_id']
    # The other canonical series cannot acquire another canary in this scope.
    ready_b,_=ready(a,(1,1))
    req.update(operation_id=str(uuid4()),expected_fingerprint=view(a,(1,1))['fingerprint'],
        evidence_id=view(a,(1,1))['evidence']['id'],review_id=view(a,(1,1))['review_id'],ready_id=ready_b['ready_id'],
        series=list(reversed(req['series'])))
    with SessionLocal() as s:req['authorization']['authorized_public_payload_sha256']=pm.digest(dc.payload(s,s.get(QueueItem,a.rows[1,1][0])))
    result=a.client.post(f'/api/drama/canary/items/{a.rows[1,1][0]}/preview',json=req)
    assert result.status_code==409


def test_ready_rollback_and_generic_reapprove_cannot_reactivate_a_grant(candidate):
    a=candidate;saved,_=grant(a);qid=a.rows[0,1][0]
    response=a.client.post('/api/drama/receipts/'+a.canary_req['ready_id']+'/rollback',json={
        'operation_id':str(uuid4()),'expected_plan_fingerprint':a.client.get('/api/drama/plan').json()['fingerprint']})
    assert response.status_code==200,response.text
    with SessionLocal() as s:s.get(QueueItem,qid).status='ready';s.commit()
    at_slot(a)
    result=dispatch(a,dispatch_request(a,saved));assert result.status_code==409
    assert not a.transfers


def test_provider_target_reader_binds_page_pin_and_native_x_not_display_name(monkeypatch):
    from backend.app.config import settings
    profile={'success':True,'profile':{'username':'Dramarama','facebook_page_id':'700000000000001','social_accounts':{
        'facebook':{'username':'synthetic-fb-oauth-owner','display_name':'Unrelated OAuth display name'},
        'x':{'username':'synthetic-stable-x-id','handle':'synthetic_public_handle','display_name':'Different display label','reauth_required':False},
        'youtube':{'username':'unapproved-connected-channel'}}}}
    pages={'success':True,'pages':[{'id':'700000000000001','name':'Synthetic Page'}],'selected_page_id':'700000000000001'}
    calls=[]
    def handler(req):
        calls.append(req.url.path);assert req.method=='GET'
        return httpx.Response(200,json=profile if req.url.path.endswith('/Dramarama') else pages)
    real=httpx.AsyncClient
    monkeypatch.setattr(settings,'uploadpost_api_key','synthetic-not-a-credential')
    monkeypatch.setattr(httpx,'AsyncClient',lambda **kw:real(transport=httpx.MockTransport(handler),**kw))
    result=asyncio.run(upload_post.canary_target_identity('700000000000001'))
    assert result['x_account_id']=='synthetic-stable-x-id' and result['x_handle']=='synthetic_public_handle'
    assert result['facebook_page_name']=='Synthetic Page' and result['facebook_pinned_page_id']=='700000000000001'
    assert result['accounts']==['Dramarama:facebook','Dramarama:x'] and len(calls)==2
    pages['selected_page_id']='700000000000002'
    with pytest.raises(upload_post.UploadPostError):asyncio.run(upload_post.canary_target_identity('700000000000001'))
    pages['selected_page_id']=None
    with pytest.raises(upload_post.UploadPostError):asyncio.run(upload_post.canary_target_identity('700000000000001'))
    pages['selected_page_id']='700000000000001';profile['profile']['facebook_page_id']='700000000000002'
    with pytest.raises(upload_post.UploadPostError):asyncio.run(upload_post.canary_target_identity('700000000000001'))
    profile['profile'].pop('facebook_page_id')
    with pytest.raises(upload_post.UploadPostError):asyncio.run(upload_post.canary_target_identity('700000000000001'))
    # The documented selected_page_id profile spelling is authoritative too.
    profile['profile']['selected_page_id']='700000000000001'
    assert asyncio.run(upload_post.canary_target_identity('700000000000001'))['profile_pinned_page_id']=='700000000000001'
    profile['profile']['facebook_page_id']=None
    with pytest.raises(upload_post.UploadPostError):asyncio.run(upload_post.canary_target_identity('700000000000001'))
    profile['profile']['facebook_page_id']='700000000000002'
    with pytest.raises(upload_post.UploadPostError):asyncio.run(upload_post.canary_target_identity('700000000000001'))
    profile['profile'].pop('facebook_page_id');pages['facebook_page_id']='700000000000002'
    with pytest.raises(upload_post.UploadPostError):asyncio.run(upload_post.canary_target_identity('700000000000001'))
    pages['facebook_page_id']=None
    with pytest.raises(upload_post.UploadPostError):asyncio.run(upload_post.canary_target_identity('700000000000001'))
    pages.pop('facebook_page_id');pages.pop('selected_page_id')
    with pytest.raises(upload_post.UploadPostError):asyncio.run(upload_post.canary_target_identity('700000000000001'))
    pages['selected_page_id']='700000000000001'
    # A friendly name/handle cannot substitute for the opaque native X ID.
    profile['profile']['social_accounts']['x'].pop('username')
    with pytest.raises(upload_post.UploadPostError):asyncio.run(upload_post.canary_target_identity('700000000000001'))
