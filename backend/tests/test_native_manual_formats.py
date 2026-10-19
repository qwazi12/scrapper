"""Actual local packages and image artifacts; no real posting/provider calls."""
import json
from pathlib import Path

from fastapi.testclient import TestClient
from PIL import Image
import pytest

from backend.app import auth
from backend.app.db import SessionLocal
from backend.app.main import app
from backend.app.models import QueueItem
from backend.tests.test_native_campaigns import brief,research,approve_native


@pytest.fixture
def client():return TestClient(app,headers={'x-access-token':'test-token'})


def campaign(client,formats):
    oid=research(client)
    original=client.get('/api/editorial/opportunities').json()
    row=next(row for row in original if row['id']==oid)
    fields={key:row[key] for key in ('title','angle','audience','evidence','formats')}
    fields['formats']=formats
    assert client.put(f'/api/editorial/opportunities/{oid}',json=fields).status_code==200
    assert client.post(f'/api/editorial/opportunities/{oid}/approve').status_code==200
    body={**brief(oid),'formats':formats,'image_text':'One original shape can change a scene.',
        'image_caption':'Which original composition would you choose for the clearest message?',
        'community_text':'We made three original shape compositions. Which change made the idea clearest, and why?',
        'poll_question':'Which original shape composition would you choose?', 'poll_options':['Bold center','Quiet corner','Moving edge'],
        'reel_caption':'Notice the frame before the payoff. Save the original composition you prefer.'}
    result=client.post('/api/editorial/campaigns',json=body)
    assert result.status_code==200,result.text
    return result.json()['id'],body


@pytest.mark.parametrize('fmt',['community','poll'])
def test_private_manual_package_is_actual_files_never_queue_or_publish(client,fmt):
    cid,body=campaign(client,[fmt])
    with SessionLocal() as s:before=s.query(QueueItem).count()
    result=client.post(f'/api/editorial/campaigns/{cid}/render/{fmt}')
    assert result.status_code==200,result.text
    exported=result.json();eid=exported['id'];manifest=exported['manifest']
    assert manifest['delivery']=='manual_only' and not exported['queue_item_id']
    assert manifest['quality']['decode']=='passed' and len(manifest['cards'])==1
    json_file=next(item for item in manifest['manual_assets'] if item['file'].endswith('.json'))
    payload=json.loads(Path(json_file['file']).read_text())
    assert payload['delivery']=='manual_only' and payload['publication_status']=='not_published'
    assert payload['options']==(body['poll_options'] if fmt=='poll' else [])
    url=f"/api/editorial/exports/{eid}/manual/{Path(json_file['file']).name}"
    assert client.get(url).status_code==409
    anonymous=TestClient(app)
    assert anonymous.get(url+'?g='+auth.media_pass()['pass']).status_code==401
    assert client.get(f"/api/editorial/exports/{eid}/file/{Path(json_file['file']).name}").status_code==404
    approve_native(client,eid)
    download=client.get(url)
    assert download.status_code==200 and download.json()==payload and 'attachment' in download.headers['content-disposition']
    assert client.post(f'/api/editorial/exports/{eid}/enqueue',json={'accounts':['default:youtube']}).status_code==409
    with SessionLocal() as s:assert s.query(QueueItem).count()==before
    Path(json_file['file']).write_text('tampered private payload')
    assert client.get(url).status_code==409


def test_single_image_has_one_checked_image_and_its_own_caption(client):
    cid,body=campaign(client,['image'])
    result=client.post(f'/api/editorial/campaigns/{cid}/render/image')
    assert result.status_code==200,result.text
    item=result.json();manifest=item['manifest'];eid=item['id']
    assert len(manifest['cards'])==1 and not manifest.get('video')
    assert manifest['cards'][0]['alt']==body['image_text']
    with Image.open(manifest['cards'][0]['file']) as image:
        image.load();assert image.size==(1080,1350)
    approve_native(client,eid)
    assert client.post(f'/api/editorial/exports/{eid}/enqueue',json={'accounts':['default:youtube']}).status_code==400
    queue=client.post(f'/api/editorial/exports/{eid}/enqueue',json={'accounts':['default:instagram']})
    assert queue.status_code==200,queue.text
    with SessionLocal() as s:
        row=s.get(QueueItem,queue.json()['queue_item_id'])
        assert row.media_kind=='images' and row.status=='review' and len(row.media_files)==1
        assert row.description==body['image_caption'] and not row.video_path


@pytest.mark.parametrize('changes',[
    {'poll_options':['Same','same']},{'poll_options':['one']},{'poll_options':['x','valid']},
    {'poll_options':['one','two','three','four','five']},{'poll_question':'short'},
])
def test_poll_refuses_invalid_options_or_missing_question(client,changes):
    cid,body=campaign(client,['poll'])
    result=client.post('/api/editorial/campaigns',json={**body,**changes})
    assert result.status_code==422,result.text


def test_reel_requires_its_own_platform_copy(client):
    cid,body=campaign(client,['reel'])
    assert client.post('/api/editorial/campaigns',json={**body,'reel_caption':''}).status_code==422
