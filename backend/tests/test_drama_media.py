"""Original synthetic media only; no live grants, uploads or external assets."""
import asyncio
import contextlib
import copy
import hashlib
import json
import subprocess
import threading
from concurrent.futures import ThreadPoolExecutor
from uuid import uuid4

import pytest
from fastapi import HTTPException
from sqlalchemy.orm import Session

from backend.app import drama_media as dm, drama_media_routes as routes, drama_controls as dc, pending_metadata as pm
from backend.app.db import SessionLocal
from backend.app.models import QueueItem,DramaRecord,PublicationEvent
from backend.app.social import upload_post
from backend.tests.test_drama_controls import archive,original_video,inspect,view
from backend.tests.test_drama_canary import candidate,grant,dispatch,dispatch_request,at_slot

REVIEW='I reviewed the exact original and derivative video and every audio channel, and approve this exact lineage artifact without cuts or content changes'


def prepare_request(a):
    proof,_=inspect(a,(0,1))
    return {'operation_id':str(uuid4()),'expected_fingerprint':view(a,(0,1))['fingerprint'],
            'evidence_id':proof['evidence_id'],'recipe':dm.RECIPE}


def prepare(a,req):return a.client.post(f'/api/drama/items/{a.rows[0,1][0]}/derivatives/prepare',json=req)


def test_server_derivative_records_original_artifact_recipe_and_replays_without_overwrite(archive):
    a=archive;req=prepare_request(a);before=view(a,(0,1));original=dict(a.manifest['items'][a.rows[0,1][1]])
    result=prepare(a,req);assert result.status_code==200,result.text
    saved=result.json();doc=saved['document'];path=dm.path(saved['derivative_id'])
    assert doc['original']['sha256']==original['sha256'] and doc['artifact']['sha256']==dm.file_hash(path)
    assert doc['artifact']['media']['audio_profile']=='LC' and doc['original']['media']['frame_count']==doc['artifact']['media']['frame_count']
    assert '-enc_time_base:v' in doc['recipe']['arguments'] and set(doc['recipe']['tools'])=={'ffmpeg','ffprobe'}
    assert doc['similarity']['audio_per_channel'][0]['rms_level_ratio']>0.98
    assert view(a,(0,1))==before and a.manifest['items'][a.rows[0,1][1]]==original
    assert prepare(a,req).json()['replayed'] and dm.file_hash(path)==doc['artifact']['sha256']
    assert a.client.get(saved['artifact_path']).content==path.read_bytes()
    assert not a.transfers and all(not p.exists() for p in a.downloads)
    assert prepare(a,{**req,'recipe':'arbitrary-ffmpeg-command'}).status_code==422


def test_existing_uncommitted_path_is_not_deleted_or_overwritten_on_prepare_failure(archive):
    a=archive;req=prepare_request(a);path=dm.path(req['operation_id']);path.parent.mkdir(parents=True)
    path.write_bytes(b'synthetic earlier validated/uncertain artifact retained')
    before=path.read_bytes();result=prepare(a,req)
    assert result.status_code==409 and path.read_bytes()==before


def test_commit_response_loss_preserves_committed_artifact_and_replays(archive,monkeypatch):
    a=archive;req=prepare_request(a);original=Session.commit;raised=[]
    def lost(s):
        result=original(s)
        if not raised and s.query(DramaRecord).filter_by(operation_id=req['operation_id']).first():
            raised.append(True);raise RuntimeError('Synthetic committed response lost')
        return result
    monkeypatch.setattr(Session,'commit',lost)
    with pytest.raises(RuntimeError,match='response lost'):prepare(a,req)
    path=dm.path(req['operation_id']);sha=dm.file_hash(path)
    result=prepare(a,req);assert result.status_code==200 and result.json()['replayed']
    assert sha==result.json()['document']['artifact']['sha256']==dm.file_hash(path)


