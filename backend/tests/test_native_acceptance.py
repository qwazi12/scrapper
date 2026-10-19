"""Exact native review and transport gates; all media/providers are local fixtures."""
import asyncio
from copy import deepcopy
import io

from fastapi.testclient import TestClient
from PIL import Image
import pytest

from backend.app import auth
from backend.app.db import SessionLocal
from backend.app.main import app
from backend.app.models import CreativeExport,QueueItem
from backend.app.social import queue_manager as qm,upload_post
from backend.tests.test_native_campaigns import rendered,acceptance_request,approve_native,brief,research
from backend.tests.test_native_manual_formats import campaign


@pytest.fixture
def client():return TestClient(app,headers={'x-access-token':'test-token'})


def test_native_review_is_private_and_legacy_approval_cannot_release(client):
    _,_,export=rendered(client);eid=export['id']
    anonymous=TestClient(app)
    url=f'/api/editorial/exports/{eid}/export-review'
    assert anonymous.get(url).status_code==401
    assert anonymous.get(url+'?g='+auth.media_pass()['pass']).status_code==401
    body=acceptance_request(client,eid)
    assert client.post(f'/api/editorial/exports/{eid}/approve',json={'reviewed':True}).status_code==422
    with SessionLocal() as s:
        row=s.get(CreativeExport,eid);row.approval={'fingerprint':body['expected_fingerprint']};s.commit()
    assert client.post(f'/api/editorial/exports/{eid}/enqueue',json={'accounts':['default:instagram']}).status_code==409
    result=approve_native(client,eid)['approval']
    assert result['schema']==2 and result['rights_basis']=='owner_attested' and result['rights_verified_by_app'] is False


def test_changed_inspection_requires_every_new_finding_and_blocks_existing_queue(client,monkeypatch):
    _,_,export=rendered(client);eid=export['id'];old=acceptance_request(client,eid)
    approve_native(client,eid)
    queued=client.post(f'/api/editorial/exports/{eid}/enqueue',json={'accounts':['default:instagram']}).json()['queue_item_id']
    with SessionLocal() as s:
        row=s.get(CreativeExport,eid);m=deepcopy(row.manifest)
        m['quality'].update(warnings=['Review this new fixture inspection warning'],black_spans=[{'start':0,'end':1}],silence_spans=[{'start':1,'end':5}])
        row.manifest=m;s.commit()
    response=client.post(f'/api/editorial/exports/{eid}/approve',json=old)
    assert response.status_code==409 and 'changed' in response.json()['detail']
    assert client.post(f'/api/editorial/exports/{eid}/enqueue',json={'accounts':['default:instagram']}).status_code==409
    calls=[]
    async def forbidden(*args,**kwargs):calls.append('provider');raise AssertionError('No transport before fresh review')
    monkeypatch.setattr(upload_post,'list_accounts',forbidden)
    monkeypatch.setattr(upload_post,'upload_images',forbidden)
    with SessionLocal() as s:
        with pytest.raises(Exception,match='acceptance evidence is incomplete'):
            asyncio.run(qm.publish_queue_item(queued,s))
        assert not s.get(QueueItem,queued).publish_requests
    assert not calls
    fresh=acceptance_request(client,eid)
    assert len(fresh['warning_decisions'])==3
    for changes in [{'warning_decisions':{}},{'checks':['rights']*4},
                    {'warning_decisions':{**fresh['warning_decisions'],'stale':'Not a current finding requiring an owner decision.'}},
                    {'warning_decisions':dict.fromkeys(fresh['warning_decisions'],'fine')}]:
        assert client.post(f'/api/editorial/exports/{eid}/approve',json={**fresh,**changes}).status_code==409
    assert client.post(f'/api/editorial/exports/{eid}/approve',json={**fresh,'ignored':True}).status_code==422
    approve_native(client,eid)
    # The old queued fingerprint stays fenced even after accepting the revised inspection.
    with SessionLocal() as s:
        with pytest.raises(upload_post.UploadPostError,match='approval changed'):
            asyncio.run(qm.publish_queue_item(queued,s))
    assert not calls


