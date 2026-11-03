"""Synthetic local media/provider boundaries; never production IDs or approvals."""
import asyncio
import copy
import datetime as dt
import json
import sqlite3
import subprocess
from concurrent.futures import ThreadPoolExecutor
from types import SimpleNamespace
from uuid import uuid4

import httpx
import pytest
from fastapi import HTTPException
from fastapi.testclient import TestClient
from sqlalchemy import text

from backend.app import ick_readiness as ir,pending_metadata as pm,publication_journal as journal,worker_leases as leases
from backend.app.db import SessionLocal,engine
from backend.app.models import QueueItem,MetadataMutationReceipt,PublicationEvent,AppSetting
from backend.app.social import queue_manager as qm,queue_payloads as qp,upload_post
from backend.tests.test_platform_payloads import multipart


@pytest.fixture(scope='module')
def original(tmp_path_factory):
    p=tmp_path_factory.mktemp('ick-original')/'original.mp4'
    subprocess.run(['ffmpeg','-v','error','-f','lavfi','-i','color=c=blue:s=320x180:r=12:d=1',
        '-f','lavfi','-i','sine=f=880:d=1','-c:v','libx264','-preset','ultrafast','-pix_fmt','yuv420p',
        '-c:a','aac','-profile:a','aac_low','-ar','48000','-ac','2','-shortest',str(p)],check=True)
    return p


@pytest.fixture
def candidate(session,monkeypatch,tmp_path,original):
    from backend.app.main import app
    prior_config=copy.deepcopy(qm.schedule_config())
    prior_setting=session.get(AppSetting,'schedule')
    prior_value=copy.deepcopy(prior_setting.value) if prior_setting else None
    prior_exists=prior_setting is not None
    # Retained reservations survive fixture queue deletion. Advance the synthetic
    # database clock past those exact old slots rather than deleting history.
    with SessionLocal() as s:
        past=[dt.datetime.fromisoformat(r.data['document']['slot']) for r in ir.reservations(s)]
    clock=[max([dt.datetime(2040,1,2,7,tzinfo=dt.timezone.utc),*[x+dt.timedelta(days=1) for x in past]])]
    monkeypatch.setattr(leases,'clock',lambda s:clock[0])
    monkeypatch.setattr(qm,'wake',lambda:pytest.fail('Guarded readiness must not wake the poster'))
    qm.save_schedule(session,{'timezone':'America/New_York','start_hour':10,'end_hour':20,'interval_hours':2,'posts_per_day':2,'pipelines':{},'accounts':{}})
    media=tmp_path/'source.mp4';media.write_bytes(original.read_bytes())
    item=QueueItem(pipeline=qm.ICK_ROOM,status='review',title='Original exact title',description='Reviewed words. #TheIckRoom',
        tags='#tonightsconversation #theickroom #Extra',video_path=str(media),media_kind='video',accounts=['TheIckRoom:*'],research={'keep':'private'},notes='Keep notes')
    session.add(item);session.commit();qid=item.id
    client=TestClient(app,headers={'x-access-token':'test-token'})
    accounts=[{'id':'TheIckRoom:'+n,'profile':'TheIckRoom','network':n,'username':'native-'+n} for n in ('facebook','x')]
    identity={'profile':'TheIckRoom','accounts':['TheIckRoom:facebook'],'facebook_page_id':'700000000000001',
        'facebook_pinned_page_id':'700000000000001','profile_pinned_page_id':'700000000000001','facebook_connection_id':'original-owner'}
    async def connected():return copy.deepcopy(accounts)
    async def native(page,profile_name='Dramarama'):
        assert profile_name=='TheIckRoom' and page==identity['facebook_page_id']
        return copy.deepcopy(identity),{'facebook':{'username':'original-owner'},'x':{'username':'native-x','handle':'original_handle'}}
    monkeypatch.setattr(upload_post,'list_accounts',connected);monkeypatch.setattr(upload_post,'_native_page_identity',native)
    values=client.get(f'/api/pending-metadata/{qid}').json()
    context={'revision':'Synthetic owner-reviewed copy','workbook_sha256':'1'*64,'proposal_sha256':'2'*64,
        'applied_public_copy_sha256':pm.digest({k:values[k] for k in pm.PUBLIC_FIELDS})}
    approved=client.patch(f'/api/pending-metadata/{qid}',json={'operation_id':str(uuid4()),'expected_fingerprint':values['fingerprint'],
        'title':values['title'],'approval_context':context});assert approved.status_code==200,approved.text
    body={'operation_id':str(uuid4()),'expected_fingerprint':approved.json()['fingerprint'],'approval_receipt_id':approved.json()['receipt_id']}
    inspected=client.post(f'/api/ick-readiness/items/{qid}/inspect',json=body);assert inspected.status_code==200,inspected.text
    request={**body,'operation_id':str(uuid4()),'media_receipt_id':inspected.json()['media_receipt_id'],
        'destination_ids':['TheIckRoom:facebook','TheIckRoom:x'],'facebook_page_id':identity['facebook_page_id']}
    yield SimpleNamespace(qid=qid,client=client,body=request,clock=clock,media=media,identity=identity,session=session)
    session.rollback()
    with SessionLocal() as cleanup:
        setting=cleanup.get(AppSetting,'schedule')
        if prior_exists:
            setting.value=prior_value
        elif setting is not None:
            cleanup.delete(setting)
        cleanup.commit()
    qm._schedule_cfg=prior_config