def test_same_operation_waiting_before_lease_rechecks_receipt_and_keeps_first_artifact(archive,monkeypatch):
    a=archive;req=prepare_request(a);generated,second,release=threading.Event(),threading.Event(),threading.Event()
    real_create=dm.create_derivative;real_owned=routes.owned;counts=[];guard=threading.Lock()
    def create(*args,**kwargs):
        value=real_create(*args,**kwargs);generated.set();assert release.wait(15);return value
    @contextlib.contextmanager
    def owned(*args,**kwargs):
        with guard:counts.append(1);ordinal=len(counts)
        if ordinal==2:second.set();assert release.wait(15)
        with real_owned(*args,**kwargs):yield
    monkeypatch.setattr(dm,'create_derivative',create);monkeypatch.setattr(routes,'owned',owned)
    with ThreadPoolExecutor(max_workers=2) as pool:
        first=pool.submit(prepare,a,req);assert generated.wait(15)
        pending=pool.submit(prepare,a,req);assert second.wait(15)
        release.set();one=first.result(timeout=20);two=pending.result(timeout=20)
    # A lease conflict is a controlled refusal; replay after it never regenerates.
    assert one.status_code==200 and two.status_code in (200,409)
    replay=prepare(a,req);assert replay.status_code==200 and replay.json()['replayed']
    assert dm.file_hash(dm.path(req['operation_id']))==one.json()['document']['artifact']['sha256']


def derivative_ready(a):
    qid=a.rows[0,1][0]
    rollback=a.client.post('/api/drama/receipts/'+a.canary_req['ready_id']+'/rollback',json={
        'operation_id':str(uuid4()),'expected_plan_fingerprint':a.client.get('/api/drama/plan').json()['fingerprint']})
    assert rollback.status_code==200,rollback.text
    req=prepare_request(a);created=prepare(a,req);assert created.status_code==200,created.text
    saved=created.json();choice={'derivative_id':saved['derivative_id'],'document_sha256':saved['document_sha256'],'decision':REVIEW}
    request={'operation_id':str(uuid4()),'expected_fingerprint':view(a,(0,1))['fingerprint'],
        'evidence_id':req['evidence_id'],'approved_revision':'Original synthetic derivative review',
        'approved_workbook_sha256':pm.digest('Synthetic original-media owner review'),
        'approved_proposal_sha256':pm.digest({k:view(a,(0,1))[k] for k in pm.PUBLIC_FIELDS}),
        'decision':'Accept this exact canonical episode and public copy for guarded release','derivative':choice}
    preview=a.client.post(f'/api/drama/items/{qid}/review-preview',json=request);assert preview.status_code==200,preview.text
    request['acknowledged_fingerprint']=preview.json()['acknowledged_fingerprint']
    review=a.client.post(f'/api/drama/items/{qid}/review',json=request);assert review.status_code==200,review.text
    ready=a.client.post(f'/api/drama/items/{qid}/ready',json={'operation_id':str(uuid4()),
        'expected_fingerprint':view(a,(0,1))['fingerprint'],'evidence_id':req['evidence_id'],'review_id':request['operation_id']})
    assert ready.status_code==200,ready.text
    a.canary_req.update(expected_fingerprint=view(a,(0,1))['fingerprint'],review_id=request['operation_id'],ready_id=ready.json()['ready_id'])
    lineage=preview.json()['document']['derivative_lineage']
    a.canary_req['authorization'].update(authorized_artifact_sha256=lineage['artifact_sha256'],authorized_lineage_sha256=pm.digest(lineage))
    return saved,lineage


