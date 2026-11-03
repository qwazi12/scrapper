"""A generated diagnostic, independent from all canonical episode/queue history."""
import copy
import datetime as dt
import itertools
import json
import shutil
import subprocess
import sys
import threading
from concurrent.futures import ThreadPoolExecutor
from types import SimpleNamespace
from uuid import uuid4

from fastapi.testclient import TestClient
import pytest
from sqlalchemy import text
from sqlalchemy.exc import DBAPIError

from backend.app import drama_calibration as cal,drama_canary,drama_controls as dc,drama_media as dm,pending_metadata as pm
from backend.app.db import SessionLocal
from backend.app.models import QueueItem,DramaRecord
from backend.app.social import upload_post

_SCENARIOS=itertools.count()
DECISION='I authorize ONE public 120-second original calibration VIDEO to this exact Facebook Page and future slot, and confirm rights for this exact asset and copy'


@pytest.fixture(scope='module')
def generated(tmp_path_factory):
    path=tmp_path_factory.mktemp('original-calibration')/'generated.mp4'
    recipe=cal.generate(path);observed=cal.validate_asset(path)
    return path,recipe,observed


@pytest.fixture
def diagnostic(session,generated,monkeypatch):
    from backend.app.main import app
    path,recipe,observed=generated
    # Full real recipe/decode/marker validation above; repeated authority/race
    # cases reuse those exact immutable synthetic bytes, not private test media.
    def generate(destination):shutil.copyfile(path,destination);return copy.deepcopy(recipe)
    def validated(candidate):
        assert dm.file_hash(candidate)==observed['proof']['sha256']
        return copy.deepcopy(observed)
    monkeypatch.setattr(cal,'generate',generate);monkeypatch.setattr(cal,'validate_asset',validated)
    monkeypatch.setattr(cal,'SCOPE','synthetic-calibration-'+uuid4().hex)
    clock=[dt.datetime(2090,1,1,13,tzinfo=dc.UTC)+dt.timedelta(days=32*next(_SCENARIOS))]
    monkeypatch.setattr(dc,'now',lambda s:clock[0])
    target={'profile':'Dramarama','accounts':['Dramarama:facebook'],'facebook_page_id':'700000000000001',
        'facebook_connection_id':'synthetic-native-fb-owner','facebook_page_name':'Original Synthetic Page',
        'facebook_pinned_page_id':'700000000000001','profile_pinned_page_id':'700000000000001',
        'pin_evidence':'matching native profile and Page/pin GET responses'}
    reads=[];sends=[]
    async def native(page):reads.append(page);return copy.deepcopy(target)
    async def upload(path,**kw):
        assert dm.file_hash(path)==observed['proof']['sha256'];sends.append(kw);return 'synthetic-original-calibration-request'
    monkeypatch.setattr(upload_post,'facebook_page_identity',native);monkeypatch.setattr(upload_post,'upload_video',upload)
    row=QueueItem(title='Other brand exact protected copy',pipeline='Movie Clips',status='ready',
        accounts=['Other:facebook'],scheduled_at=clock[0],tags='#Original',source='Synthetic source')
    session.add(row);session.commit()
    with SessionLocal() as s:before=pm.fingerprint(s,s.get(QueueItem,row.id))
    a=SimpleNamespace(client=TestClient(app,headers={'x-access-token':'test-token'}),clock=clock,
        target=target,reads=reads,sends=sends,observed=observed,other_id=row.id,before=before)
    result=a.client.post('/api/drama/calibration/assets/prepare',json={'operation_id':str(uuid4()),'recipe':cal.RECIPE})
    assert result.status_code==200,result.text
    a.asset=result.json()
    return a


def proposal(a):
    payload=upload_post.prepare_video_fields('Dramarama',['facebook'],'Original synthetic120s diagnostic',
        'Entirely generated original colors and tones; not an episode.',
        {'facebook_media_type':'VIDEO','facebook_page_id':a.target['facebook_page_id']})
    return {'operation_id':str(uuid4()),'asset_id':a.asset['asset_id'],
        'asset_document_sha256':a.asset['document_sha256'],'facebook_page_id':a.target['facebook_page_id'],
        'title':payload['facebook_title'],'description':payload['facebook_description'],
        'slot':(a.clock[0]+dt.timedelta(hours=2)).isoformat(),
        'authorization':{'authorizer':'Synthetic generated-media owner','authorization_reference':'Synthetic fixture approval only',
            'rights_reference':'Wholly generated synthetic color/tone fixture; no external material',
            'authorized_asset_sha256':a.observed['proof']['sha256'],'authorized_payload_sha256':pm.digest(payload),'decision':DECISION}}


