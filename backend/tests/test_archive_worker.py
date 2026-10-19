"""Claim fencing and media/Drive receipt handoff; external services simulated."""
from pathlib import Path
import shutil

from fastapi.testclient import TestClient
import pytest

from backend.app.main import app
from backend.app import channel_archive as archive,worker_leases as leases
from backend.app.db import SessionLocal
from backend.app.models import ArchiveWorkerClaim,WorkerLease
from backend.tests.test_channel_archive import fixture,CID,VID

HEAD={'x-access-token':'test-token'}
WORKER='original-worker'


def claim(client):
    response=client.post(f'/api/archive-workers/channels/{CID}/claim',headers=HEAD,json={'worker_id':WORKER})
    assert response.status_code==200,response.text
    return response.json()


def auth(row):return {'worker_id':WORKER,'generation':row['generation']}


def upload(client,row,path):
    import json
    with path.open('rb') as stream:
        return client.post(f"/api/archive-workers/{row['token']}/videos/{VID}",headers=HEAD,
            data={**auth(row),'metadata':json.dumps({'id':VID,'channel_id':CID,'duration':.3})},files={'file':('original.mp4',stream,'video/mp4')})


def original(fixture,tmp_path):
    from backend.app import channel_sources
    path,_=channel_sources.download(CID,VID,tmp_path/'worker',10**7)
    return path


def test_mac_claim_receipt_upload_and_finish(fixture,tmp_path):
    client=TestClient(app);row=claim(client);path=original(fixture,tmp_path)
    base='/api/archive-workers/'+row['token']
    assert client.post(base+'/renew',headers=HEAD,json=auth(row)).status_code==200
    assert client.get(base+'/pending',headers=HEAD,params=auth(row)).json()['items'][0]['video_id']==VID
    response=upload(client,row,path);assert response.status_code==200,response.text
    assert response.json()['verified'] and response.json()['drive_file_id']
    assert not archive.local_path(CID,VID)
    assert client.get(base+'/pending',headers=HEAD,params=auth(row)).json()['items']==[]
    assert client.post(base+'/finish',headers=HEAD,json=auth(row)).status_code==200
    assert client.get(base,headers=HEAD).json()['status']=='done'
    assert archive.view(CID)['status']=='complete'


def test_server_job_and_two_macs_cannot_claim_same_channel(fixture):
    client=TestClient(app);row=claim(client)
    response=client.post(f'/api/archive-workers/channels/{CID}/claim',headers=HEAD,json={'worker_id':'second-worker'})
    assert response.status_code==409
    with pytest.raises(leases.LeaseLost):
        with leases.scope('archive:'+CID):pass
    assert client.post('/api/archive-workers/'+row['token']+'/release',headers=HEAD,json=auth(row)).status_code==200


def test_ui_stop_fences_stale_upload_and_renewal(fixture,tmp_path):
    client=TestClient(app);row=claim(client);path=original(fixture,tmp_path)
    assert archive.view(CID)['active_lease']
    assert client.post(f'/api/channel-archives/{CID}/stop',headers=HEAD).status_code==200
    assert upload(client,row,path).status_code==409
    base='/api/archive-workers/'+row['token']
    assert client.post(base+'/renew',headers=HEAD,json=auth(row)).status_code==409
    newer=claim(client);assert newer['generation']>row['generation']


def test_challenge_record_is_handoff_and_private_item_is_skipped(fixture):
    client=TestClient(app);row=claim(client)
    endpoint=f"/api/archive-workers/{row['token']}/videos/{VID}/failure"
    payload={**auth(row),'reason':'Sign in to confirm not a bot','restricted':True,'halt':True}
    assert client.post(endpoint,headers=HEAD,json=payload).status_code==200
    assert archive.view(CID)['items'][0]['status']=='awaiting_worker'
    payload.update(reason='Private video',halt=False)
    assert client.post(endpoint,headers=HEAD,json=payload).status_code==200
    assert archive.view(CID)['counts']['skipped']==1


def test_local_inventory_validates_identities_and_never_queues(fixture):
    data,_,_=fixture;client=TestClient(app)
    payload={**data,'parent_folder_id':'parent','formats':['shorts']}
    assert client.post('/api/archive-workers/inventory',headers=HEAD,json=payload).status_code==200
    payload['videos'][0]['video_id']='../../bad'
    assert client.post('/api/archive-workers/inventory',headers=HEAD,json=payload).status_code==422
