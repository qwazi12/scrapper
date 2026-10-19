"""Real bounded local video/audio indexing, captions, retrieval and integrity."""
import json
from pathlib import Path

from fastapi.testclient import TestClient
import pytest

from backend.app import auth
from backend.app.main import app
from backend.app.studio import media


@pytest.fixture
def client():return TestClient(app,headers={'x-access-token':'test-token'})


@pytest.fixture
def sources(tmp_path):
    import hashlib
    tone=400+int(hashlib.sha256(str(tmp_path).encode()).hexdigest()[:8],16)/1000000
    video=tmp_path/'original.mp4';audio=tmp_path/'original.wav'
    media.run(['ffmpeg','-v','error','-y','-f','lavfi','-i','testsrc2=s=320x180:r=24:d=2',
        '-f','lavfi','-i',f'sine=f={tone}:d=2','-c:v','libx264','-pix_fmt','yuv420p','-c:a','aac',str(video)])
    media.run(['ffmpeg','-v','error','-y','-f','lavfi','-i',f'sine=f={tone+100}:d=2',str(audio)])
    return video,audio


def upload(client,path,kind,**changes):
    data={'kind':kind,'license':'owner_created','attestation':'This original procedural media is created by the local synthetic test.',
        'description':'Original moving colors and generated sound used for a fixture.', 'tags':'synthetic, moving colors','people':'No real people',
        'caption_cues':json.dumps([{'start':0,'end':1.8,'text':'A unique turquoise fixture changes the frame.'}]),**changes}
    return client.post('/api/editorial/assets/media',data=data,files={'file':('../unsafe.ext',path.read_bytes(),'application/octet-stream')})


def test_real_media_index_has_previews_caption_spans_and_deduplicated_provenance(client,sources):
    video,audio=sources
    for path,kind in [(video,'video'),(audio,'audio')]:
        response=upload(client,path,kind)
        assert response.status_code==200,response.text
        row=response.json();aid=row['id'];assert not row['duplicate']
        metadata=row['provenance']['media']
        assert 1.9<=metadata['seconds']<=2.2 and metadata['caption_count']==1
        assert len(metadata['frames'])==(3 if kind=='video' else 0)
        details=client.get(f'/api/editorial/assets/{aid}')
        assert details.json()['caption_cues'][0]['start']==0
        duplicate=upload(client,path,kind,description='A different description must not rewrite the existing rights record.')
        assert duplicate.status_code==200 and duplicate.json()['id']==aid and duplicate.json()['duplicate']
        assert duplicate.json()['provenance']['description']==row['provenance']['description']
        search=client.get('/api/editorial/assets/search',params={'q':'turquoise','kind':kind}).json()
        result=next(item for item in search['assets'] if item['id']==aid)
        assert result['matching_spans']==[{'start':0.0,'end':1.8,'text':'A unique turquoise fixture changes the frame.'}]
        assert search['semantic_model']=='not_run' and result['identity_basis']=='owner_labels_not_face_recognition'
        public=TestClient(app)
        assert public.get(f'/api/editorial/assets/{aid}').status_code==401
        assert public.get(f'/api/editorial/assets/{aid}?g='+auth.media_pass()['pass']).status_code==401
        assert public.get(f'/api/editorial/assets/{aid}/media?g='+auth.media_pass()['pass']).status_code==200
        assert client.get(f'/api/editorial/assets/{aid}/media').headers['content-type'].startswith('video/mp4' if kind=='video' else 'audio/mp4')
        if kind=='video':
            first=client.get(f'/api/editorial/assets/{aid}/frame/0')
            assert first.status_code==200 and first.content[:2]==b'\xff\xd8'
            assert client.get(f'/api/editorial/assets/{aid}/frame/99').status_code==404
            frame=Path(metadata['frames'][0]['file']);frame.write_bytes(b'tampered preview')
            assert client.get(f'/api/editorial/assets/{aid}/frame/0').status_code==409


@pytest.mark.parametrize('caption',[
    '[{"start":0,"end":99,"text":"outside"}]',
    '[{"start":true,"end":1,"text":"boolean"}]',
    '[{"start":0,"end":1.5,"text":"first"},{"start":1,"end":1.8,"text":"overlap"}]',
    '[{"start":0,"end":1,"text":"valid","unexpected":"field"}]','{}','not json',
])
def test_invalid_cues_fail_without_index_or_leftover_temporary_files(client,sources,caption):
    from backend.app.editorial.asset_catalog import root
    result=upload(client,sources[0],'video',caption_cues=caption)
    assert result.status_code==400,result.text
    assert not list(root().glob('.import-*'))


def test_actual_stream_kind_and_source_limits_are_checked(client,sources,monkeypatch):
    from backend.app.editorial import asset_catalog
    assert upload(client,sources[1],'video').status_code==400
    monkeypatch.setattr(asset_catalog,'MAX_SOURCE',1)
    assert upload(client,sources[0],'video').status_code==413
    assert not list(asset_catalog.root().glob('.import-*'))


def test_response_rechecks_immutable_hash_after_route_constructed_it(client,sources,monkeypatch):
    from backend.app.editorial import asset_catalog
    row=upload(client,sources[1],'audio').json()
    original=asset_catalog.FileResponse
    def changed_after_construction(path,**kwargs):
        response=original(path,**kwargs)
        Path(path).write_bytes(b'changed after the route created its response')
        return response
    monkeypatch.setattr(asset_catalog,'FileResponse',changed_after_construction)
    assert client.get(f"/api/editorial/assets/{row['id']}/media").status_code==409


def test_storage_preflight_fails_before_import(client,sources,monkeypatch):
    from backend.app.editorial import asset_catalog
    from types import SimpleNamespace
    monkeypatch.setattr(asset_catalog.shutil,'disk_usage',lambda path:SimpleNamespace(free=1))
    assert upload(client,sources[0],'video').status_code==409
    assert not list(asset_catalog.root().glob('.import-*'))


def test_duplicate_cannot_report_success_for_corrupted_existing_media(client,sources):
    from backend.app.db import SessionLocal
    from backend.app.models import MediaAsset
    # Use a distinct description only for this first import; no rights metadata is rewritten on reuse.
    response=upload(client,sources[0],'video')
    assert response.status_code==200,response.text
    with SessionLocal() as s:path=Path(s.get(MediaAsset,response.json()['id']).file_path)
    path.write_bytes(b'corrupted retained catalog media')
    duplicate=upload(client,sources[0],'video')
    assert duplicate.status_code==409 and 'duplicate reuse refused' in duplicate.text
    assert path.read_bytes()==b'corrupted retained catalog media'