def grant(a,req=None):
    req=req or proposal(a);preview=a.client.post('/api/drama/calibration/preview',json=req)
    assert preview.status_code==200,preview.text
    req['acknowledged_fingerprint']=preview.json()['acknowledged_fingerprint']
    response=a.client.post('/api/drama/calibration/grants',json=req);assert response.status_code==200,response.text
    return response.json(),req


def dispatch(a,saved,req):return a.client.post('/api/drama/calibration/grants/'+saved['grant_id']+'/dispatch',json=req)


def armed(a):
    saved,request=grant(a);a.clock[0]=dt.datetime.fromisoformat(request['slot'])
    req={'operation_id':str(uuid4()),'grant_sha256':saved['grant_sha256']}
    return saved,request,req


def test_real_generated120s_markers_tail_audio_and_profile_without_public_authority(generated):
    path,recipe,observed=generated
    assert observed['proof']['duration']==120 and observed['proof']['media']['frame_count']==3600
    assert observed['proof']['media']['audio_profile']=='LC' and observed['colors']['105'][0]>250
    assert observed['late_audio_pulse_verified'] and recipe['id']==cal.RECIPE
    assert 'http' not in ' '.join(recipe['arguments']) and path.exists()


def test_preview_requires_no_fabricated_authority_but_grant_requires_specific_exact_asset_copy(diagnostic):
    a=diagnostic;req=proposal(a);req['authorization']=None
    p=a.client.post('/api/drama/calibration/preview',json=req);assert p.status_code==200
    req['acknowledged_fingerprint']=p.json()['acknowledged_fingerprint']
    assert a.client.post('/api/drama/calibration/grants',json=req).status_code==422
    assert a.client.get('/api/drama/calibration/contract').json()['grant_count']==0 and not a.sends
    assert a.client.get('/api/drama/calibration/assets/'+a.asset['asset_id']+'/artifact').content==dm.path(a.asset['asset_id']).read_bytes()
    for field in ('local_path','url','queue_id','episode','accounts','checks_passed'):
        damaged={**proposal(a),field:'client input'}
        assert a.client.post('/api/drama/calibration/preview',json=damaged).status_code==422


@pytest.mark.parametrize('damage',['copy_hash','asset_hash','blank_rights','broad_approval','slot','pin','asset_bytes'])
def test_missing_or_changed_exact_calibration_evidence_never_creates_public_intent(diagnostic,damage):
    a=diagnostic;req=proposal(a)
    if damage=='copy_hash':req['authorization']['authorized_payload_sha256']='0'*64
    if damage=='asset_hash':req['authorization']['authorized_asset_sha256']='0'*64
    if damage=='blank_rights':req['authorization']['rights_reference']=' '
    if damage=='broad_approval':req['authorization']['decision']='Deploy approved'
    if damage=='slot':req['slot']=a.clock[0].isoformat()
    if damage=='pin':a.target['facebook_pinned_page_id']='700000000000002'
    if damage=='asset_bytes':
        p=dm.path(a.asset['asset_id']);p.write_bytes(p.read_bytes()+b'changed')
    r=a.client.post('/api/drama/calibration/preview',json=req)
    assert r.status_code in (409,422) and not a.sends


def test_exact_single_facebook_request_and_durable_replay_do_not_touch_any_queue_or_episode(diagnostic):
    a=diagnostic;saved,request,req=armed(a)
    before=a.client.get('/api/drama/canary/contract').json()
    result=dispatch(a,saved,req);assert result.status_code==200,result.text
    assert len(a.sends)==1 and a.sends[0]['platforms']==['facebook']
    assert a.sends[0]['platform_overrides']['facebook_media_type']=='VIDEO'
    assert a.sends[0]['platform_overrides']['facebook_page_id']==a.target['facebook_page_id']
    reads=len(a.reads)
    assert dispatch(a,saved,req).json()['replayed'] and len(a.sends)==1 and len(a.reads)==reads
    assert dispatch(a,saved,{**req,'operation_id':str(uuid4())}).status_code==409
    status=a.client.get('/api/drama/calibration/grants/'+saved['grant_id']).json()
    assert status['results'][0]['response']['request_id']=='synthetic-original-calibration-request'
    assert status['dispatch']['original_sha256']==status['dispatch']['artifact_sha256']==a.observed['proof']['sha256']
    with SessionLocal() as s:
        from backend.app import file_refs
        assert file_refs.protected(s,dm.path(a.asset['asset_id']))
        assert s.query(QueueItem).count()==1 and pm.fingerprint(s,s.get(QueueItem,a.other_id))==a.before
    assert a.client.get('/api/drama/canary/contract').json()==before
    assert a.client.get('/api/drama/plan').json()['plan'] is None


