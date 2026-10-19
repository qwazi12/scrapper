import asyncio
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime,timedelta,timezone

import pytest
from fastapi import HTTPException

from backend.app import file_use
from backend.app.db import SessionLocal
from backend.app.file_response import FileResponse
from backend.app.models import FileUse,WorkerLease
from backend.app.worker_leases import LeaseLost


def outside(fn):
    with ThreadPoolExecutor(1) as pool:return pool.submit(fn).result(timeout=10)


def replace(path):
    with file_use.pin([path.parent],exclusive=True):path.write_bytes(b'newer export')


def run(response,send,headers=(),method='GET',extensions=None):
    async def receive():return {'type':'http.disconnect'}
    asyncio.run(response({'type':'http','method':method,'headers':list(headers),
                         'extensions':extensions or {}},receive,send))


def claims():
    with SessionLocal() as s:return [r.resource for r in s.query(FileUse)]


def test_media_claim_covers_every_body_chunk_and_preserves_range_support(tmp_path):
    path=tmp_path/'export.mp4';payload=b'0123456789'*15000;path.write_bytes(payload)
    before=set(claims());messages=[]
    async def send(message):
        messages.append(message)
        if message['type']=='http.response.body':
            with pytest.raises(file_use.FileBusy):outside(lambda:replace(path))
    response=FileResponse(path)
    run(response,send,headers=[(b'range',b'bytes=10-99999')],extensions={'http.response.pathsend':{}})
    assert messages[0]['status']==206
    assert b''.join(m.get('body',b'') for m in messages)==payload[10:100000]
    assert all(m['type']!='http.response.pathsend' for m in messages)
    assert set(claims())==before
    outside(lambda:replace(path));assert path.read_bytes()==b'newer export'


def test_disconnect_releases_stream_claim_in_finally(tmp_path):
    path=tmp_path/'export.mp4';path.write_bytes(b'x'*200000)
    before=set(claims())
    async def send(message):
        if message['type']=='http.response.body':raise asyncio.CancelledError()
    with pytest.raises(asyncio.CancelledError):run(FileResponse(path),send)
    assert set(claims())==before
    outside(lambda:replace(path))


def test_expired_stream_stops_before_sending_a_second_chunk(tmp_path):
    path=tmp_path/'export.mp4';path.write_bytes(b'x'*200000);bodies=[];before=set(claims())
    async def send(message):
        if message['type']=='http.response.body':
            bodies.append(message['body'])
            with SessionLocal() as s:
                for handle in file_use._active.get():
                    s.get(WorkerLease,handle.resource).expires_at=datetime.now(timezone.utc)-timedelta(seconds=1)
                s.commit()
    with pytest.raises(LeaseLost):run(FileResponse(path),send)
    assert len(bodies)==1 and set(claims())==before


def test_missing_response_file_has_a_truthful_404_and_no_leaked_claim(tmp_path):
    before=set(claims())
    async def send(message):pytest.fail('No headers should be emitted for a missing file')
    with pytest.raises(HTTPException) as exc:run(FileResponse(tmp_path/'gone'),send)
    assert exc.value.status_code==404 and set(claims())==before


def test_head_claim_is_released_without_transferring_body(tmp_path):
    path=tmp_path/'export.mp4';path.write_bytes(b'original');messages=[];before=set(claims())
    async def send(message):messages.append(message)
    run(FileResponse(path),send,method='HEAD')
    assert messages[0]['status']==200 and messages[-1]['body']==b''
    assert set(claims())==before