def test_explicit_derivative_review_and_authorization_bind_both_hashes_to_real_transport(candidate,monkeypatch):
    a=candidate;saved,lineage=derivative_ready(a);path=dm.path(saved['derivative_id']);transfers=[]
    async def upload(media,**kwargs):
        assert dm.file_hash(media)==lineage['artifact_sha256'];transfers.append(kwargs)
        return 'synthetic-derivative-'+str(len(transfers))
    monkeypatch.setattr(upload_post,'upload_video',upload)
    canary,_=grant(a);at_slot(a);req=dispatch_request(a,canary)
    result=dispatch(a,req);assert result.status_code==200,result.text
    assert len(transfers)==2 and dispatch(a,req).json()['replayed']
    with SessionLocal() as s:
        events=s.query(PublicationEvent).filter_by(queue_id=a.rows[0,1][0],kind='batch_intent').all()
        assert len(events)==1 and events[0].payload['original_media_sha256']==lineage['original_sha256']
        assert events[0].payload['transport_media_sha256']==lineage['artifact_sha256']
        assert events[0].payload['derivative_lineage']==lineage and not dc.complete(s,s.get(QueueItem,a.rows[0,1][0]))
    assert path.exists() and a.client.get('/api/drama/plan').json()['plan'] is None


@pytest.mark.parametrize('damage',['missing_authority','artifact_changed','artifact_missing','recipe_changed','revoked','source_changed'])
def test_derivative_drift_or_missing_specific_authority_cannot_dispatch(candidate,monkeypatch,damage):
    a=candidate;saved,lineage=derivative_ready(a)
    if damage=='missing_authority':a.canary_req['authorization'].pop('authorized_artifact_sha256')
    if damage=='artifact_changed':
        p=dm.path(saved['derivative_id']);p.write_bytes(p.read_bytes()+b'changed')
    if damage=='artifact_missing':dm.path(saved['derivative_id']).unlink()
    if damage=='recipe_changed':monkeypatch.setattr(dm,'RECIPE','unsupported-new-recipe')
    if damage=='revoked':
        assert a.client.post('/api/drama/derivatives/'+saved['derivative_id']+'/revoke',json={
            'operation_id':str(uuid4()),'document_sha256':saved['document_sha256'],'reason':'Synthetic owner withdraws derivative review'}).status_code==200
    if damage=='source_changed':
        with SessionLocal() as s:s.get(QueueItem,a.rows[0,1][0]).drive_link='https://drive.google.com/file/d/changed/view';s.commit()
    result=a.client.post(f'/api/drama/canary/items/{a.rows[0,1][0]}/preview',json=a.canary_req)
    assert result.status_code==409 and not a.transfers
    assert dm.path(saved['derivative_id']).exists() is (damage!='artifact_missing')
    with SessionLocal() as s:assert s.query(DramaRecord).filter_by(operation_id=saved['derivative_id']).first() is not None


@pytest.fixture(scope='module')
def fractional_stereo(tmp_path_factory):
    path=tmp_path_factory.mktemp('fractional-original')/'original.mp4'
    subprocess.run(['ffmpeg','-v','error','-nostdin','-n','-f','lavfi','-i','testsrc2=s=320x180:r=30000/1001:d=3',
        '-f','lavfi','-i','aevalsrc=0.1*sin(2*PI*440*t)|0.08*sin(2*PI*680*t):s=48000:d=3',
        '-vf',"select='not(eq(mod(n,5),2))',setpts=PTS+0.1/TB",'-fps_mode:v','passthrough',
        '-enc_time_base:v','1:30000','-video_track_timescale','30000','-c:v','libx264','-preset','ultrafast',
        '-pix_fmt','yuv420p','-threads:v','2','-c:a','aac','-profile:a','aac_low','-ac','2',str(path)],check=True)
    return path


def test_full_fractional_vfr_pts_offset_and_stereo_content_survive_fixed_recipe(fractional_stereo,tmp_path):
    source=dm.inspect(fractional_stereo);target=tmp_path/'candidate.mp4'
    recipe=dm.encode(fractional_stereo,target,source);candidate=dm.inspect(target)
    assert source['media']['video_start']>0 and len(set(round(b-a,5) for a,b in zip(source['video_pts'],source['video_pts'][1:])))>1
    assert source['video_pts']==candidate['video_pts']
    assert recipe['arguments'][recipe['arguments'].index('-enc_time_base:v')+1]=='1:30000'
    result=dm.compare(fractional_stereo,target,source,candidate)
    assert len(result['audio_per_channel'])==2


