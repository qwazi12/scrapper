"""Delivered length must be compared with requested length, not just mux plan."""
from datetime import datetime,timezone
from types import SimpleNamespace
import pytest
from backend.app.db import SessionLocal
from backend.app.models import CountdownProject,ExportVersion
from backend.app.export_versions import data
from backend.app.studio import media_quality
from backend.app.studio.export_acceptance import view,accept,require_acceptance
from backend.tests.test_export_acceptance import produced,decision
from backend.tests.test_countdown_format_admission import measured
from backend.tests.test_queue_source_binding import reviewed_source


def test_actual_short_file_requires_explicit_requested_length_decision(produced):
    project,root=produced;project.target_minutes=3.0
    current=view(project,root)
    finding=next((row for row in current['findings'] if row['kind']=='requested_duration'),None)
    assert finding and '180' in finding['message'] and '1.0' in finding['message']
    assert current['requested_duration']['target_seconds']==180
    request=decision(project,root)
    request.warning_decisions.pop(finding['id'])
    with pytest.raises(RuntimeError,match='every current finding'):accept(project,root,request,datetime.now(timezone.utc))
    request=decision(project,root)
    approved=accept(project,root,request,datetime.now(timezone.utc))
    project.review={'export_approval':approved};require_acceptance(project,root)
    assert approved['requested_duration']['target_seconds']==180


def test_same_bytes_cannot_reuse_acceptance_after_requested_duration_changes(produced):
    project,root=produced;project.target_minutes=3.0
    request=decision(project,root);before=media_quality.export_fingerprint(project,root)
    project.review={'export_approval':accept(project,root,request,datetime.now(timezone.utc))}
    project.target_minutes=4.0
    assert media_quality.export_fingerprint(project,root)!=before
    with pytest.raises(RuntimeError,match='changed'):accept(project,root,request,datetime.now(timezone.utc))
    with pytest.raises(RuntimeError,match='approve this exact export'):require_acceptance(project,root)


def test_countdown_custom_segment_duration_is_retained_without_guessing_from_format():
    c=CountdownProject(fmt='top5',topic='Original duration intent',review={'segment_minutes':2.0})
    intent=data(c)['inputs'].get('duration_intent')
    assert intent and intent['target_seconds']==600 and intent['segment_seconds']==120
    assert intent['basis']=='requested segment total; final bookends add time'


def test_countdown_length_setting_edit_retains_old_target_bytes_and_revokes_acceptance(measured):
    from pathlib import Path
    cid,root,video=measured;original=video.read_bytes()
    with SessionLocal() as s:
        c=s.get(CountdownProject,cid);c.review={'segment_minutes':2.0};s.commit()
        # Restore the already-generated fixture after this isolated input edit.
        c.render={'file':str(video),'compilation_id':root.name,'quality':media_quality.inspect_export(video,expected_seconds=1)};s.commit()
        c.review={**c.review,'export_approval':{'test_only':'invalid sentinel'}};s.commit()
        c.review={**c.review,'segment_minutes':1.2};s.commit()
        assert c.render is None and not c.review.get('export_approval')
        versions=s.query(ExportVersion).filter_by(kind='countdown',project_id=cid).all()
        old=next(v for v in versions if (v.manifest['snapshot']['inputs'].get('duration_intent') or {}).get('target_seconds')==600)
        asset=next(a for a in old.manifest['assets'] if a['role']=='video')
        assert Path(asset['blob']).read_bytes()==original and video.read_bytes()==original


@pytest.mark.parametrize('seconds,needs_decision',[(180,False),(140,False),(220,False),(100,True),(260,True)])
def test_length_band_is_an_explicit_review_heuristic(seconds,needs_decision):
    # This pure finding test does not represent a real generated long export.
    from backend.app.studio.export_acceptance import findings
    project=SimpleNamespace(target_minutes=3.0,render={'quality':{'seconds':seconds}},plan=[],shots=[])
    assert any(row['kind']=='requested_duration' for row in findings(project)) is needs_decision


@pytest.mark.parametrize('invalid',[None,{'schema':True,'target_seconds':180},
    {'schema':1,'kind':'studio','target_seconds':'180'}, {'schema':9,'kind':'studio','target_seconds':180}])
def test_missing_or_malformed_retained_intent_never_borrows_a_live_target(reviewed_source,invalid):
    from copy import deepcopy
    from backend.app import export_versions,queue_sources
    from backend.app.models import StudioProject
    from backend.app.publication_journal import digest
    from fastapi import HTTPException
    client,pid,file,qa=reviewed_source()
    with SessionLocal() as s:
        c=s.get(StudioProject,pid);eid=export_versions.retain(s,c);s.commit()
        source=s.get(ExportVersion,eid);payload=deepcopy(source.manifest['snapshot'])
        if invalid is None:payload['inputs'].pop('duration_intent')
        else:payload['inputs']['duration_intent']=invalid
        old_id=digest([source.kind,source.project_identity,payload])
        s.add(ExportVersion(id=old_id,kind=source.kind,project_id=pid,project_identity=source.project_identity,
            manifest={**deepcopy(source.manifest),'snapshot':payload}));s.commit()
        words=c.script;c.target_minutes=4.0;s.commit()
        with pytest.raises(HTTPException,match='requested length is unconfirmed'):queue_sources.retained(s,old_id,approve=True)
        assert c.script==words and file.is_file()


def test_subminute_countdown_child_keeps_a_valid_retained_requested_length():
    # A real supported Top15 creates .6-minute Studio children, below the
    # standalone Studio creation form's minimum of one minute.
    from backend.app.models import StudioProject
    from backend.app.studio.duration_intent import valid
    child=StudioProject(target_minutes=.6)
    intent=data(child)['inputs']['duration_intent']
    assert intent['target_seconds']==36 and valid(intent,'studio')


@pytest.mark.parametrize('invalid',[0,False])
def test_invalid_explicit_saved_segment_length_cannot_inherit_default(invalid):
    c=CountdownProject(fmt='top5',topic='Retain invalid historical length',review={'segment_minutes':invalid})
    assert data(c)['inputs']['duration_intent']['target_seconds'] is None
