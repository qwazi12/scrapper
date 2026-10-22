"""Bounded upload retries and authoritative receipt reconciliation."""
import threading
from urllib.error import HTTPError

import pytest
from fastapi.testclient import TestClient
from scripts import archive_worker as mac
from backend.app.main import app
from backend.app import channel_archive as archive
from backend.tests.test_channel_archive import fixture,CID,VID


def error(code=500):return HTTPError('http://localhost/upload',code,'simulated',None,None)


@pytest.mark.parametrize('lost_response',[False,True])
def test_real_handoff_retries_local_bytes_or_reconciles_success_without_second_upload(fixture,tmp_path,monkeypatch,lost_response):
    client=TestClient(app);heads={'x-access-token':'test-token'};sent=[];downloads=[]
    def get(server,token,path):
        response=client.get(path,headers=heads);assert response.status_code==200,response.text
        return response.json()
    def post(server,token,path,data):
        response=client.post(path,headers=heads,json=data);assert response.status_code==200,response.text
        return response.json()
    original=mac.source.download
    def download(cid,vid,directory,bound,cancelled):
        downloads.append(vid);return original(cid,vid,directory,bound)
    def send(server,token,path,video,fields,**kwargs):
        sent.append((video,video.read_bytes()))
        if len(sent)==1 and not lost_response:raise error()
        with video.open('rb') as stream:
            response=client.post(path,headers=heads,data=fields,files={'file':('original.mp4',stream,'video/mp4')})
        assert response.status_code==200,response.text
        if len(sent)==1 and lost_response:raise error()
        return response.json()
    monkeypatch.setattr(mac,'api_get',get);monkeypatch.setattr(mac,'api_post',post)
    monkeypatch.setattr(mac,'download',download);monkeypatch.setattr(mac,'multipart_video',send)
    monkeypatch.setattr(mac,'retry_pause',lambda *args:None)
    assert mac.drain('http://localhost','test-token',CID,tmp_path/'worker',max_videos=1)==1
    assert downloads==[VID] and len(sent)==(1 if lost_response else 2)
    assert len({content for path,content in sent})==1
    assert not sent[0][0].exists() and archive.view(CID)['counts']['uploaded']==1
    assert not archive.view(CID)['active_lease']
    drive=fixture[1]
    assert len([r for r in drive.items.values() if r.get('appProperties')])==1


def invoke(tmp_path,monkeypatch,*,code=500,receipt_error=False,missing=False,cancel_on_pause=False,ledger_error=None):
    video=tmp_path/'retained.mp4';video.write_bytes(b'original');stop=threading.Event();calls=[]
    def send(*args,**kwargs):calls.append(args[3]);raise error(code)
    def get(*args):
        if receipt_error:raise error()
        return {'items':[] if missing else [{'video_id':VID,'status':'failed','error':ledger_error}]}
    def pause(*args):
        if cancel_on_pause:stop.set()
    monkeypatch.setattr(mac,'multipart_video',send);monkeypatch.setattr(mac,'api_get',get)
    monkeypatch.setattr(mac,'retry_pause',pause)
    with pytest.raises((HTTPError,RuntimeError)):
        mac.send_with_reconciliation('http://localhost','test','/upload',video,{},cid=CID,vid=VID,max_bytes=100,cancelled=stop)
    assert video.read_bytes()==b'original'
    return len(calls)


def test_retries_are_bounded_and_never_redownload_or_remove_local_file(tmp_path,monkeypatch):
    assert invoke(tmp_path,monkeypatch)==3


@pytest.mark.parametrize('code',[403,409,429,507])
def test_access_rate_limit_fencing_and_storage_errors_are_not_retried(tmp_path,monkeypatch,code):
    assert invoke(tmp_path,monkeypatch,code=code)==1


@pytest.mark.parametrize('option',[{'receipt_error':True},{'missing':True},{'cancel_on_pause':True},
                                 {'ledger_error':'Drive quota exceeded'}])
def test_unresolved_receipts_cancellation_and_quota_fail_closed(tmp_path,monkeypatch,option):
    assert invoke(tmp_path,monkeypatch,**option)==1