def snap(qid):
    with SessionLocal() as s:return {c.key:getattr(s.get(QueueItem,qid),c.key) for c in QueueItem.__table__.columns}


def prepared(a):
    r=a.client.post(f'/api/ick-readiness/items/{a.qid}/preview',json=a.body);assert r.status_code==200,r.text
    return {**a.body,'acknowledged_preview_sha256':r.json()['preview_sha256']},r.json()['document']


def release(a):
    body,doc=prepared(a);r=a.client.post(f'/api/ick-readiness/items/{a.qid}/ready',json=body);assert r.status_code==200,r.text
    return body,doc,r.json()


def test_future_ready_exact_retry_no_wake_no_protected_edits_and_legacy_slots(candidate):
    a=candidate
    with SessionLocal() as s:
        # Existing25Ready and unrelated brand assignments are not approval inputs.
        existing=[]
        for i,slot in enumerate(qm.slots_after(a.clock[0],25,pipeline=qm.ICK_ROOM)):
            row=QueueItem(pipeline=qm.ICK_ROOM,status='ready',title='Preexisting',video_path=str(a.media),accounts=['TheIckRoom:facebook'],scheduled_at=slot,position=i)
            s.add(row);existing.append(row)
        other=QueueItem(pipeline='Other brand',status='ready',title='Other',video_path=str(a.media),accounts=['Other:facebook'],scheduled_at=a.clock[0]+dt.timedelta(hours=2),pinned_at=a.clock[0]+dt.timedelta(hours=2));s.add(other);s.commit()
        ids=[r.id for r in existing]+[other.id]
    preserved={i:snap(i) for i in ids};before=snap(a.qid);body,doc,response=release(a);after=snap(a.qid)
    assert dt.datetime.fromisoformat(doc['slot'])>max(qm._aware(preserved[i]['scheduled_at']) for i in ids[:-1])
    assert {k:v for k,v in before.items() if k not in ['status','scheduled_at']}=={k:v for k,v in after.items() if k not in ['status','scheduled_at']}
    assert preserved=={i:snap(i) for i in ids}
    assert a.client.post(f'/api/ick-readiness/items/{a.qid}/ready',json=body).json()['replayed']
    for _ in range(2):
        with SessionLocal() as s:qm.plan_schedule(s,a.clock[0])
    assert snap(a.qid)['scheduled_at']==after['scheduled_at']
    with SessionLocal() as s:
        slots=[qm._aware(r.scheduled_at) for r in s.query(QueueItem).filter_by(pipeline=qm.ICK_ROOM,status='ready')]
    assert len(slots)==len(set(slots)) and qm._aware(after['scheduled_at']) not in [qm._aware(snap(i)['scheduled_at']) for i in ids[:-1]]
    bad={**body,'expected_fingerprint':'0'*64};assert a.client.post(f'/api/ick-readiness/items/{a.qid}/ready',json=bad).status_code==409


