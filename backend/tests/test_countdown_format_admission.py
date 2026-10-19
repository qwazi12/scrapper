"""Unsupported saved formats must not turn arbitrary counts into Top5/10/15."""
import pytest
from fastapi import HTTPException
from fastapi.testclient import TestClient
from backend.app.db import SessionLocal
from backend.app.models import CountdownProject
from backend.app.studio import countdown,countdown_eligibility


@pytest.mark.parametrize('fmt',['top3','top100','unknown'])
def test_unknown_current_format_cannot_skip_exact_entry_count(fmt,monkeypatch):
    entries=[{'rank':i,'tmdb_id':99700+i,'media_type':'movie','title':f'Synthetic Format Film {i}',
        'year':2026,'why_it_ranks':'Our editorial preference for the original visual structure of this synthetic example.'} for i in range(1,3)]
    monkeypatch.setattr(countdown_eligibility.tmdb,'fact_sheet',lambda kind,ident,**kwargs:{'tmdb_id':ident,'media_type':kind,
        'title':f'Synthetic Format Film {ident-99700}','primary_date':'2026-01-01','genres':['Animation']})
    with SessionLocal() as s:
        c=CountdownProject(topic='Synthetic animated films',fmt=fmt,ranking={'contract_version':1,'entries':entries})
        s.add(c);s.commit()
        with pytest.raises(HTTPException) as error:countdown.confirm_titles(s,c)
        assert error.value.status_code==409
        assert not c.segment_ids


def test_unsupported_legacy_format_is_readable_but_cannot_be_promoted_by_upgrade():
    from backend.app.main import app
    words={'intro':'Retain these original private words.','outro':'Keep this original closing.'}
    with SessionLocal() as s:
        c=CountdownProject(topic='Original custom format',fmt='top3',ranking={'entries':[]},script=words,stage_status='stopped')
        s.add(c);s.commit();cid=c.id
    client=TestClient(app,headers={'x-access-token':'test-token'})
    current=client.get(f'/api/countdowns/{cid}').json()
    assert current['saved_contract']=='unsupported' and current['script']==words
    result=client.post(f'/api/countdowns/{cid}/upgrade-saved-contract',json={'expected_version':current['contract_upgrade_version'],'reviewed':True})
    assert result.status_code==409,result.text
    with SessionLocal() as s:assert s.get(CountdownProject,cid).script==words


def test_retained_countdown_inputs_include_the_exact_requested_format():
    from backend.app.export_versions import data
    c=CountdownProject(topic='Original custom format',fmt='top3',ranking={'entries':[]},script={'intro':'Private original words.'})
    assert data(c)['inputs']['fmt']=='top3'


@pytest.fixture
def measured():
    from uuid import uuid4
    from backend.app.config import settings
    from backend.app.studio import media,media_quality
    root=settings.data_path/'studio'/'compilations'/uuid4().hex;root.mkdir(parents=True)
    video=root/'final.mp4'
    media.run(['ffmpeg','-v','error','-y','-f','lavfi','-i','testsrc2=s=320x180:r=24:d=1',
        '-f','lavfi','-i','sine=f=1103:d=1','-c:v','libx264','-pix_fmt','yuv420p','-c:a','aac',str(video)])
    qa=media_quality.inspect_export(video,expected_seconds=1);assert not qa['errors']
    with SessionLocal() as s:
        c=CountdownProject(topic='Synthetic format fingerprint',fmt='top5',script={'intro':'Original private fixture words.','outro':'Original closing.'},
            ranking={'contract_version':1,'entries':[]},render={'file':str(video),'compilation_id':root.name,'quality':qa})
        s.add(c);s.commit();cid=c.id
    return cid,root,video


def test_format_changes_the_measured_export_fingerprint(measured):
    from backend.app.studio.media_quality import export_fingerprint
    cid,root,video=measured
    with SessionLocal() as s:
        c=s.get(CountdownProject,cid);before=export_fingerprint(c,root)
        c.fmt='top10'
        assert export_fingerprint(c,root)!=before
        s.rollback()


def test_format_edit_retains_original_bytes_and_format_and_clears_render_acceptance(measured):
    from pathlib import Path
    from backend.app.models import ExportVersion
    cid,root,video=measured;before=video.read_bytes()
    with SessionLocal() as s:
        c=s.get(CountdownProject,cid)
        # An invalid acceptance sentinel isolates dependency cleanup; it is not
        # represented as a real owner acceptance or valid publishing proof.
        c.review={'export_approval':{'test_only':'invalid acceptance sentinel'}};s.commit()
        words=c.script;c.fmt='top10';s.commit()
        assert c.render is None and not c.review.get('export_approval') and c.script==words
        rows=s.query(ExportVersion).filter_by(kind='countdown',project_id=cid).all()
        old=next(row for row in rows if row.manifest['snapshot']['inputs']['fmt']=='top5')
        asset=next(a for a in old.manifest['assets'] if a['role']=='video')
        assert Path(asset['blob']).read_bytes()==before and video.read_bytes()==before


@pytest.mark.parametrize('fmt,count,reason',[(None,5,'format is unconfirmed'),('top5',2,'count differs'),('top5',5,'needs current acceptance')])
def test_retained_countdown_proof_cannot_infer_missing_format_or_admit_wrong_count(fmt,count,reason):
    from types import SimpleNamespace
    from backend.app import queue_sources
    project=SimpleNamespace(topic='Synthetic retained format',ranking={'contract_version':1},segment_ids=list(range(count)),review={})
    if fmt is not None:project.fmt=fmt
    with SessionLocal() as s,pytest.raises(HTTPException,match=reason):queue_sources._countdown(s,project,{})


def test_scheduler_keeps_live_unsupported_format_owner_until_its_lease_is_released(monkeypatch):
    from backend.app import worker_leases
    with SessionLocal() as s:
        c=CountdownProject(topic='Retain active unsupported work',fmt='top3',ranking={'contract_version':1,'entries':[]},stage='render',stage_status='paused')
        s.add(c);s.commit();cid=c.id
        lease=worker_leases.acquire(s,f'countdown:{cid}','synthetic-unsupported-owner');s.commit()
    calls=[];monkeypatch.setattr(countdown,'start_render',lambda cid:calls.append(cid));monkeypatch.setattr(countdown,'_budget_ok',lambda:True)
    try:
        countdown.tick(from_runner=True)
        with SessionLocal() as s:assert s.get(CountdownProject,cid).stage_status=='paused'
        assert not calls
    finally:
        with SessionLocal() as s:worker_leases.release(s,lease);s.commit()
    countdown.tick(from_runner=True)
    with SessionLocal() as s:assert s.get(CountdownProject,cid).stage_status=='stopped'
    assert not calls