def test_observe_reads_only_original_request_and_retains_native_ids_without_fidelity_claim(diagnostic,monkeypatch):
    a=diagnostic;saved,request,req=armed(a);assert dispatch(a,saved,req).status_code==200
    calls=[]
    async def status(rid):
        calls.append(rid)
        return {'status':'completed','results':[{'platform':'facebook','success':True,
            'post_id':'synthetic-native-video-id','post_url':'https://example.invalid/synthetic-original-calibration'}]}
    monkeypatch.setattr(upload_post,'get_status',status)
    body={'operation_id':str(uuid4())};url='/api/drama/calibration/grants/'+saved['grant_id']+'/observe'
    result=a.client.post(url,json=body)
    assert result.status_code==200 and result.json()['fidelity_verified'] is False
    assert result.json()['observed_status']['results'][0]['post_id']=='synthetic-native-video-id'
    assert a.client.post(url,json=body).json()['replayed'] and len(calls)==1 and len(a.sends)==1
    status=a.client.get('/api/drama/calibration/grants/'+saved['grant_id']).json()
    assert status['observations'][0]['original_sha256']==a.observed['proof']['sha256']


def test_unknown_diagnostic_recipe_on_source_rollback_is_held(diagnostic,monkeypatch):
    a=diagnostic;monkeypatch.setattr(cal,'RECIPE','unsupported-recipe-after-source-rollback')
    assert a.client.post('/api/drama/calibration/preview',json=proposal(a)).status_code==409
    assert not a.sends and dm.path(a.asset['asset_id']).exists()


def test_late_accepted_receipt_survives_publisher_generation_loss(diagnostic,monkeypatch):
    from backend.app import worker_leases as leases
    from backend.app.file_use import suspend
    from backend.app.models import WorkerLease
    a=diagnostic;saved,request,req=armed(a);calls=[]
    async def late(path,**kwargs):
        calls.append(kwargs)
        with leases.bind(None),suspend(),SessionLocal() as s:
            lease=s.get(WorkerLease,'drama:calibration')
            lease.generation+=1;lease.owner=None
            lease.expires_at=leases.clock(s)-dt.timedelta(seconds=1);s.commit()
        return 'synthetic-late-accepted-after-generation-loss'
    monkeypatch.setattr(upload_post,'upload_video',late)
    result=dispatch(a,saved,req);assert result.status_code in (200,409),result.text
    status=a.client.get('/api/drama/calibration/grants/'+saved['grant_id']).json()
    assert status['results'][0]['response']['request_id']=='synthetic-late-accepted-after-generation-loss'
    assert status['results'][0]['original_sha256']==a.observed['proof']['sha256']
    assert dispatch(a,saved,req).json()['replayed'] and len(calls)==1


@pytest.mark.parametrize('damage',['changed_bytes','symlink'])
def test_calibration_route_rechecks_hash_when_actual_response_starts(diagnostic,tmp_path,damage):
    from fastapi import HTTPException
    from backend.tests.test_media_stream_claims import run,claims
    a=diagnostic;path=dm.path(a.asset['asset_id'])
    with SessionLocal() as s:response=cal.review_artifact(a.asset['asset_id'],s)
    before=set(claims())
    if damage=='changed_bytes':path.write_bytes(b'changed generated original after response construction')
    else:
        other=tmp_path/'unapproved.mp4';other.write_bytes(b'unapproved bytes');path.unlink();path.symlink_to(other)
    async def send(message):pytest.fail('No changed generated source may be served')
    with pytest.raises(HTTPException) as exc:run(response,send)
    assert exc.value.status_code==409 and set(claims())==before


def test_concurrent_claim_and_revocation_inflight_do_not_reset_or_repeat_accepted_diagnostic(diagnostic,monkeypatch):
    a=diagnostic;saved,request,req=armed(a);entered,finish=threading.Event(),threading.Event()
    original=upload_post.upload_video
    async def held(path,**kw):entered.set();assert finish.wait(15);return await original(path,**kw)
    monkeypatch.setattr(upload_post,'upload_video',held)
    with ThreadPoolExecutor(max_workers=1) as pool:
        first=pool.submit(dispatch,a,saved,req);assert entered.wait(15)
        assert dispatch(a,saved,req).json()['replayed']
        changed=dispatch(a,saved,{**req,'operation_id':str(uuid4())});assert changed.status_code==409
        revoke=a.client.post('/api/drama/calibration/grants/'+saved['grant_id']+'/revoke',json={
            'operation_id':str(uuid4()),'grant_sha256':saved['grant_sha256'],'reason':'Synthetic owner withdraws remaining authority'})
        assert revoke.status_code==200
        finish.set();assert first.result(timeout=20).status_code==200
    assert len(a.sends)==1
    renewal=proposal(a);renewal['slot']=(a.clock[0]+dt.timedelta(days=1)).isoformat()
    assert a.client.post('/api/drama/calibration/preview',json=renewal).status_code==409
    assert a.client.get('/api/drama/calibration/contract').json()['consumed_count']==1