@pytest.mark.parametrize('damage',['title','description','tags','research','accounts','position','schedule','media','approval','native','history','deleted'])
def test_stale_copy_media_approval_schedule_identity_or_history_cannot_turn_ready(candidate,damage):
    a=candidate;body,doc=prepared(a);before=snap(a.qid)
    if damage=='media':a.media.write_bytes(a.media.read_bytes()+b'changed')
    elif damage=='native':a.identity['facebook_connection_id']='different-owner'
    else:
        with SessionLocal() as s:
            item=s.get(QueueItem,a.qid)
            if damage in ['title','description','tags']:setattr(item,damage,getattr(item,damage)+' changed')
            if damage=='research':item.research={'different':'approval context'}
            if damage=='accounts':item.accounts=['TheIckRoom:facebook']
            if damage=='position':item.position=999
            if damage=='schedule':item.scheduled_at=a.clock[0]
            if damage=='history':journal.record(s,item,{'state':'accepted','profile':'TheIckRoom','platforms':['facebook'],'request_id':'old-partial','client_request_id':'synthetic-old-attempt'},'transport_result')
            if damage=='approval':
                old=s.get(MetadataMutationReceipt,a.body['approval_receipt_id']);s.add(MetadataMutationReceipt(operation_id=str(uuid4()),queue_id=item.id,queue_identity=journal.identity(item),request_hash='3'*64,data=copy.deepcopy(old.data)))
            if damage=='deleted':s.delete(item)
            s.commit()
    denied=a.client.post(f'/api/ick-readiness/items/{a.qid}/ready',json=body)
    assert denied.status_code in (404,409),denied.text
    if damage!='deleted':assert snap(a.qid)['status']=='review'


def test_concurrent_publication_lease_rejects_ready_and_has_no_snapshot_bypass(candidate):
    a=candidate;body,_=prepared(a)
    with leases.scope(f'publication:{a.qid}'):
        denied=a.client.post(f'/api/ick-readiness/items/{a.qid}/ready',json=body)
        assert denied.status_code==409
    assert snap(a.qid)['status']=='review'


@pytest.mark.parametrize('when',['early','expired','manual','changed_schedule','changed_identity','changed_bytes'])
def test_dispatch_guard_is_future_only_and_never_catches_up_or_retargets(candidate,when):
    a=candidate;_,doc,_=release(a);slot=dt.datetime.fromisoformat(doc['slot']);a.clock[0]=slot
    if when=='early':a.clock[0]=slot-dt.timedelta(seconds=1)
    if when=='expired':a.clock[0]=slot+dt.timedelta(minutes=5)
    if when=='changed_identity':a.identity['facebook_connection_id']='reconnected'
    if when=='changed_bytes':a.media.write_bytes(a.media.read_bytes()+b'changed')
    if when=='changed_schedule':
        with SessionLocal() as s:qm.save_schedule(s,{**qm.schedule_config(),'timezone':'UTC'})
    with SessionLocal() as s:
        with pytest.raises(HTTPException):ir.admit(s,s.get(QueueItem,a.qid),a.media,regular=when!='manual')
    assert snap(a.qid)['status']=='ready'