@pytest.mark.parametrize('filter',['volume=0.5','pan=stereo|c0=c1|c1=c0','pan=stereo|c0=c0|c1=0*c1'])
def test_real_stereo_gain_swap_and_dropped_channel_are_rejected(fractional_stereo,tmp_path,filter):
    target=tmp_path/'changed.mp4'
    subprocess.run(['ffmpeg','-v','error','-nostdin','-n','-copyts','-i',str(fractional_stereo),'-map','0:v:0','-map','0:a:0',
        '-c:v','copy','-af',filter,'-c:a','aac','-profile:a','aac_low','-ac','2',str(target)],check=True)
    with pytest.raises(HTTPException):dm.compare(fractional_stereo,target,dm.inspect(fractional_stereo),dm.inspect(target))


def test_missing_or_high_efficiency_aac_profile_is_never_assumed_low_complexity(fractional_stereo):
    proof=dm.inspect(fractional_stereo);assert dm.compatible(proof)
    for profile in (None,'HE-AAC','HE-AACv2','unknown'):
        changed=copy.deepcopy(proof);changed['media']['audio_profile']=profile
        assert not dm.compatible(changed)


def test_unsafe_encoder_timebase_or_timeline_trim_does_not_pass_content_comparison(fractional_stereo,tmp_path):
    source=dm.inspect(fractional_stereo)
    for case in ('quantized','trimmed'):
        target=tmp_path/(case+'.mp4')
        args=dm.recipe_arguments(source)
        if case=='quantized':args[args.index('-enc_time_base:v')+1]='1:10'
        else:args[args.index('-i'):args.index('-i')]=['-ss','0.3']
        command=[str(fractional_stereo) if x=='{original}' else str(target) if x=='{artifact}' else x for x in args]
        dm.media.run(command,timeout=60)
        with pytest.raises(HTTPException):dm.compare(fractional_stereo,target,source,dm.inspect(target))


def test_explicit_derivative_never_weakens_final_original_hash_guard(candidate,monkeypatch):
    from backend.app import drive_sync
    a=candidate;saved,lineage=derivative_ready(a);canary,_=grant(a);at_slot(a)
    original=drive_sync.download_drive_file
    def changed(*args,**kwargs):
        import struct
        path=original(*args,**kwargs);body=b'changed original canonical source'
        path.write_bytes(path.read_bytes()+struct.pack('>I4s',8+len(body),b'free')+body)
        return path
    monkeypatch.setattr(drive_sync,'download_drive_file',changed)
    result=dispatch(a,dispatch_request(a,canary))
    assert result.status_code==409 and not a.transfers
    assert dm.file_hash(dm.path(saved['derivative_id']))==lineage['artifact_sha256']


def test_changed_derivative_between_destinations_holds_unsent_x(candidate,monkeypatch):
    a=candidate;saved,lineage=derivative_ready(a);canary,_=grant(a);at_slot(a);calls=[]
    async def upload(path,**kwargs):
        calls.append(kwargs)
        if kwargs['platforms']==['facebook']:path.write_bytes(path.read_bytes()+b'independent artifact mutation')
        return 'synthetic-derivative-fb-request'
    monkeypatch.setattr(upload_post,'upload_video',upload)
    assert dispatch(a,dispatch_request(a,canary)).status_code==200
    assert len(calls)==1 and calls[0]['platforms']==['facebook']


def test_preparation_receipt_and_exact_artifact_survive_independent_interpreter(archive):
    import sys
    a=archive;req=prepare_request(a);saved=prepare(a,req).json()
    script='''import sys,json
from backend.app.e2e_safety import install_network_guard
install_network_guard()
from backend.app.db import SessionLocal
from backend.app.drama_media import path,file_hash
from backend.app.models import DramaRecord
with SessionLocal() as s:
 row=s.query(DramaRecord).filter_by(operation_id=sys.argv[1]).one()
 print(json.dumps({'sha':file_hash(path(row.operation_id)),'document_sha':row.data['response']['document_sha256']}))
'''
    result=json.loads(subprocess.check_output([sys.executable,'-c',script,req['operation_id']],text=True))
    assert result=={'sha':saved['document']['artifact']['sha256'],'document_sha':saved['document_sha256']}


