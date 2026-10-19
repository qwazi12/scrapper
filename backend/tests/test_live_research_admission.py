"""Cached storyboard green never substitutes for current typed-fact admission."""
from copy import deepcopy
from types import SimpleNamespace

from fastapi.testclient import TestClient
import pytest

from backend.app.db import SessionLocal
from backend.app.main import app
from backend.app.models import StudioProject
from backend.app.studio import countdown,runner,stage_plan,stage_render

FACTS={'title':'Synthetic Horizon','media_type':'movie','primary_date':'2026-01-01',
       'cast':[{'actor':'Ada Example','character':'The Traveler'}]}
WORDS='Invented Performer plays The Traveler in Synthetic Horizon.'


@pytest.fixture
def cached():
    script={'sentences':[{'text':WORDS,'paragraph':1,'check':'ok','refs':['F1']}],
        'check_issues':[],'storyboard':{'F':['Canonical synthetic credit'],'R':[]},'check_scope':'storyboard_only'}
    with SessionLocal() as s:
        row=StudioProject(tmdb_id=98877,title=FACTS['title'],facts=deepcopy(FACTS),script=script,
            plan={'retained':'stale accepted fixture'},stage_status='done')
        s.add(row);s.commit();pid=row.id
    return pid,script


def test_readiness_rechecks_cached_green_against_current_facts_without_rewriting_it(cached):
    pid,original=cached;client=TestClient(app,headers={'x-access-token':'test-token'})
    result=client.get(f'/api/studio/projects/{pid}')
    assert result.status_code==200,result.text
    data=result.json();assert data['script']==original
    assert data['script_ready'] is False
    assert any('structured cast relationship' in issue for issue in data['research_issues'])


@pytest.mark.parametrize('work',[stage_plan.plan,stage_render.render],ids=['narration-plan','render'])
def test_cached_green_stops_before_media_or_paid_work(cached,work):
    with pytest.raises(RuntimeError,match='structured cast relationship'):work(cached[0])


def test_countdown_fingerprints_recheck_each_cached_segment(cached):
    with SessionLocal() as s,pytest.raises(RuntimeError,match='structured cast relationship'):
        countdown.segment_fingerprints(s,SimpleNamespace(segment_ids=[cached[0]]))


def test_owner_approval_and_queue_admission_recheck_cached_green(cached):
    pid,original=cached;client=TestClient(app,headers={'x-access-token':'test-token'})
    root=runner.project_dir(pid);root.mkdir(parents=True,exist_ok=True)
    (root/'opaque-test-only.mp4').write_bytes(b'Synthetic opaque admission fixture, not a rendered-media proof')
    with SessionLocal() as s:
        row=s.get(StudioProject,pid);row.render={'file':'opaque-test-only.mp4'};s.commit()
    response=client.post(f'/api/studio/projects/{pid}/approve-export',json={'expected_fingerprint':'0'*64,
        'checks':['captions','visuals','rights','narration'],'warning_decisions':{},
        'rights_rationale':'Original isolated fixture; admission should reject factual conflict before acceptance.', 'reviewed':True})
    assert response.status_code==409,response.text
    assert 'structured cast relationship' in response.text
    response=client.post(f'/api/studio/projects/{pid}/publish')
    assert response.status_code==409,response.text
    assert 'structured cast relationship' in response.text
    assert client.get(f'/api/studio/projects/{pid}').json()['script']==original