@pytest.mark.parametrize('use_variant',[False,True])
def test_actual_guarded_claim_and_multipart_match_preview_for_both_destinations(candidate,monkeypatch,use_variant):
    a=candidate
    if use_variant:
        values=a.client.get(f'/api/pending-metadata/{a.qid}').json()
        description='Exact separately reviewed Facebook copy. '*12+'#TheIckRoom'
        context={'revision':'Long synthetic reviewed Facebook copy','workbook_sha256':'7'*64,'proposal_sha256':'8'*64,
            'applied_public_copy_sha256':pm.digest({k:description if k=='description' else values[k] for k in pm.PUBLIC_FIELDS})}
        approved=a.client.patch(f'/api/pending-metadata/{a.qid}',json={'operation_id':str(uuid4()),'expected_fingerprint':values['fingerprint'],
            'description':description,'approval_context':context});assert approved.status_code==200,approved.text
        a.body.update(operation_id=str(uuid4()),expected_fingerprint=approved.json()['fingerprint'],approval_receipt_id=approved.json()['receipt_id'])
        proof=a.client.post(f'/api/ick-readiness/items/{a.qid}/inspect',json={k:v for k,v in a.body.items() if k in ir.Inspect.model_fields})
        assert proof.status_code==200,proof.text
        a.body.update(operation_id=str(uuid4()),media_receipt_id=proof.json()['media_receipt_id'])
        assert a.client.post(f'/api/ick-readiness/items/{a.qid}/preview',json=a.body).status_code==409
        variant,_=approve_variant(a);assert variant.status_code==200,variant.text
        a.body['x_variant_receipt_id']=variant.json()['receipt_id']
    _,doc,_=release(a);a.clock[0]=dt.datetime.fromisoformat(doc['slot']);calls=[]
    real=httpx.AsyncClient
    def handler(request):
        fields=multipart(request);network=fields['platform[]'][0].decode();calls.append(fields)
        assert fields['video']==[a.media.read_bytes()]
        if network=='x':assert fields['x_title']==[doc['payload']['values']['x_title'].encode()]
        else:
            assert fields['facebook_title']==[doc['payload']['values']['facebook_title'].encode()]
            assert fields['facebook_description']==[doc['payload']['values']['facebook_description'].encode()]
        return httpx.Response(200,json={'request_id':'original-'+network})
    monkeypatch.setattr(upload_post.settings,'uploadpost_api_key','synthetic-test-key')
    monkeypatch.setattr(upload_post.httpx,'AsyncClient',lambda **kw:real(transport=httpx.MockTransport(handler),**kw))
    monkeypatch.setattr('backend.app.costs.record_free',lambda *a,**kw:None)
    with SessionLocal() as s:asyncio.run(qm.publish_queue_item(a.qid,s,regular=True))
    assert len(calls)==2 and snap(a.qid)['status']=='posting'
    with SessionLocal() as s:
        with pytest.raises(upload_post.UploadPostError):asyncio.run(qm.publish_queue_item(a.qid,s,regular=True))
    assert len(calls)==2


def test_old_publisher_status_write_cannot_bypass_guard_after_rollback(candidate):
    a=candidate;release(a)
    with engine.connect() as c:
        with pytest.raises(Exception,match='ICK atomic dispatch permit'):
            c.execute(text("UPDATE queue_items SET status='posting',publish_requests=:value WHERE id=:id"),{'id':a.qid,'value':json.dumps([{'client_request_id':'legacy-no-permit'}])})
        c.rollback()
    assert snap(a.qid)['status']=='ready'


def test_unsuccessful_atomic_claim_rolls_back_unused_one_use_permit(candidate,monkeypatch):
    from backend.app import drama_controls
    from backend.app.models import DramaDispatchPermit
    from sqlalchemy import update
    a=candidate;_,doc,_=release(a);a.clock[0]=dt.datetime.fromisoformat(doc['slot']);before=snap(a.qid)
    real=drama_controls.grant_dispatch
    def invalidate(s,item,attempt):
        real(s,item,attempt)
        # Fault injected after permit insertion: the actual conditional claim
        # must fail, and its rollback must remove the otherwise usable permit.
        s.execute(update(QueueItem).where(QueueItem.id==item.id).values(position=99999),execution_options={'synchronize_session':False})
    monkeypatch.setattr(drama_controls,'grant_dispatch',invalidate)
    with SessionLocal() as s:
        with pytest.raises(upload_post.UploadPostError,match='atomic claim'):
            asyncio.run(qm.publish_queue_item(a.qid,s,regular=True))
    with SessionLocal() as s:assert not s.query(DramaDispatchPermit).filter_by(queue_id=a.qid).first()
    assert snap(a.qid)==before


