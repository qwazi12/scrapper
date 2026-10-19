"""Saved legacy Countdown lists cannot silently inherit current verification."""
import asyncio
from types import SimpleNamespace

import pytest
from fastapi import HTTPException
from fastapi.testclient import TestClient

from backend.app.db import SessionLocal
from backend.app.models import CountdownProject,StudioProject,QueueItem
from backend.app.studio import countdown


@pytest.mark.parametrize('version',[None,0,2,True,'1'])
def test_unconfirmed_saved_contract_cannot_bypass_bookend_admission(version):
    with SessionLocal() as s,pytest.raises(HTTPException) as error:
        countdown.require_bookends(s,SimpleNamespace(ranking={'contract_version':version}))
    assert error.value.status_code==409


@pytest.fixture
def legacy():
    from backend.app.main import app
    with SessionLocal() as s:
        p=StudioProject(tmdb_id=99600,title='Original unrelated source',facts={'private':'retain'})
        s.add(p);s.flush()
        c=CountdownProject(topic='Top 5 Synthetic Animated Films',fmt='top5',stage='segments',stage_status='stopped',
            ranking={'entries':[{'rank':1,'tmdb_id':99600,'media_type':'movie','title':p.title,'year':2026},
                {'rank':2,'tmdb_id':99601,'media_type':'movie','title':'Second private legacy entry','year':2026}]},
            script={'title':'Original private title','intro':'Original private introduction.','outro':'Original private closing words.'},
            segment_ids=[p.id],review={'legacy_source_note':'Keep private note'})
        s.add(c);s.commit();cid,pid=c.id,p.id
    return TestClient(app,headers={'x-access-token':'test-token'}),cid,pid


def test_legacy_confirm_and_make_refuse_before_creating_or_starting_segments(legacy,monkeypatch):
    client,cid,pid=legacy;calls=[]
    monkeypatch.setattr(countdown,'tick',lambda *a,**k:calls.append('tick'))
    for action in ('confirm','make'):
        response=client.post(f'/api/countdowns/{cid}/{action}')
        assert response.status_code==409,response.text
        assert 'saved' in response.text.lower() or 'legacy' in response.text.lower()
    assert not calls
    with SessionLocal() as s:
        c=s.get(CountdownProject,cid);assert c.segment_ids==[pid]
        assert s.get(StudioProject,pid).facts=={'private':'retain'}


def test_paused_legacy_render_is_not_automatically_resumed_by_scheduler(legacy,monkeypatch):
    client,cid,pid=legacy;calls=[]
    with SessionLocal() as s:
        c=s.get(CountdownProject,cid);c.stage='render';c.stage_status='paused';s.commit()
    monkeypatch.setattr(countdown,'_budget_ok',lambda:True)
    monkeypatch.setattr(countdown,'start_render',lambda cid:calls.append(cid))
    countdown.tick(from_runner=True)
    assert not calls
    with SessionLocal() as s:
        c=s.get(CountdownProject,cid);assert c.stage_status=='stopped'


def test_historical_singular_countdown_source_alias_is_not_a_manual_queue_bypass(tmp_path,monkeypatch):
    from backend.app.social import queue_manager as qm,upload_post
    file=tmp_path/'opaque-legacy-countdown.mp4';file.write_bytes(b'isolated opaque fixture, not media quality evidence')
    with SessionLocal() as s:
        item=QueueItem(title='Legacy singular alias',source='Countdown Studio',pipeline='Countdowns',
            video_path=str(file),status='ready',accounts=['default:youtube'],research={'matched':True})
        s.add(item);s.commit();qid=item.id
    calls=[]
    async def accounts():return [{'id':'default:youtube','profile':'default'}]
    async def submit(*a,**k):calls.append('submit');return 'synthetic-must-not-submit'
    monkeypatch.setattr(upload_post,'list_accounts',accounts);monkeypatch.setattr(upload_post,'upload_video',submit)
    with SessionLocal() as s,pytest.raises(upload_post.UploadPostError) as error:asyncio.run(qm.publish_queue_item(qid,s))
    assert error.value.status_code==409 and not calls
    with SessionLocal() as s:s.delete(s.get(QueueItem,qid));s.commit()