def test_reused_source_images_have_individual_review_decisions(client):
    image=io.BytesIO();Image.new('RGB',(96,96),'#164272').save(image,format='PNG')
    imported=client.post('/api/editorial/assets',data={'license':'owner_created',
        'attestation':'Original synthetic blue frame created for this isolated repetition test'},files={'file':('original.png',image.getvalue(),'image/png')})
    assert imported.status_code==200,imported.text
    oid=research(client);client.post(f'/api/editorial/opportunities/{oid}/approve')
    body=brief(oid)
    for beat in body['beats']:beat['asset_id']=imported.json()['id']
    cid=client.post('/api/editorial/campaigns',json=body).json()['id']
    export=client.post(f'/api/editorial/campaigns/{cid}/render/carousel').json();eid=export['id']
    review=client.get(f'/api/editorial/exports/{eid}/export-review').json()
    repeated=[f for f in review['findings'] if f['kind']=='visual_repetition']
    assert len(repeated)==2 and repeated[0]['id']!=repeated[1]['id']
    approve_native(client,eid)


def test_single_image_transport_uses_its_exact_native_copy_and_rechecks_under_pin(client,monkeypatch):
    cid,body=campaign(client,['image'])
    export=client.post(f'/api/editorial/campaigns/{cid}/render/image').json();eid=export['id']
    approve_native(client,eid)
    queued=client.post(f'/api/editorial/exports/{eid}/enqueue',json={'accounts':['default:instagram']}).json()['queue_item_id']
    calls=[]
    async def accounts():return [{'id':'default:instagram','profile':'default'}]
    async def upload(paths,**kwargs):
        assert kwargs['description']==body['image_caption']
        calls.append([str(p) for p in paths]);return 'synthetic-single-image-receipt'
    monkeypatch.setattr(upload_post,'list_accounts',accounts)
    monkeypatch.setattr(upload_post,'upload_images',upload)
    original=qm.require_native_queue;checks=[]
    def checked(s,item):
        checks.append(item.id);return original(s,item)
    monkeypatch.setattr(qm,'require_native_queue',checked)
    with SessionLocal() as s:
        item=asyncio.run(qm.publish_queue_item(queued,s))
        assert item.status=='posting'
    assert checks==[queued,queued]
    assert calls==[[export['manifest']['cards'][0]['file']]]


def test_actual_reel_keeps_its_own_caption_at_video_transport(client,monkeypatch):
    from backend.app.editorial import native
    # A generated tone replaces speech synthesis only; cards, video mux/decode,
    # review, queue and provider boundary still run. This does not prove speech quality.
    def tone(text,path):
        native.media.run(['ffmpeg','-v','error','-y','-f','lavfi','-i','sine=frequency=330:duration=1',str(path)])
    monkeypatch.setattr(native.tts,'synth',tone)
    cid,body=campaign(client,['reel'])
    result=client.post(f'/api/editorial/campaigns/{cid}/render/reel')
    assert result.status_code==200,result.text
    export=result.json();eid=export['id']
    assert export['manifest']['quality']['decode']=='passed'
    approve_native(client,eid)
    queued=client.post(f'/api/editorial/exports/{eid}/enqueue',json={'accounts':['default:instagram']}).json()['queue_item_id']
    calls=[]
    async def accounts():return [{'id':'default:instagram','profile':'default'}]
    async def upload(path,**kwargs):
        assert kwargs['description']==body['reel_caption'] and kwargs['description']!=body['short_caption']
        calls.append(str(path));return 'synthetic-reel-receipt'
    monkeypatch.setattr(upload_post,'list_accounts',accounts)
    monkeypatch.setattr(upload_post,'upload_video',upload)
    with SessionLocal() as s:
        item=asyncio.run(qm.publish_queue_item(queued,s))
        assert item.status=='posting'
    assert calls==[export['manifest']['video']['file']]