def test_native_identity_change_between_destinations_stops_second_send_and_partial_retry(candidate,monkeypatch):
    a=candidate;_,doc,_=release(a);a.clock[0]=dt.datetime.fromisoformat(doc['slot']);calls=[];real=httpx.AsyncClient
    def handler(request):
        fields=multipart(request);calls.append(fields['platform[]'][0].decode())
        a.identity['facebook_connection_id']='changed-after-first-destination'
        return httpx.Response(200,json={'request_id':'first-destination-only'})
    monkeypatch.setattr(upload_post.settings,'uploadpost_api_key','synthetic-test-key')
    monkeypatch.setattr(upload_post.httpx,'AsyncClient',lambda **kw:real(transport=httpx.MockTransport(handler),**kw))
    monkeypatch.setattr('backend.app.costs.record_free',lambda *a,**kw:None)
    with SessionLocal() as s:asyncio.run(qm.publish_queue_item(a.qid,s,regular=True))
    assert calls==['facebook'] and snap(a.qid)['status']=='posting'
    with SessionLocal() as s:
        with pytest.raises(upload_post.UploadPostError):asyncio.run(qm.publish_queue_item(a.qid,s,regular=True))
    assert calls==['facebook']


def test_new_rejected_publication_history_after_ready_cannot_enter_another_dispatch(candidate):
    a=candidate;_,doc,_=release(a);a.clock[0]=dt.datetime.fromisoformat(doc['slot'])
    with SessionLocal() as s:
        item=s.get(QueueItem,a.qid)
        journal.record(s,item,{'state':'rejected','profile':'TheIckRoom','platforms':['facebook'],
            'client_request_id':'new-independent-history'},'transport_result');s.commit()
    with SessionLocal() as s:
        with pytest.raises(HTTPException,match='Retained publication history'):
            ir.admit(s,s.get(QueueItem,a.qid),a.media,regular=True)
    assert snap(a.qid)['status']=='ready'


def test_source_binding_revision_added_after_ready_cannot_change_approved_source(candidate):
    from backend.app.models import QueueSourceBinding
    a=candidate;_,doc,_=release(a);a.clock[0]=dt.datetime.fromisoformat(doc['slot'])
    with SessionLocal() as s:
        item=s.get(QueueItem,a.qid)
        s.add(QueueSourceBinding(id='a'*64,queue_id=item.id,queue_identity=journal.identity(item),revision=1,data={'synthetic':'new source revision'}));s.commit()
    with SessionLocal() as s:
        with pytest.raises(HTTPException):ir.admit(s,s.get(QueueItem,a.qid),a.media,regular=True)
    assert snap(a.qid)['status']=='ready'


def test_refused_generic_ick_approval_paths_do_not_wake_or_mutate(candidate):
    a=candidate;before=snap(a.qid)
    for method,path,body in [('POST',f'/api/queue/{a.qid}/approve',None),
            ('PATCH',f'/api/queue/{a.qid}',{'status':'ready'}),
            ('POST','/api/queue/bulk-action',{'ids':[a.qid],'action':'approve'}),
            ('POST','/api/queue/bulk-action',{'ids':[a.qid],'action':'change_status','target_status':'ready'})]:
        result=a.client.request(method,path,json=body)
        assert result.status_code==409,result.text
        assert snap(a.qid)==before


def test_exact_hashtag_union_unicode_and_phrase_tags_never_rewrite_reviewed_body():
    title='Exact title';desc='Exact caption #TheIckRoom #Cafe\u0301'
    tags={'public_hashtags':['#tonightsconversation','#THEICKROOM','#Café','#Extra','#extra'],'phrase_tags':['dating advice','exact phrase']}
    r=qp.preview(title,desc,json.dumps(tags),['facebook','x'],ick=True)
    assert r['values']['x_title']==title+'\n\n'+desc+'\n\n#tonightsconversation #Extra'
    assert r['tags']['phrase_tags']==['dating advice','exact phrase']
    assert 'dating advice' not in r['values']['x_title']
    assert qp.preview(title,desc,'#tonightsconversation #THEICKROOM #Café #Extra #extra',['x'],ick=True)['values']['x_title']==r['values']['x_title']


