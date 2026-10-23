"""Continue inventoried media without changing or recreating uncertain intents."""
import copy
import pytest
from fastapi.testclient import TestClient
from scripts import archive_worker as mac, archive_batch as batch
from backend.app.main import app
from backend.app import channel_archive as archive
from backend.tests.test_channel_archive import fixture,CID,VID


def test_other_videos_finish_and_second_visit_does_not_redownload_or_clear_hold(fixture,tmp_path,monkeypatch):
    data,drive,downloads=fixture
    others=['b'*11,'c'*11]
    data['videos'] += [{**data['videos'][0],'video_id':vid} for vid in others]
    archive.discover(data['source_url'],'parent',['shorts'])
    archive.persist(CID,VID,status='ambiguous',error='Unknown Drive creation outcome')
    before=archive.manifest(CID)['items'][0].copy()
    client=TestClient(app);heads={'x-access-token':'test-token'}
    def get(server,token,path):
        r=client.get(path,headers=heads);assert r.status_code==200,r.text
        return r.json()
    def post(server,token,path,data):
        r=client.post(path,headers=heads,json=data);assert r.status_code==200,r.text
        return r.json()
    def send(server,token,path,video,fields,**kwargs):
        with video.open('rb') as stream:
            r=client.post(path,headers=heads,data=fields,files={'file':('synthetic.mp4',stream,'video/mp4')})
        assert r.status_code==200,r.text
        return r.json()
    original=mac.source.download
    monkeypatch.setattr(mac,'api_get',get);monkeypatch.setattr(mac,'api_post',post)
    monkeypatch.setattr(mac,'multipart_video',send)
    monkeypatch.setattr(mac,'download',lambda cid,vid,directory,bound,cancelled:original(cid,vid,directory,bound))
    assert mac.drain('http://localhost','test-token',CID,tmp_path/'worker',max_videos=25)==2
    assert mac.drain('http://localhost','test-token',CID,tmp_path/'worker',max_videos=25)==0
    assert downloads==others
    assert archive.manifest(CID)['items'][0]==before
    view=archive.view(CID)
    assert view['status']=='partial' and view['counts']['remaining']==1
    assert view['counts']['uploaded']==2 and view['counts']['failed']==1 and view['counts']['skipped']==0
    assert not view['active_lease']
    assert len([r for r in drive.items.values() if r.get('appProperties')])==2
    # A rotation with nothing but held work must end partial, not loop/complete.
    monkeypatch.setattr(batch,'api_get',get)
    result=batch.run('http://localhost','test-token',[{'channel_id':CID}],tmp_path/'worker',tmp_path/'state')
    assert result['status']=='partial' and not result['all_channels_complete']
    assert downloads==others and archive.manifest(CID)['items'][0]==before


@pytest.mark.parametrize('changed',['missing','incomplete','identity'])
def test_cannot_select_past_hold_from_stale_or_unrelated_inventory(monkeypatch,changed):
    manifest={'channel_id':CID,'inventory':{'complete':True},'counts':{'missing_from_latest_inventory':0},
              'items':[{'video_id':VID,'status':'ambiguous','drive_file_id':None}]}
    if changed=='missing':manifest['counts']['missing_from_latest_inventory']=1
    elif changed=='incomplete':manifest['inventory']['complete']=False
    else:manifest['channel_id']='UC'+'z'*22
    monkeypatch.setattr(mac,'api_get',lambda s,t,path:
        {'items':manifest['items']} if '/pending?' in path else copy.deepcopy(manifest))
    with pytest.raises(RuntimeError,match='incomplete or changed inventory'):
        mac.next_pending('http://localhost','test','/claim',{},CID)
