import pytest
from backend.app.studio import research_gate


FACTS={'title':'Dune: Part Three','primary_date':'2026-12-18',
       'cast':[{'actor':'Robert Pattinson','character':'Scytale'},{'actor':'Austin Butler','character':'Feyd-Rautha'}],
       'writing_credits':[{'name':'Frank Herbert','job':'Novel'},{'name':'Denis Villeneuve','job':'Screenplay'}]}


@pytest.mark.parametrize('text',[
    'Robert Pattinson plays Feyd-Rautha.',
    'Robert Pattinson portrays Scytale’s father.',
    'Scytale is played by Austin Butler.',
    'The screenplay was written by Frank Herbert.',
    'Frank Herbert wrote the screenplay.',
    'This movie was written by Frank Herbert.',
    'Dune: Part Three will be released in 2027.',
])
def test_material_credit_conflicts_are_independent_of_checker_approval(text):
    checked,issues=research_gate.checked_sentences([{'text':text,'refs':['F1']}],
        [{'i':1,'verdict':'ok','refs':['F1'],'kind':'fact'}],{'F':['provider observation']})
    assert not issues and checked[0]['check']=='ok'
    assert research_gate.structured_conflicts(checked,FACTS)


@pytest.mark.parametrize('text',[
    'Robert Pattinson plays Scytale.',
    'Robert Pattinson portrays Scytale (voice).',
    'Scytale is played by Robert Pattinson.',
    'The screenplay was written by Denis Villeneuve.',
    'The source novel was written by Frank Herbert.',
    'Dune: Part Three will be released in 2026.',
])
def test_credit_types_and_valid_source_material_context_are_preserved(text):
    assert research_gate.structured_conflicts([{'text':text}],FACTS)==[]


def test_multiple_roles_do_not_accidentally_accept_a_related_character():
    facts={'cast':[{'actor':'Synthetic Performer','character':'Hero / Narrator'}]}
    assert not research_gate.structured_conflicts([{'text':'Synthetic Performer plays Narrator.'}],facts)
    assert research_gate.structured_conflicts([{'text':'Synthetic Performer plays Hero’s brother.'}],facts)
