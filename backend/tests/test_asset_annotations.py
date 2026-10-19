"""Real immutable owner annotations and source-use gates; no external publication."""
import asyncio
from copy import deepcopy
from datetime import timedelta
import io
from pathlib import Path

from fastapi.testclient import TestClient
from PIL import Image
import pytest
from sqlalchemy import text
from sqlalchemy.exc import IntegrityError

from backend.app import auth,file_use
from backend.app.db import SessionLocal
from backend.app.main import app
from backend.app.models import AssetAnnotation,MediaAsset,CreativeExport
from backend.app.editorial import content_lineage
from backend.tests.test_asset_catalog import client,sources,upload
from backend.tests.test_native_campaigns import research,brief,approve_native,acceptance_request


def body(view,**changes):
    return {**view['metadata'],'expected_version':view['version'],
        'description':'Reviewed violet pattern moving across this original procedural source.',
        'tags':['violet','original'], 'rationale':'Correct the owner labels after inspecting the complete permitted source.',
        'reviewed':True,**changes}


def review(client,aid):
    result=client.get(f'/api/editorial/assets/{aid}/metadata-review')
    assert result.status_code==200,result.text
    return result.json()


def save(client,aid,values):return client.post(f'/api/editorial/assets/{aid}/metadata-review',json=values)


def still(client):
    image=io.BytesIO();Image.new('RGB',(320,180),'purple').save(image,format='PNG')
    response=client.post('/api/editorial/assets',data={'license':'owner_created','attestation':'Created this original procedural purple still for the isolated fixture.','tags':'purple'},
        files={'file':('original.png',image.getvalue(),'image/png')})
    assert response.status_code==200,response.text
    return response.json()['id']


def test_revision_search_and_exact_retry_preserve_original_bytes_and_evidence(client,sources):
    row=upload(client,sources[0],'video').json();aid=row['id'];initial=review(client,aid)
    with SessionLocal() as s:
        asset=s.get(MediaAsset,aid);original=(deepcopy(asset.tags),deepcopy(asset.provenance),Path(asset.file_path).read_bytes())
    values=body(initial,caption_cues=[{'start':.25,'end':1.5,'text':'A violet ribbon turns here.'}])
    response=save(client,aid,values);assert response.status_code==200,response.text
    saved=response.json();assert saved['created'] and saved['is_current']
    current=saved['view'];assert current['history_count']==1 and current['version']!=initial['version']
    assert current['original']==initial['original']
    item=next(x for x in client.get('/api/editorial/assets/search?q=violet').json()['assets'] if x['id']==aid)
    assert item['matching_spans']==values['caption_cues'] and item['metadata_version']==current['version']
    assert client.get(f'/api/editorial/assets/{aid}').json()['caption_cues']==values['caption_cues']
    assert save(client,aid,values).json()['created'] is False
    assert save(client,aid,{**values,'description':'A stale changed description must never overwrite the newer review.'}).status_code==409
    blocked=save(client,aid,body(current,use_status='blocked')).json()
    retry=save(client,aid,values).json()
    assert not retry['created'] and not retry['is_current'] and retry['view']['version']==blocked['view']['version']
    assert retry['view']['metadata']['use_status']=='blocked' and retry['view']['history_count']==2
    with SessionLocal() as s:
        asset=s.get(MediaAsset,aid)
        assert (asset.tags,asset.provenance,Path(asset.file_path).read_bytes())==original
        assert s.get(AssetAnnotation,saved['id']).data['permission_verified_by_app'] is False
    for statement in ['UPDATE asset_annotations SET revision=99 WHERE id=:id','DELETE FROM asset_annotations WHERE id=:id']:
        with SessionLocal() as s,pytest.raises(IntegrityError,match='append-only'):
            s.execute(text(statement),{'id':saved['id']});s.commit()


def test_review_auth_bounds_integrity_and_independent_file_claim(client,sources):
    row=upload(client,sources[1],'audio').json();aid=row['id'];values=body(review(client,aid))
    public=TestClient(app)
    assert public.get(f'/api/editorial/assets/{aid}/metadata-review').status_code==401
    assert public.get(f'/api/editorial/assets/{aid}/metadata-review?g='+auth.media_pass()['pass']).status_code==401
    assert public.post(f'/api/editorial/assets/{aid}/metadata-review',json=values).status_code==401
    for patch in [{'reviewed':False},{'tags':['same','SAME']},{'people':['']},{'attestation':' '*21},
        {'caption_cues':[{'start':0,'end':99,'text':'outside'}]},{'caption_cues':[{'start':True,'end':1,'text':'boolean'}]}, {'unexpected':'field'}]:
        result=save(client,aid,{**values,**patch});assert result.status_code==422,result.text
    with SessionLocal() as s:path=s.get(MediaAsset,aid).file_path
    with file_use.pin([path]),file_use.suspend():
        assert save(client,aid,values).status_code==409
    assert save(client,aid,values).status_code==200
    path=Path(path);path.write_bytes(b'changed bytes')
    assert save(client,aid,body(review(client,aid))).status_code==409
    assert review(client,aid)['history_count']==1
    image=still(client)
    assert save(client,image,body(review(client,image),caption_cues=[{'start':0,'end':1,'text':'not timed'}])).status_code==422


