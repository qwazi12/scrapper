"""Still duplicates must prove retained bytes, just like video/audio reuse."""
import hashlib
import io
from pathlib import Path

from fastapi.testclient import TestClient
from PIL import Image
import pytest

from backend.app.db import SessionLocal
from backend.app.main import app
from backend.app.models import MediaAsset
from backend.app import file_use


def fixture(tmp_path):
    image=io.BytesIO();color=tuple(hashlib.sha256(str(tmp_path).encode()).digest()[:3])
    Image.new('RGB',(319,179),color).save(image,format='PNG')
    data={'license':'owner_created','attestation':'This original generated image is created by the isolated local integrity fixture.','tags':'original,test'}
    client=TestClient(app,headers={'x-access-token':'test-token'})
    def upload():return client.post('/api/editorial/assets',data=data,files={'file':('original.png',image.getvalue(),'image/png')})
    result=upload();assert result.status_code==200,result.text
    with SessionLocal() as s:path=Path(s.get(MediaAsset,result.json()['id']).file_path)
    return upload,path,result.json()['id']


@pytest.mark.parametrize('change',['corrupt','missing'])
def test_still_duplicate_refuses_unverified_retained_copy(tmp_path,change):
    upload,path,aid=fixture(tmp_path)
    if change=='corrupt':path.write_bytes(b'corrupted retained still')
    else:path.unlink()
    response=upload()
    assert response.status_code==409,response.text
    assert 'duplicate reuse refused' in response.text
    assert path.read_bytes()==b'corrupted retained still' if change=='corrupt' else not path.exists()
    with SessionLocal() as s:assert s.get(MediaAsset,aid) is not None


def test_still_duplicate_obeys_independent_exclusive_source_claim(tmp_path):
    upload,path,aid=fixture(tmp_path);original=path.read_bytes()
    with file_use.pin([path],exclusive=True),file_use.suspend():assert upload().status_code==409
    response=upload();assert response.status_code==200 and response.json()=={'id':aid,'duplicate':True}
    assert path.read_bytes()==original


@pytest.mark.parametrize('change',['verified','corrupt','missing'])
def test_concurrent_unique_winner_is_verified_before_duplicate_success(tmp_path,monkeypatch,change):
    from sqlalchemy.orm import Query
    upload,path,aid=fixture(tmp_path)
    if change=='corrupt':path.write_bytes(b'corrupted concurrent winner')
    elif change=='missing':path.unlink()
    # Simulate the first read missing another import's just-committed winner.
    # The ensuing insert really violates the database's unique SHA constraint.
    original_first=Query.first;hidden=[]
    def first(query):
        if not hidden and query.column_descriptions[0].get('entity') is MediaAsset:
            hidden.append(True);return None
        return original_first(query)
    monkeypatch.setattr(Query,'first',first)
    response=upload();assert hidden
    if change=='verified':assert response.status_code==200 and response.json()=={'id':aid,'duplicate':True}
    else:assert response.status_code==409 and 'duplicate reuse refused' in response.text