def test_explicit_upgrade_preserves_private_words_and_old_media_without_adopting_segments(legacy):
    from pathlib import Path
    from backend.app.config import settings
    from backend.app.models import ExportVersion
    from backend.app.studio import media
    client,cid,pid=legacy
    root=settings.data_path/'studio'/'compilations'/f'legacy-{cid}';root.mkdir(parents=True,exist_ok=True)
    video=root/'final.mp4'
    media.run(['ffmpeg','-v','error','-y','-f','lavfi','-i','testsrc2=s=320x180:r=24:d=1',
        '-f','lavfi','-i','sine=f=1201:d=1','-c:v','libx264','-pix_fmt','yuv420p','-c:a','aac',str(video)])
    before_bytes=video.read_bytes()
    with SessionLocal() as s:
        c=s.get(CountdownProject,cid);c.render={'file':str(video),'compilation_id':f'legacy-{cid}'}
        c.review={**c.review,'bookend_review':{'version':'old'},'export_approval':{'fingerprint':'old'}};s.commit()
        words=c.script;ranking=c.ranking
    view=client.get(f'/api/countdowns/{cid}').json()
    assert view['saved_contract']=='legacy' and not view['bookend_reviewed']
    assert client.post(f'/api/countdowns/{cid}/upgrade-saved-contract',json={'expected_version':view['contract_upgrade_version'],'reviewed':False}).status_code==409
    response=client.post(f'/api/countdowns/{cid}/upgrade-saved-contract',json={'expected_version':view['contract_upgrade_version'],'reviewed':True})
    assert response.status_code==200,response.text
    result=response.json();assert result['saved_contract']=='current' and result['script']==words
    assert not result['has']['titles'] and not result['has']['render'] and not result['bookend_reviewed']
    assert result['ranking']['entries']==ranking['entries']
    assert result['review']['legacy_source_note']=='Keep private note' and not result['review'].get('export_approval')
    with SessionLocal() as s:
        assert s.get(StudioProject,pid).facts=={'private':'retain'}
        rows=s.query(ExportVersion).filter_by(kind='countdown',project_id=cid).all();assert rows
        old=next(row for row in rows if row.manifest['snapshot']['inputs']['script']==words)
        asset=next(a for a in old.manifest['assets'] if a['role']=='video')
        assert Path(asset['blob']).read_bytes()==before_bytes and video.read_bytes()==before_bytes
    # Upgrade makes a draft: it cannot bless a two-entry Top5 or old approval.
    assert client.post(f'/api/countdowns/{cid}/confirm').status_code==409
    assert client.post(f'/api/countdowns/{cid}/upgrade-saved-contract',json={'expected_version':view['contract_upgrade_version'],'reviewed':True}).status_code==409


def test_upgrade_version_refuses_concurrent_words_and_unknown_future_schema(legacy):
    client,cid,pid=legacy;view=client.get(f'/api/countdowns/{cid}').json()
    with SessionLocal() as s:
        c=s.get(CountdownProject,cid);c.script={**c.script,'intro':'Newer exact private words.'};s.commit()
    body={'expected_version':view['contract_upgrade_version'],'reviewed':True}
    assert client.post(f'/api/countdowns/{cid}/upgrade-saved-contract',json=body).status_code==409
    with SessionLocal() as s:
        c=s.get(CountdownProject,cid);assert c.script['intro']=='Newer exact private words.'
        c.ranking={**c.ranking,'contract_version':2};s.commit()
    view=client.get(f'/api/countdowns/{cid}').json();assert view['saved_contract']=='unsupported'
    assert client.post(f'/api/countdowns/{cid}/upgrade-saved-contract',json={**body,'expected_version':view['contract_upgrade_version']}).status_code==409


@pytest.fixture
def confirmed(monkeypatch):
    from backend.app.studio import countdown_eligibility
    def facts(kind,ident,**kwargs):
        return {'tmdb_id':ident,'media_type':kind,'title':f'Synthetic Legacy Recovery Film {ident}',
            'primary_date':'2026-01-01','genres':['Animation'],'source':'synthetic://original-category-record'}
    monkeypatch.setattr(countdown_eligibility.tmdb,'fact_sheet',facts)
    entries=[{'rank':index,'tmdb_id':99610+index,'media_type':'movie','title':facts('movie',99610+index)['title'],
        'year':2026,'why_it_ranks':'Our editorial preference for the original visual structure of this synthetic example.'} for index in range(1,6)]
    with SessionLocal() as s:
        c=CountdownProject(topic='Top 5 Synthetic Animated Films',fmt='top5',ranking={'contract_version':1,'entries':entries})
        s.add(c);s.commit();cid=c.id
        countdown.confirm_titles(s,c);s.commit();ids=list(c.segment_ids)
    return cid,ids,facts


