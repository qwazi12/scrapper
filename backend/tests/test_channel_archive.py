"""Original tiny media, real validation/ledger; Drive/YouTube are simulated."""
import hashlib
import json
from pathlib import Path
import shutil
import subprocess

import pytest
from fastapi.testclient import TestClient

from backend.app import channel_archive as archive, channel_sources as source, worker_leases as leases
from backend.app.db import SessionLocal
from backend.app.main import app
from backend.app.models import ArchiveChannel, ArchiveVideo, QueueItem

CID='UC'+'a'*22
VID='a'*11


from scripts.e2e.fake_archive_drive import Drive


@pytest.fixture
def fixture(monkeypatch,tmp_path):
    with SessionLocal() as s:
        s.query(ArchiveVideo).delete();s.query(ArchiveChannel).delete();s.commit()
    asset=tmp_path/'original.mp4'
    subprocess.run(['ffmpeg','-v','error','-f','lavfi','-i','color=c=blue:s=64x96:r=10:d=0.3',
        '-c:v','libx264','-pix_fmt','yuv420p',str(asset)],check=True)
    calls=[];drive=Drive()
    data={'channel_id':CID,'title':'Original Fixture','handle':'@fixture','source_url':'https://www.youtube.com/@fixture',
        'complete':True,'videos':[{'video_id':VID,'title':'First title','media_kind':'shorts',
            'original_url':'https://www.youtube.com/watch?v='+VID,'estimated_bytes':asset.stat().st_size}]}
    monkeypatch.setattr(source,'inventory',lambda *a:data)
    def download(cid,vid,dest,bound):
        calls.append(vid);dest.mkdir(parents=True,exist_ok=True);p=dest/(vid+'.mp4');shutil.copyfile(asset,p)
        return p,{'id':vid,'channel_id':cid,'duration':.3,'upload_date':'20261018'}
    monkeypatch.setattr(source,'download',download)
    from backend.app import drive_sync
    monkeypatch.setattr(drive_sync,'get_drive_service',lambda:drive)
    monkeypatch.setattr(archive.shutil,'disk_usage',lambda p:shutil._ntuple_diskusage(10**12,0,10**12))
    archive.discover(data['source_url'],'parent',['shorts'])
    return data,drive,calls


def run(retry=False):
    with leases.scope('archive:'+CID):return archive.run(CID,retry)


def test_actual_bytes_validate_upload_cleanup_and_title_rename_dedup(fixture):
    data,drive,calls=fixture
    with SessionLocal() as s:before=s.query(QueueItem).count()
    result=run();assert result['counts']['uploaded']==1 and result['counts']['downloaded']==1
    item=result['items'][0];remote=drive.items[item['drive_file_id']]
    assert remote['bytes'] and remote['md5Checksum']==item['md5'] and VID in remote['name']
    assert not archive.local_path(CID,VID)
    original_name=remote['name'];updates=drive.media_updates
    data['videos'][0]['title']='Renamed descriptive title';archive.discover(data['source_url'],'parent',['shorts'])
    assert run()['counts']['uploaded']==1 and calls==[VID]
    assert remote['name']==original_name and drive.media_updates==updates+1 # manifest only
    assert archive.manifest(CID)['items'][0]['title']=='Renamed descriptive title'
    with SessionLocal() as s:assert s.query(QueueItem).count()==before


@pytest.mark.parametrize('fault',['fail_create','fail_transfer'])
def test_unknown_remote_outcome_reconciles_without_duplicate_or_redownload(fixture,fault):
    _,drive,calls=fixture;setattr(drive,fault,True)
    with pytest.raises(TimeoutError):run()
    assert archive.local_path(CID,VID).is_file()
    assert run(True)['counts']['uploaded']==1 and calls==[VID]
    assert archive.local_path(CID,VID) is None
    media=[r for r in drive.items.values() if r.get('appProperties')]
    assert len(media)==1