def test_unknown_transport_replays_original_request_and_survives_new_interpreter(diagnostic,monkeypatch):
    a=diagnostic;saved,request,req=armed(a);calls=[]
    async def lost(*args,**kwargs):calls.append(kwargs);raise TimeoutError('Synthetic response lost after possible acceptance')
    monkeypatch.setattr(upload_post,'upload_video',lost)
    assert dispatch(a,saved,req).status_code==200 and len(calls)==1
    status=a.client.get('/api/drama/calibration/grants/'+saved['grant_id']).json()
    assert status['results'][0]['response']['state']=='unknown' and status['results'][0]['response']['transport_dispatched']=='unknown'
    digest=cal.digest('calibration_dispatch',cal.Dispatch.model_validate(req),saved['grant_id'])
    script='''import sys,json
from backend.app.e2e_safety import install_network_guard
install_network_guard()
from backend.app.db import SessionLocal
from backend.app.drama_controls import replay
with SessionLocal() as s:print(json.dumps(replay(s,sys.argv[1],sys.argv[2])))
'''
    replay=json.loads(subprocess.check_output([sys.executable,'-c',script,req['operation_id'],digest],text=True))
    assert replay['replayed'] and replay['resend_authorized'] is False
    assert dispatch(a,saved,req).json()['replayed'] and len(calls)==1
    # Existing SQL append-only protection survives source/process rollback.
    with SessionLocal() as s,pytest.raises(DBAPIError):
        s.execute(text('DELETE FROM drama_records WHERE operation_id=:id'),{'id':req['operation_id']});s.commit()


def test_expired_or_revoked_before_dispatch_stays_unsent(diagnostic):
    a=diagnostic;saved,request,req=armed(a);a.clock[0]+=dt.timedelta(minutes=5)
    assert dispatch(a,saved,req).status_code==409 and not a.sends
    revoke=a.client.post('/api/drama/calibration/grants/'+saved['grant_id']+'/revoke',json={
        'operation_id':str(uuid4()),'grant_sha256':saved['grant_sha256'],'reason':'Synthetic revoke expired unconsumed permission'})
    assert revoke.status_code==200 and a.client.get('/api/drama/calibration/contract').json()['consumed_count']==0


def test_independent_process_claim_keeps_original_receipt_and_blocks_another_process(diagnostic,tmp_path):
    import time
    a=diagnostic;saved,request,req=armed(a);started=tmp_path/'started';finish=tmp_path/'finish';sends=tmp_path/'sends'
    script='''import datetime as dt,json,sys,time
from pathlib import Path
from backend.app.e2e_safety import install_network_guard
install_network_guard()
from backend.app import drama_calibration as cal,drama_controls as dc
from backend.app.db import SessionLocal
from backend.app.social import upload_post
cal.SCOPE=sys.argv[1];dc.now=lambda s:dt.datetime.fromisoformat(sys.argv[2])
async def identity(page):return json.loads(sys.argv[3])
async def upload(path,**kwargs):
 with Path(sys.argv[6]).open('a') as out:out.write(kwargs['idempotency_key']+'\\n')
 Path(sys.argv[4]).touch();deadline=time.monotonic()+25
 while not Path(sys.argv[5]).exists():
  assert time.monotonic()<deadline;time.sleep(.02)
 return 'synthetic-independent-process-native-request'
upload_post.facebook_page_identity=identity;upload_post.upload_video=upload
with SessionLocal() as s:
 print(json.dumps(cal.dispatch(sys.argv[7],cal.Dispatch.model_validate(json.loads(sys.argv[8])),s)))
'''
    process=subprocess.Popen([sys.executable,'-c',script,cal.SCOPE,a.clock[0].isoformat(),json.dumps(a.target),
        str(started),str(finish),str(sends),saved['grant_id'],json.dumps(req)],stdout=subprocess.PIPE,stderr=subprocess.PIPE,text=True)
    try:
        deadline=time.monotonic()+60
        while not started.exists() and process.poll() is None and time.monotonic()<deadline:time.sleep(.05)
        assert started.exists(),'Independent process did not reach the synthetic provider boundary'
        assert dispatch(a,saved,req).json()['replayed']
        assert dispatch(a,saved,{**req,'operation_id':str(uuid4())}).status_code==409
        finish.touch();stdout,stderr=process.communicate(timeout=30)
        assert process.returncode==0,stderr
        assert json.loads(stdout)['resend_authorized'] is False
    finally:
        finish.touch()
        if process.poll() is None:process.terminate();process.communicate(timeout=10)
    assert len(sends.read_text().splitlines())==1 and not a.sends
    status=a.client.get('/api/drama/calibration/grants/'+saved['grant_id']).json()
    assert status['results'][0]['response']['request_id']=='synthetic-independent-process-native-request'
