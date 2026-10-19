"""Known role relationships cannot be assigned to an unlisted forward subject."""
import pytest
from backend.app.studio import research_gate

FACTS={'title':'Synthetic Horizon','media_type':'movie','primary_date':'2026-01-01',
    'cast':[{'actor':'Ada Example','character':'The Traveler / Narrator'},
            {'actor':'Bea Fixture','character':'The Man in the Wall'},
            {'actor':'Cy Figure','character':'The Traveler Junior'}]}


@pytest.mark.parametrize('words',[
    'Invented Performer plays The Traveler.',
    'Not Ada Example plays The Traveler.',
    'Invented Performer portrays The Traveler in Synthetic Horizon.',
    'Invented Performer stars as The Man in the Wall in the film Synthetic Horizon.',
    'Invented Performer plays The Traveler in the film Other Story.',
])
def test_unlisted_subject_cannot_take_a_canonical_role_even_if_model_approves(words):
    checked,issues=research_gate.checked_sentences([{'text':words,'refs':['F1']}],
        [{'i':1,'verdict':'ok','kind':'fact','refs':['F1']}],{'F':['Canonical synthetic credits']})
    assert not issues and checked[0]['check']=='ok'
    assert research_gate.structured_conflicts(checked,FACTS)


@pytest.mark.parametrize('words',[
    'Ada Example plays The Traveler.',
    'The actor Ada Example plays The Traveler in Synthetic Horizon.',
    'In Synthetic Horizon Ada Example plays The Traveler.',
    'Cy Figure plays The Traveler Junior in Synthetic Horizon.',
    'Bea Fixture portrays The Man in the Wall in Synthetic Horizon.',
])
def test_exact_subject_and_longest_canonical_role_are_preserved(words):
    assert research_gate.structured_conflicts([{'text':words}],FACTS)==[]
