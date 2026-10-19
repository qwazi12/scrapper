"""Fair channel rotation; the real media/receipt protocol is tested separately."""
import copy
import json

from scripts import archive_batch as batch


def test_rotates_25_per_channel_then_returns_for_remaining_videos(tmp_path,monkeypatch):
    ids=['UC'+letter*22 for letter in 'abc']
    rows=[{'channel_id':cid,'handle':'@'+str(i)} for i,cid in enumerate(ids)]
    channels={cid:{'channel_id':cid,'inventory':{'complete':i<2},'uploaded_bytes':0,
                  'counts':{'discovered':26 if i<2 else 0,'uploaded':0,'downloaded':0,
                            'skipped':0,'failed':0,'remaining':26 if i<2 else 0}}
              for i,cid in enumerate(ids)}
    calls=[];syncs=[]

    def get(server,token,path):
        if path=='/api/channel-archives':return {'channels':copy.deepcopy(list(channels.values()))}
        assert path.endswith('?limit=1')
        return copy.deepcopy(channels[path.split('/')[-1].split('?')[0]])

    def drain(server,token,cid,workdir,*,max_videos):
        count=min(max_videos,channels[cid]['counts']['remaining'])
        calls.append((cid,count))
        channels[cid]['counts']['remaining']-=count
        channels[cid]['counts']['uploaded']+=count
        channels[cid]['counts']['downloaded']+=count
        return count

    monkeypatch.setattr(batch,'api_get',get)
    monkeypatch.setattr(batch.worker,'drain',drain)
    monkeypatch.setattr(batch,'sync_review',lambda s,t,f:syncs.append(f) or {'status':'done'})
    result=batch.run('http://localhost','test',rows,tmp_path/'media',tmp_path/'state',
                     batch_size=25,start_after=ids[0],sync_folder='explicit-folder')
    assert calls==[(ids[1],25),(ids[0],25),(ids[1],1),(ids[0],1)]
    assert syncs==['explicit-folder']*4
    assert result['totals']['uploaded']==52 and result['totals']['remaining']==0
    assert result['status']=='partial' and not result['all_channels_complete']
    assert ids[2] in result['inventory_unavailable']
    assert json.loads((tmp_path/'state/batch-state.json').read_text())['status']=='partial'


def test_explicit_queue_sync_never_auto_approves(monkeypatch):
    sent=[]
    monkeypatch.setattr(batch,'api_post',lambda s,t,p,data:sent.append((p,data)) or {'job_id':'job'})
    monkeypatch.setattr(batch,'api_get',lambda *args:{'status':'done','result':{'added':1}})
    assert batch.sync_review('http://localhost','test','chosen-folder')['status']=='done'
    assert sent==[('/api/drive/sync',{'folder_id':'chosen-folder','pipeline':'Movie Clips','auto_approve':False})]


def test_revoked_lease_stops_rotation_without_starting_next_channel(tmp_path,monkeypatch):
    ids=['UC'+letter*22 for letter in 'ab']
    channels=[{'channel_id':cid,'inventory':{'complete':True},'uploaded_bytes':0,
               'counts':{'discovered':1,'uploaded':0,'downloaded':0,'skipped':0,'failed':0,'remaining':1}}
              for cid in ids]
    calls=[]
    monkeypatch.setattr(batch,'api_get',lambda s,t,p:
                        {'channels':channels} if p=='/api/channel-archives' else channels[0])
    def stopped(server,token,cid,*args,**kwargs):
        calls.append(cid);raise RuntimeError('Archive lease lost; local files retained')
    monkeypatch.setattr(batch.worker,'drain',stopped)
    monkeypatch.setattr(batch,'sync_review',lambda *args: (_ for _ in ()).throw(AssertionError('No sync after owner Stop')))
    result=batch.run('http://localhost','test',[{'channel_id':cid} for cid in ids],tmp_path/'media',
                     tmp_path/'state',sync_folder='explicit-folder')
    assert result['status']=='stopped' and calls==ids[:1]
