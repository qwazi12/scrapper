"""Editorial decisions bind actual inspected media and every current finding."""
from datetime import datetime,timezone
from types import SimpleNamespace

import pytest
from fastapi.testclient import TestClient

from backend.app.main import app
from backend.app.studio import media_quality as quality
from backend.app.studio.export_acceptance import AcceptanceIn,CHECKS,accept,view
from backend.tests.test_upload_quality import make_video


@pytest.fixture
def produced(tmp_path):
    make_video(tmp_path/'final.mp4')
    qa=quality.inspect_export(tmp_path/'final.mp4',expected_seconds=1)
    project=SimpleNamespace(render={'file':'final.mp4','quality':qa},script={'sentences':['original fixture']},
        plan=[{'slot':1,'shot':'original'},{'slot':2,'shot':'original'}],shots=[{'id':'original'}],review={},
        trailer={'url':'https://example.invalid/original-cleared-fixture'},created_at=datetime.now(timezone.utc))
    return project,tmp_path


def decision(project,root):
    current=view(project,root)
    return AcceptanceIn(expected_fingerprint=current['fingerprint'],checks=list(CHECKS),
        warning_decisions={row['id']:'Intentional synthetic fixture after inspection of the exact frames and audio.' for row in current['findings']},
        rights_rationale='All footage and audio in this fixture was originally generated for these tests.',reviewed=True)


def test_no_legacy_single_click_approval_and_every_warning_requires_a_decision(produced):
    p,root=produced
    p.review={'export_approval':{'fingerprint':quality.export_fingerprint(p,root)}}
    with pytest.raises(RuntimeError,match='editorial acceptance'):quality.require_export_approval(p,root)
    current=view(p,root)
    assert any(row['kind']=='visual_repetition' for row in current['findings'])
    request=decision(p,root);request.warning_decisions={}
    with pytest.raises(RuntimeError,match='every current finding'):accept(p,root,request,datetime.now(timezone.utc))
    request=decision(p,root);request.checks=['rights']*4
    with pytest.raises(RuntimeError,match='each editorial'):accept(p,root,request,datetime.now(timezone.utc))
    request=decision(p,root);request.warning_decisions[next(iter(request.warning_decisions))]='fine'
    with pytest.raises(RuntimeError,match='substantive'):accept(p,root,request,datetime.now(timezone.utc))


def test_approval_binds_sources_inspection_and_exact_original_input(produced):
    p,root=produced
    request=decision(p,root)
    approved=accept(p,root,request,datetime.now(timezone.utc));p.review={'export_approval':approved}
    quality.require_export_approval(p,root)
    assert approved['rights_basis']=='owner_attested' and approved['rights_verified_by_app'] is False
    p.trailer={**p.trailer,'url':'https://example.invalid/different-rights'}
    with pytest.raises(RuntimeError,match='changed'):accept(p,root,request,datetime.now(timezone.utc))
    with pytest.raises(RuntimeError,match='approve this exact export'):quality.require_export_approval(p,root)
    p.trailer={'url':'https://example.invalid/original-cleared-fixture'}
    p.render['quality']['warnings']=['new inspection warning']
    with pytest.raises(RuntimeError,match='changed'):accept(p,root,request,datetime.now(timezone.utc))


def test_private_review_routes_require_owner_and_post_requires_decision_body():
    client=TestClient(app)
    for prefix in ('/api/studio/projects/1','/api/countdowns/1'):
        assert client.get(prefix+'/export-review').status_code==401
        assert client.post(prefix+'/approve-export',json={}).status_code==401
        assert client.post(prefix+'/approve-export',headers={'x-access-token':'test-token'}).status_code==422


def test_actual_black_and_silence_spans_have_timestamps(tmp_path):
    from backend.app.studio import media
    path=tmp_path/'intentional-black-silence.mp4'
    media.run(['ffmpeg','-v','error','-y','-f','lavfi','-i','color=black:s=320x180:d=4',
        '-f','lavfi','-i','anullsrc=r=48000:cl=mono','-t','4','-c:v','libx264','-c:a','aac',str(path)])
    qa=quality.inspect_export(path,expected_seconds=4)
    assert qa['black_spans'][0]['start']==0 and qa['black_spans'][0]['end']>3.8
    assert qa['silence_spans'][0]['start']==0 and qa['silence_spans'][0]['end']>=4