def test_original_catalog_change_and_reused_id_require_a_new_owner_review(client):
    aid=still(client);first=save(client,aid,body(review(client,aid))).json()['view']
    with SessionLocal() as s:
        asset=s.get(MediaAsset,aid);asset.tags=['changed outside review'];s.commit()
    current=review(client,aid)
    assert current['metadata']['use_status']=='blocked' and current['version']!=first['version']
    assert save(client,aid,body(current)).status_code==200
    with SessionLocal() as s:
        asset=s.get(MediaAsset,aid);asset.created_at+=timedelta(seconds=1);s.commit()
    replacement=review(client,aid)
    assert replacement['asset_identity']!=first['asset_identity'] and replacement['history_count']==0


def test_metadata_and_revoked_permission_block_old_release_but_preserve_lineage(client,monkeypatch):
    aid=still(client);version=review(client,aid)['version'];oid=research(client)
    assert client.post(f'/api/editorial/opportunities/{oid}/approve').status_code==200
    values=brief(oid);values['beats'][0].update(asset_id=aid,asset_metadata_version=version)
    created=client.post('/api/editorial/campaigns',json=values)
    assert created.status_code==200,created.text
    cid=created.json()['id'];rendered=client.post(f'/api/editorial/campaigns/{cid}/render/carousel')
    assert rendered.status_code==200,rendered.text
    exported=rendered.json();eid=exported['id'];approve_native(client,eid)
    with SessionLocal() as s:
        oldlineage=content_lineage.source(s,'native_export',eid)[2]
        assert s.get(CreativeExport,eid).manifest['inputs']['assets'][str(aid)]['asset_identity']==review(client,aid)['asset_identity']
    current=save(client,aid,body(review(client,aid),use_status='blocked')).json()['view']
    assert client.post(f'/api/editorial/exports/{eid}/enqueue',json={'accounts':['default:instagram']}).status_code==409
    assert client.get(f'/api/editorial/exports/{eid}/export-review').status_code==409
    assert client.post(f'/api/editorial/campaigns/{cid}/render/carousel').status_code==409
    assert client.post('/api/editorial/campaigns',json=values).status_code==409
    current=save(client,aid,body(current,use_status='permitted')).json()['view']
    assert client.post('/api/editorial/campaigns',json=values).status_code==409
    values['beats'][0]['asset_metadata_version']=current['version']
    revised=client.post('/api/editorial/campaigns',json=values);assert revised.status_code==200,revised.text
    fresh=client.post(f"/api/editorial/campaigns/{revised.json()['id']}/render/carousel")
    assert fresh.status_code==200,fresh.text
    approve_native(client,fresh.json()['id'])
    queued=client.post(f"/api/editorial/exports/{fresh.json()['id']}/enqueue",json={'accounts':['default:instagram']})
    assert queued.status_code==200,queued.text
    # Exercise the actual publisher claim and durable intent; fake only the external transport.
    from backend.app.social import queue_manager,upload_post
    decisions=body(current,use_status='blocked');calls=[]
    async def accounts():return [{'id':'default:instagram','profile':'default'}]
    async def upload(paths,**kwargs):
        with file_use.suspend():assert save(client,aid,decisions).status_code==409
        calls.append(kwargs['idempotency_key']);return 'synthetic-annotation-source-receipt'
    monkeypatch.setattr(upload_post,'list_accounts',accounts);monkeypatch.setattr(upload_post,'upload_images',upload)
    with SessionLocal() as s:
        result=asyncio.run(queue_manager.publish_queue_item(queued.json()['queue_item_id'],s))
        assert result.status=='posting' and result.publish_requests[-1]['request_id']=='synthetic-annotation-source-receipt'
    assert len(calls)==1
    assert save(client,aid,decisions).status_code==200
    with SessionLocal() as s:assert content_lineage.source(s,'native_export',eid)[2]==oldlineage
    assert Path(exported['manifest']['cards'][0]['file']).is_file()