@pytest.mark.parametrize('raw',['dating advice, relationships','dating advice','["dating advice"]','{"public_hashtags":["#ok"],"unknown":[]}'])
def test_ambiguous_phrase_lists_are_actionable_holds(raw):
    with pytest.raises(ValueError):qp.preview('Title','Caption',raw,['x'])


@pytest.mark.parametrize('title',['界'*141,'A'*281,'A'*279+'😀','https://example.invalid','Control\x00'])
def test_conservative_unicode_and_unsupported_copy_never_silently_fits(title):
    with pytest.raises(ValueError):qp.preview(title,'','',['x'])


def clone(a):
    with SessionLocal() as s:
        values=snap(a.qid);values.pop('id');values.pop('created_at');item=QueueItem(**values);s.add(item);s.commit();qid=item.id
    value=a.client.get(f'/api/pending-metadata/{qid}').json();context={'revision':'Other synthetic approved item','workbook_sha256':'1'*64,'proposal_sha256':'2'*64,'applied_public_copy_sha256':pm.digest({k:value[k] for k in pm.PUBLIC_FIELDS})}
    response=a.client.patch(f'/api/pending-metadata/{qid}',json={'operation_id':str(uuid4()),'expected_fingerprint':value['fingerprint'],'title':value['title'],'approval_context':context});assert response.status_code==200,response.text
    req={**a.body,'operation_id':str(uuid4()),'expected_fingerprint':response.json()['fingerprint'],'approval_receipt_id':response.json()['receipt_id']};req.pop('media_receipt_id')
    inspection=a.client.post(f'/api/ick-readiness/items/{qid}/inspect',json={k:v for k,v in req.items() if k in ir.Inspect.model_fields});assert inspection.status_code==200,inspection.text
    req.update(operation_id=str(uuid4()),media_receipt_id=inspection.json()['media_receipt_id'])
    return SimpleNamespace(**{**vars(a),'qid':qid,'body':req})


def test_concurrent_reservations_do_not_double_allocate_and_stale_preview_requires_new_ack(candidate):
    a=candidate;b=clone(a);ba,da=prepared(a);bb,db=prepared(b);assert da['slot']==db['slot']
    def send(pair):obj,body=pair;return obj.client.post(f'/api/ick-readiness/items/{obj.qid}/ready',json=body)
    with ThreadPoolExecutor(2) as pool:result=list(pool.map(send,[(a,ba),(b,bb)]))
    assert sorted(r.status_code for r in result)==[200,409]
    loser=b if result[0].status_code==200 else a
    loser.body['operation_id']=str(uuid4());_,later,_=release(loser)
    assert dt.datetime.fromisoformat(later['slot'])>dt.datetime.fromisoformat(da['slot'])


def approve_variant(a,text=None,**changes):
    text=text or 'Separately approved exact X words. #TheIckRoom #tonightsconversation #Extra'
    body={k:v for k,v in a.body.items() if k in ir.Inspect.model_fields};body.update(operation_id=str(uuid4()),text=text,
        approved_revision='Synthetic separately reviewed X revision',approved_workbook_sha256='4'*64,
        approved_proposal_sha256='5'*64,approved_sha256=pm.digest(text),approval_reference='Synthetic explicit owner approval of this exact X text',
        decision='Accept this exact X-only variant for the unchanged reviewed ICK source and Facebook copy')
    body.update(changes)
    return a.client.post(f'/api/ick-readiness/items/{a.qid}/x-variants/review',json=body),body


