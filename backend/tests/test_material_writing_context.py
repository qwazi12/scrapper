"""Synthetic writing roles must bind complete credited people and title context."""
import pytest
from backend.app.studio.research_gate import structured_conflicts

FACTS={'title':'Synthetic Horizon','original_title':'Original Synthetic Horizon',
    'media_type':'movie','primary_date':'2026-01-01','writing_credits':[
        {'name':'Ada Example','job':'Screenplay'},{'name':'Bea E. Fixture','job':'Writer'},
        {'name':'Dee Sample','job':'Novel'},{'name':'Eve Original','job':'Story'}]}


@pytest.mark.parametrize('text',[
    'The screenplay was written by Invented Writer.',
    'Invented Writer wrote the screenplay.',
    'Invented Writer penned the film.',
    'The script is by Invented Writer.',
    'This movie was written by Invented Writer.',
    'The teleplay was written by Dee Sample.',
    'The screenplay was written by Ada Example and Invented Writer.',
    'Ada Example and Invented Writer wrote the screenplay.',
    'Invented Writer, Ada Example wrote the screenplay.',
    'The screenplay was written by Ada Example in Other Story.',
    'Ada Example wrote the screenplay for Synthetic Horizon (2027).',
    'The script was written by Ada Example for the series Synthetic Horizon.',
])
def test_uncredited_or_wrong_context_writing_relationship_is_blocked(text):
    assert structured_conflicts([{'text':text}],FACTS)


@pytest.mark.parametrize('text',[
    'The screenplay was written by Ada Example.',
    'Ada Example wrote the screenplay.',
    'The screenplay was written by Bea E. Fixture.',
    'Bea E. Fixture wrote the screenplay.',
    'The screenplay was written by Ada Example and Bea E. Fixture.',
    'Ada Example and Bea E. Fixture wrote the screenplay.',
    'Ada Example, Bea E. Fixture wrote the screenplay.',
    'In Synthetic Horizon, Ada Example wrote the screenplay.',
    'The script was written by Ada Example for the film Synthetic Horizon (2026).',
    'Ada Example wrote the screenplay for Original Synthetic Horizon.',
    'The novel was written by Dee Sample.',
    'Dee Sample wrote the original novel.',
    'The original story was written by Eve Original.',
])
def test_supported_writers_and_distinct_original_source_roles_are_preserved(text):
    assert not structured_conflicts([{'text':text}],FACTS)


def test_absent_writer_metadata_cannot_certify_an_explicit_screenplay_claim():
    assert structured_conflicts([{'text':'The screenplay was written by Invented Writer.'}],{})


def test_saved_writer_words_and_current_readiness_do_not_inherit_model_approval(monkeypatch):
    from copy import deepcopy
    import re
    from fastapi.testclient import TestClient
    from backend.app.db import SessionLocal
    from backend.app.main import app
    from backend.app.models import StudioProject
    from backend.app.studio import gemini,stage_script,stage_plan
    with SessionLocal() as s:
        project=StudioProject(tmdb_id=98878,title=FACTS['title'],facts=deepcopy(FACTS),
            script={'sentences':[{'text':'Original private fixture words.','paragraph':1}]},shots=[])
        s.add(project);s.commit();pid=project.id
    def approve(prompt,**kwargs):
        return [{'i':int(i),'verdict':'ok','kind':'fact','refs':['F1']}
            for i,words in re.findall(r'(?m)^(\d+)\. (.+)$',prompt.split('Script:\n',1)[-1])]
    monkeypatch.setattr(gemini,'ask_json',approve)
    client=TestClient(app,headers={'x-access-token':'test-token'})
    for text,valid in [
        ('The screenplay was written by Invented Writer.',False),
        ('Ada Example and Invented Writer wrote the screenplay.',False),
        ('The screenplay was written by Ada Example in Other Story.',False),
        ('The novel was written by Dee Sample.',True),
        ('The screenplay was written by Bea E. Fixture.',True)]:
        saved=client.patch(f'/api/studio/projects/{pid}',json={'script':{'sentences':[{'text':text,'paragraph':1}]}})
        assert saved.status_code==200,saved.text
        if valid:stage_script.verify_saved(pid)
        else:
            with pytest.raises(RuntimeError,match='structured writing credits'):stage_script.verify_saved(pid)
            with pytest.raises(RuntimeError,match='structured writing credits'):stage_plan.plan(pid)
        current=client.get(f'/api/studio/projects/{pid}').json()
        assert current['script']['sentences'][0]['text']==text
        assert current['script']['sentences'][0]['check']=='ok'
        assert current['script_ready'] is valid