def test_direct_segment_runner_refuses_legacy_before_rewriting_words_or_paid_work(legacy,monkeypatch):
    from backend.app.studio import runner
    client,cid,pid=legacy;calls=[]
    with SessionLocal() as s:
        p=s.get(StudioProject,pid);p.script={'sentences':[{'text':'Retain my original private segment words.'}]};s.commit()
    monkeypatch.setitem(runner.STAGES,'script',lambda pid:calls.append(pid))
    with pytest.raises(HTTPException):runner.run_one(pid,'script')
    assert not calls
    with SessionLocal() as s:assert s.get(StudioProject,pid).script['sentences'][0]['text']=='Retain my original private segment words.'


def test_current_segment_can_gather_then_advance_without_requiring_other_children_gathered(confirmed,monkeypatch):
    from backend.app.studio import runner
    cid,ids,facts=confirmed;calls=[]
    def gather(pid):
        with SessionLocal() as s:
            p=s.get(StudioProject,pid);p.facts={**p.facts,**facts('movie',p.tmdb_id)};s.commit()
        calls.append(('gather',pid));return 'Synthetic no-provider gather'
    monkeypatch.setitem(runner.STAGES,'gather',gather)
    monkeypatch.setitem(runner.STAGES,'trailer',lambda pid:calls.append(('trailer',pid)) or 'Synthetic no-provider trailer')
    runner.run_one(ids[0],'gather');runner.run_one(ids[0],'trailer')
    assert calls==[('gather',ids[0]),('trailer',ids[0])]
    with SessionLocal() as s:assert not s.get(StudioProject,ids[1]).facts.get('primary_date')


def test_parent_creation_identity_reuse_does_not_adopt_old_segments(confirmed):
    from datetime import timedelta
    cid,ids,facts=confirmed
    with SessionLocal() as s:
        c=s.get(CountdownProject,cid);before=s.get(StudioProject,ids[0]).facts
        c.created_at=c.created_at+timedelta(seconds=1);s.commit()
        with pytest.raises(HTTPException,match='context changed'):countdown.require_segment_context(s,s.get(StudioProject,ids[0]),'gather')
        countdown.confirm_titles(s,c);s.commit()
        assert not set(ids).intersection(c.segment_ids)
        assert s.get(StudioProject,ids[0]).facts==before


def test_from_breakdowns_is_an_unconfirmed_current_draft_without_adopting_media(legacy):
    client,cid,pid=legacy
    with SessionLocal() as s:
        p=StudioProject(tmdb_id=99699,title='Second private source',facts={'primary_date':'2026-01-01'})
        s.add(p);s.commit();second=p.id
    response=client.post('/api/countdowns/from-breakdowns',json={'topic':'New explicit editorial draft','project_ids':[pid,second]})
    assert response.status_code==200,response.text
    value=response.json();assert value['saved_contract']=='current'
    assert value['entry_count']==2 and not value['has']['titles'] and not value['has']['render']
    assert [e['tmdb_id'] for e in value['ranking']['entries']]==[99600,99699]
    assert client.post(f"/api/countdowns/{value['id']}/confirm").status_code==409


def test_upgrade_refuses_active_segment_lease_without_changing_saved_words(legacy):
    from backend.app import worker_leases
    client,cid,pid=legacy;view=client.get(f'/api/countdowns/{cid}').json()
    with SessionLocal() as s:
        lease=worker_leases.acquire(s,f'studio:{pid}','synthetic-active-child');s.commit()
    try:
        response=client.post(f'/api/countdowns/{cid}/upgrade-saved-contract',json={'expected_version':view['contract_upgrade_version'],'reviewed':True})
        assert response.status_code==409,response.text
        current=client.get(f'/api/countdowns/{cid}').json();assert current['script']==view['script'] and current['saved_contract']=='legacy'
    finally:
        with SessionLocal() as s:worker_leases.release(s,lease);s.commit()


def test_paused_legacy_research_is_not_automatically_replaced(legacy,monkeypatch):
    client,cid,pid=legacy;calls=[]
    with SessionLocal() as s:
        c=s.get(CountdownProject,cid);c.stage='research';c.stage_status='paused';s.commit()
    monkeypatch.setattr(countdown,'_budget_ok',lambda:True)
    monkeypatch.setattr(countdown,'start_research',lambda cid:calls.append(cid))
    countdown.tick(from_runner=True)
    assert not calls
    with SessionLocal() as s:assert s.get(CountdownProject,cid).stage_status=='stopped'