def test_explicit_variant_approval_binds_X_while_Facebook_remains_exact_and_retry_safe(candidate):
    a=candidate;before=snap(a.qid);result,request=approve_variant(a);assert result.status_code==200,result.text
    assert a.client.post(f'/api/ick-readiness/items/{a.qid}/x-variants/review',json=request).json()['replayed']
    a.body['x_variant_receipt_id']=result.json()['x_variant_receipt_id'];body,doc,receipt=release(a)
    assert doc['payload']['values']['x_title']==request['text']
    assert doc['payload']['values']['facebook_title']==before['title']
    assert doc['payload']['values']['facebook_description']==before['description']+'\n\n'+before['tags']
    assert doc['x_variant_receipt_id']==result.json()['receipt_id']
    changed={**body,'x_variant_receipt_id':str(uuid4())};assert a.client.post(f'/api/ick-readiness/items/{a.qid}/ready',json=changed).status_code==409


@pytest.mark.parametrize('damage',['missing_required','wrong_hash','no_decision','blank_reference','unregistered','stale'])
def test_unapproved_dropped_or_stale_variant_cannot_ready(candidate,damage):
    a=candidate
    if damage=='unregistered':
        a.body['x_variant_receipt_id']=str(uuid4());assert a.client.post(f'/api/ick-readiness/items/{a.qid}/preview',json=a.body).status_code==409;return
    text='Approved short words. #TheIckRoom #tonightsconversation #Extra'
    if damage=='missing_required':text=text.replace('#theickroom','').replace('#TheIckRoom','')
    changes={}
    if damage=='wrong_hash':changes['approved_sha256']='0'*64
    if damage=='no_decision':changes['decision']='Not approved'
    if damage=='blank_reference':changes['approval_reference']=' '
    result,_=approve_variant(a,text,**changes)
    if damage=='stale':
        assert result.status_code==200; a.body['x_variant_receipt_id']=result.json()['receipt_id']
        with SessionLocal() as s:
            old=s.get(MetadataMutationReceipt,a.body['approval_receipt_id']);s.add(MetadataMutationReceipt(operation_id=str(uuid4()),queue_id=a.qid,queue_identity=old.queue_identity,request_hash='6'*64,data=copy.deepcopy(old.data)));s.commit()
        assert a.client.post(f'/api/ick-readiness/items/{a.qid}/preview',json=a.body).status_code==409
    else:assert result.status_code in (409,422),result.text
    assert snap(a.qid)['status']=='review'


def test_explicit_ick_variant_may_omit_optional_facebook_hashtags_but_preserves_facebook_and_backend(candidate):
    a=candidate;before=snap(a.qid)
    exact='Reviewed complete summary sentence. #tonightsconversation #theickroom'
    result,_=approve_variant(a,exact);assert result.status_code==200,result.text
    a.body['x_variant_receipt_id']=result.json()['receipt_id'];_,doc,_=release(a)
    assert doc['payload']['values']['x_title']==exact
    assert '#Extra' in doc['payload']['values']['facebook_description']
    assert snap(a.qid)['tags']==before['tags']


def test_missed_slot_is_visible_and_only_explicit_unsent_withdrawal_allows_new_future_review(candidate):
    a=candidate;_,doc,response=release(a);a.clock[0]=dt.datetime.fromisoformat(doc['expires_at'])
    assert a.client.get('/api/ick-readiness/receipts/'+response['receipt_id']).json()['reservation_state']=='expired_no_catchup'
    with SessionLocal() as s:item=s.get(QueueItem,a.qid);fp=pm.fingerprint(s,item)
    req={'operation_id':str(uuid4()),'expected_fingerprint':fp,'release_id':response['receipt_id'],'reason':'Explicit synthetic owner restaging of an unsent missed slot',
        'decision':'Withdraw this unsent ICK reservation without posting or reusing its old slot'}
    got=a.client.post(f'/api/ick-readiness/items/{a.qid}/withdraw',json=req);assert got.status_code==200,got.text
    assert a.client.post(f'/api/ick-readiness/items/{a.qid}/withdraw',json=req).json()['replayed']
    assert a.client.get('/api/ick-readiness/receipts/'+response['receipt_id']).json()['reservation_state']=='withdrawn'
    a.body['operation_id']=str(uuid4());_,later,_=release(a)
    assert dt.datetime.fromisoformat(later['slot'])>a.clock[0]
