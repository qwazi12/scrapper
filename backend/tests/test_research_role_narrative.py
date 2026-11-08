"""Owner 2026-11-08: a correct role followed by narrative words passes; a wrong role still fails.
Sentences are the live scripts that were blocked ("Sent 0 of 4"), with their TMDB credits."""
import pytest
from backend.app.studio import research_gate

OUTLANDER={'title':'Outlander: Blood of My Blood','media_type':'tv','cast':[
    {'actor':'Harriet Slater','character':'Ellen MacKenzie'},{'actor':'Jamie Roy','character':'Brian Fraser'},
    {'actor':'Hermione Corfield','character':'Julia Moriston Beauchamp'},{'actor':'Jeremy Irvine','character':'Henry Beauchamp'},
    {'actor':'Tony Curran','character':'Lord Lovat (Simon Fraser)'},{'actor':'Sam Retford','character':'Dougal MacKenzie'},
    {'actor':'Séamus McLean Ross','character':'Colum MacKenzie'},{'actor':'Rory Alexander','character':'Murtagh Fitzgibbons Fraser'}]}
JAILER={'title':'Jailer 2','media_type':'movie','cast':[
    {'actor':'Rajinikanth','character':"Muthuvel 'Tiger' Pandian"},{'actor':'Ramya Krishnan','character':"Vijayalakshmi 'Viji' Pandian"}]}
HEXED={'title':'Hexed','media_type':'movie','cast':[
    {'actor':'Hailee Steinfeld','character':'Billie Doe (voice)'},{'actor':'Rashida Jones','character':'Alice Doe (voice)'},
    {'actor':'Jodie Foster','character':'Queen Celeste (voice)'},{'actor':'Henry Winkler','character':'Nigel (voice)'},
    {'actor':'Dan Stevens','character':'Hieronymous (voice)'},
    {'actor':'Uncredited One','character':'(voice)'},{'actor':'Uncredited Two','character':'(voice)'}]}


@pytest.mark.parametrize('facts,words',[
    (OUTLANDER,'Our story begins with Harriet Slater as Ellen MacKenzie sharing a passionate, secret embrace with Jamie Roy '
               'as Brian Fraser in eighteenth-century Scotland.'),
    (OUTLANDER,'Centuries later around World War I, Jeremy Irvine as Henry Beauchamp finds joyful dances and quiet love with '
               'Hermione Corfield as Julia Moriston Beauchamp.'),
    (OUTLANDER,'Séamus McLean Ross as Colum MacKenzie and Sam Retford as Dougal MacKenzie demand obedience with fierce speeches, '
               'while Tony Curran as Lord Lovat Simon Fraser watches silently from a high leather chair.'),
    (JAILER,"This massive original sequel brings back superstar Rajinikanth as Muthuvel 'Tiger' Pandian for another wild ride."),
    (HEXED,'The story begins when baby Billie Doe, voiced by Hailee Steinfeld, suddenly levitates above her nursery crib.'),
    (HEXED,'Outside an academy, a wizard named Hieronymous, voiced by Dan Stevens, demonstrates sparkling plant magic.'),
    (HEXED,'High above, the powerful Queen Celeste, voiced by Jodie Foster, glares with glowing light while leading solemn witches.'),
])
def test_correct_role_followed_by_narrative_words_passes(facts,words):
    assert research_gate.structured_conflicts([{'text':words}],facts)==[]


@pytest.mark.parametrize('facts,words',[
    (OUTLANDER,'Harriet Slater as Julia Moriston Beauchamp sharing a passionate, secret embrace in Scotland.'),
    (JAILER,"Rajinikanth as Vijayalakshmi 'Viji' Pandian for another wild ride."),
    (HEXED,'Queen Celeste is voiced by Hailee Steinfeld while leading solemn witches.'),
])
def test_wrong_role_still_fails_with_narrative_words(facts,words):
    assert research_gate.structured_conflicts([{'text':words}],facts)