def test_same_timeline_but_changed_visual_content_is_held(fractional_stereo,tmp_path):
    original=dm.inspect(fractional_stereo);target=tmp_path/'changed-visual.mp4'
    args=dm.recipe_arguments(original)
    args[-1:-1]=['-vf','lutrgb=r=0:g=0:b=0']
    dm.media.run([str(fractional_stereo) if x=='{original}' else str(target) if x=='{artifact}' else x for x in args],timeout=60)
    with pytest.raises(HTTPException):dm.compare(fractional_stereo,target,original,dm.inspect(target))


def test_original_v1_hashes_and_consumed_receipts_survive_lineage_upgrade_without_extra_authority(candidate):
    from backend.app import drama_canary_routes as cr,drama_routes as dr,drama_canary as canary
    a=candidate;qid=a.rows[0,1][0]
    preview=a.client.post(f'/api/drama/canary/items/{qid}/preview',json=a.canary_req)
    assert preview.status_code==200,preview.text
    old_doc=preview.json()['document'];old_doc.pop('transport')
    for key in ('authorized_artifact_sha256','authorized_lineage_sha256'):old_doc['authorization'].pop(key,None)
    body={**copy.deepcopy(a.canary_req),'acknowledged_fingerprint':pm.digest(old_doc)}
    model=cr.Grant.model_validate(body)
    old_request=model.model_dump(exclude={'operation_id'})
    for key in ('authorized_artifact_sha256','authorized_lineage_sha256'):old_request['authorization'].pop(key,None)
    old_hash=pm.digest({'version':canary.VERSION,'kind':'grant','queue_id':qid,'request':old_request})
    assert cr.hashed('grant',model,qid)==old_hash
    with SessionLocal() as s:
        dc.record(s,body['operation_id'],old_hash,'canary_grant',{'grant_id':body['operation_id'],
            'grant_sha256':pm.digest(old_doc),'state':'granted_not_dispatched'},item=s.get(QueueItem,qid),
            canary_scope=canary.SCOPE,document=old_doc);s.commit()
    assert a.client.post(f'/api/drama/canary/items/{qid}/grants',json=body).json()['replayed']
    at_slot(a)
    req={'operation_id':str(uuid4()),'expected_fingerprint':body['expected_fingerprint'],
        'grant_id':body['operation_id'],'grant_sha256':pm.digest(old_doc)}
    refused=dispatch(a,req);assert refused.status_code==409 and 'legacy' in refused.text
    hashed=cr.hashed('dispatch',cr.Dispatch.model_validate(req),qid)
    with SessionLocal() as s:
        dc.record(s,req['operation_id'],hashed,'canary_dispatch',{'dispatch_id':req['operation_id'],
            'state':'durable_intent_claimed','resend_authorized':False},item=s.get(QueueItem,qid),
            canary_scope=canary.SCOPE,grant_id=body['operation_id'],document=old_doc);s.commit()
    reads=len(a.native_reads)
    assert dispatch(a,req).json()['replayed'] and len(a.native_reads)==reads and not a.transfers
    # Old review request hashes also excluded the not-yet-existing derivative field.
    review=dr.ReviewPreview.model_validate({'operation_id':str(uuid4()),'expected_fingerprint':body['expected_fingerprint'],
        'evidence_id':body['evidence_id'],'approved_revision':'Original synthetic v1',
        'approved_workbook_sha256':'1'*64,'approved_proposal_sha256':'2'*64,
        'decision':'Accept this exact canonical episode and public copy for guarded release'})
    old=review.model_dump(exclude={'operation_id'});old.pop('derivative')
    assert dr.request_hash('review',review,qid)==pm.digest({'version':dc.VERSION,'kind':'review','queue_id':qid,'request':old})