def test_remote_checksum_failure_preserves_local_and_nonmatching_remote(fixture):
    _,drive,_=fixture;drive.corrupt=True
    with pytest.raises(ValueError,match='checksum'):run()
    assert archive.local_path(CID,VID).is_file()
    with pytest.raises(ValueError,match='checksum'):run(True)
    assert len([r for r in drive.items.values() if r.get('appProperties')])==1
    assert archive.view(CID)['counts']['uploaded']==0


def test_unknown_creation_no_match_fails_closed_even_after_local_loss(fixture):
    _,drive,calls=fixture;drive.fail_create=True
    with pytest.raises(TimeoutError):run()
    fid=next(k for k,v in drive.items.items() if v.get('appProperties'));drive.items.pop(fid)
    archive.local_path(CID,VID).unlink()
    with pytest.raises(ValueError,match='Unknown Drive creation'):run(True)
    assert len([r for r in drive.items.values() if r.get('appProperties')])==0
    assert len(calls)==2


def test_full_inventory_has_no_25_or_50_cap_and_preserves_deleted_history(fixture):
    data,_,_=fixture
    data['videos']=[{**data['videos'][0],'video_id':str(n).zfill(11)} for n in range(126)]
    assert archive.discover(data['source_url'],'parent',['shorts'])['counts']['discovered']==127
    view=archive.view(CID);assert len(view['items'])==100 and view['has_more']
    assert view['counts']['missing_from_latest_inventory']==1
    assert len(archive.manifest(CID)['items'])==127


def test_channel_tab_adapter_never_sets_a_playlist_limit(monkeypatch):
    args=[]
    def extract(command,timeout=600):
        args.extend(command)
        return json.dumps({'channel_id':CID,'channel':'Fixture','entries':[{'id':str(n).zfill(11)} for n in range(151)]})
    monkeypatch.setattr(source,'_execute',extract)
    result=source.inventory('https://youtube.com/@fixture?si=removed',['shorts'])
    assert len(result['videos'])==151 and result['complete']
    assert '--playlist-end' not in args and '--playlist-items' not in args
    assert args[-1]=='https://www.youtube.com/@fixture/shorts'


@pytest.mark.parametrize('url',['http://youtube.com/@x','https://youtube.com.evil/@x','https://evil/@x',
    'https://www.youtube.com/watch?v=aaaaaaaaaaa','https://user:pass@youtube.com/@x'])
def test_nonchannel_and_unsafe_source_urls_rejected(url):
    with pytest.raises(ValueError):source.canonical_input(url)


def test_api_is_authenticated_and_paginates_manifest(fixture):
    client=TestClient(app)
    assert client.get('/api/channel-archives').status_code==401
    headers={'X-Access-Token':'test-token'}
    assert client.get('/api/channel-archives',headers=headers).json()['channels'][0]['channel_id']==CID
    assert client.get(f'/api/channel-archives/{CID}/capacity',headers=headers).json()['drive_available_bytes']>0
    assert client.get(f'/api/channel-archives/{CID}/manifest',headers=headers).json()['schema_version']==1


def test_zero_quota_stops_before_downloading_any_media(fixture,monkeypatch):
    _,drive,calls=fixture
    original=drive.get
    from scripts.e2e.fake_archive_drive import Reply
    def get(**kw):
        if 'fileId' not in kw:return Reply(lambda:{'storageQuota':{'limit':'0','usage':'0'}})
        return original(**kw)
    monkeypatch.setattr(drive,'get',get)
    with pytest.raises(ValueError,match='zero available'):run()
    assert calls==[] and not any(r.get('appProperties') for r in drive.items.values())


def test_archive_remote_receipts_use_immutable_shared_evidence_journal(fixture):
    from backend.app.models import RemoteCopyEvidence
    run()
    with SessionLocal() as s:
        rows=s.query(RemoteCopyEvidence).filter_by(resource=f'ArchiveVideo:{CID}:{VID}').all()
        assert {'remote_creation_receipt','verified_transfer_receipt'} <= {r.action for r in rows}
        proof=next(r.evidence['verification'] for r in rows if r.action=='verified_transfer_receipt')
        assert proof['sha256'] and proof['remote_md5'] and proof['bytes']>0
