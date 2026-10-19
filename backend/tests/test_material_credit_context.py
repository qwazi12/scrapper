"""Synthetic credits: exact known names and their explicit title/year context."""
import pytest
from backend.app.studio import research_gate

FACTS={'title':'Synthetic Horizon','media_type':'movie','primary_date':'2026-01-01',
    'cast':[{'actor':'Ada Example','character':'The Traveler / Narrator'},
            {'actor':'Bea Fixture','character':'The Man in the Wall'},
            {'actor':'Dee E. Sample','character':'Dr. Original'}]}


@pytest.mark.parametrize('words',[
    'Ada Example plays The Traveler in Synthetic Horizon.',
    'The Traveler is portrayed by Ada Example in Synthetic Horizon.',
    'Ada Example plays Narrator in the film Synthetic Horizon (2026).',
    'Bea Fixture plays The Man in the Wall.',
    'Dee E. Sample plays Dr. Original.',
    'Dr. Original is played by Dee E. Sample.',
])
def test_exact_credit_names_with_canonical_title_context_are_not_false_conflicts(words):
    assert research_gate.structured_conflicts([{'text':words}],FACTS)==[]


@pytest.mark.parametrize('words',[
    'Ada Example plays The Traveler in the film Other Story.',
    'Ada Example plays The Traveler in the film Synthetic Horizon (2027).',
    'Ada Example plays The Traveler / Invented Character.',
    'Bea Fixture plays The Man in the Wall’s father.',
    'The Traveler is portrayed by Ada Example in the film Other Story.',
    'Ada Example plays The Traveler in the series Synthetic Horizon.',
])
def test_context_qualifiers_and_additional_roles_cannot_hide_conflicts(words):
    assert research_gate.structured_conflicts([{'text':words}],FACTS)


@pytest.mark.parametrize('title',['The Movie Guide','Horizon (1984)','Alien / Original'])
def test_complete_canonical_title_is_checked_before_qualifier_or_alias_splitting(title):
    facts={**FACTS,'title':title}
    assert not research_gate.structured_conflicts([{'text':f'Ada Example plays The Traveler in {title}.'}],facts)


def test_saved_words_and_readiness_round_trip_through_current_credit_gate(monkeypatch):
    from copy import deepcopy
    import re
    from fastapi.testclient import TestClient
    from backend.app.db import SessionLocal
    from backend.app.main import app
    from backend.app.models import StudioProject
    from backend.app.studio import gemini,stage_script
    with SessionLocal() as s:
        row=StudioProject(tmdb_id=98876,title=FACTS['title'],facts=deepcopy(FACTS),research={'claims':[],'sources':[]},shots=[],
            script={'sentences':[{'text':'Original private fixture words.','paragraph':1}]},render={'file':'stale-fixture.mp4'})
        s.add(row);s.commit();pid=row.id
    def always_approve(prompt,**kwargs):
        return [{'i':int(i),'verdict':'ok','kind':'fact','refs':['F1']}
            for i,words in re.findall(r'(?m)^(\d+)\. (.+)$',prompt.split('Script:\n',1)[-1])]
    monkeypatch.setattr(gemini,'ask_json',always_approve)
    client=TestClient(app,headers={'x-access-token':'test-token'})
    for text,valid in [('Ada Example plays The Traveler in Synthetic Horizon.',True),
        ('Ada Example plays The Traveler in the film Other Story.',False),
        ('Invented Performer plays The Traveler in Synthetic Horizon.',False),
        ('The Traveler is portrayed by Ada Example in Synthetic Horizon (2027).',False),
        ('Dr. Original is played by Dee E. Sample in Synthetic Horizon.',True)]:
        saved=client.patch(f'/api/studio/projects/{pid}',json={'script':{'sentences':[{'text':text,'paragraph':1}]}})
        assert saved.status_code==200,saved.text
        if valid:stage_script.verify_saved(pid)
        else:
            with pytest.raises(RuntimeError,match='Research blocked'):stage_script.verify_saved(pid)
        result=client.get(f'/api/studio/projects/{pid}').json()
        assert result['script']['sentences'][0]['text']==text
        assert result['script']['sentences'][0]['check']=='ok'  # The fake model is not the deciding authority.
        assert result['script_ready'] is valid
        assert not result['has']['render']