def test_registered_artifact_is_owned_through_retention_and_cache_cleanup_without_queue_path_adoption(archive,monkeypatch):
    import datetime as dt
    from backend.app import cleanup,file_refs
    from backend.app.models import Clip,Status
    a=archive;req=prepare_request(a);saved=prepare(a,req).json();path=dm.path(saved['derivative_id']);sha=dm.file_hash(path)
    monkeypatch.setattr(cleanup.settings,'retention_days',1)
    with SessionLocal() as s:
        # A legacy/rescan row must not make the retained artifact disposable.
        clip=Clip(platform='synthetic',source_url='https://example.invalid/original',title='Synthetic stale reference',
            status=Status.done,file_path=str(path),created_at=dt.datetime.now(dt.timezone.utc)-dt.timedelta(days=10))
        s.add(clip);s.commit();cid=clip.id
        assert file_refs.protected(s,path) and file_refs.protected(s,path.parent)
        result=cleanup.run_full_sweep(s)
        assert result['retention']['clips']==0 and s.get(Clip,cid) is not None
        with pytest.raises(HTTPException):cleanup.delete_media(s,Clip,cid)
    assert path.exists() and dm.file_hash(path)==sha


@pytest.mark.parametrize('damage',['changed_bytes','symlink'])
def test_derivative_route_rechecks_immutable_artifact_at_actual_asgi_send(archive,tmp_path,damage):
    from backend.tests.test_media_stream_claims import run,claims
    a=archive;req=prepare_request(a);saved=prepare(a,req).json();path=dm.path(saved['derivative_id'])
    with SessionLocal() as s:response=routes.artifact(saved['derivative_id'],s)
    before=set(claims())
    if damage=='changed_bytes':path.write_bytes(b'changed after response construction')
    else:
        outside=tmp_path/'unapproved.mp4';outside.write_bytes(b'unapproved external bytes')
        path.unlink();path.symlink_to(outside)
    async def send(message):pytest.fail('No bytes or headers may be served for a changed reviewed artifact')
    with pytest.raises(HTTPException) as exc:run(response,send)
    assert exc.value.status_code==409 and set(claims())==before


def test_derivative_download_holds_shared_file_claim_until_the_last_body_byte(archive):
    from backend.app import file_use
    from backend.tests.test_media_stream_claims import run,claims,outside,replace
    a=archive;req=prepare_request(a);saved=prepare(a,req).json();path=dm.path(saved['derivative_id']);body=path.read_bytes()
    with SessionLocal() as s:response=routes.artifact(saved['derivative_id'],s)
    before=set(claims());received=[]
    async def send(message):
        if message['type']=='http.response.body':
            received.append(message['body'])
            with pytest.raises(file_use.FileBusy):outside(lambda:replace(path))
    run(response,send)
    assert b''.join(received)==body and set(claims())==before and path.read_bytes()==body


def test_exact_old_publisher_rejects_new_derivative_review_instead_of_sending_original(candidate):
    import ast
    a=candidate;derivative_ready(a)
    source=subprocess.check_output(['git','show','77ac5c93acfdd75e79adc68b6caf1620ebf4ad28:backend/app/drama_controls.py'],text=True)
    function=next(n for n in ast.parse(source).body if isinstance(n,ast.FunctionDef) and n.name=='payload')
    namespace={'__package__':'backend.app','readiness':dc.readiness,'latest':dc.latest,'fail':dc.fail}
    exec(compile(ast.Module(body=[function],type_ignores=[]),'exact-old-publisher-payload','exec'),namespace)
    with SessionLocal() as s:
        item=s.get(QueueItem,a.rows[0,1][0])
        assert dc.payload(s,item)['facebook_media_type']=='VIDEO'
        with pytest.raises(HTTPException):namespace['payload'](s,item)
    assert not a.transfers
