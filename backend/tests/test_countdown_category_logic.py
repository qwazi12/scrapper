"""Independent examples of category meaning, never live metadata/provider calls."""
import pytest
from fastapi import HTTPException
from backend.app.db import SessionLocal
from backend.app.models import CountdownProject, StudioProject
from backend.app.studio import countdown, countdown_eligibility as eligibility
from backend.tests.test_countdown_eligibility import ENTRY, FACTS, reviewable


def errors(topic, *, genres=('Horror',), countries=('KR',), available=('Netflix',), year=2026):
    entry={**ENTRY,'year':year}
    facts={**FACTS,'primary_date':f'{year}-05-18','genres':list(genres),'production_countries':list(countries)}
    providers={'flatrate':[{'provider_name':name} for name in available]}
    return eligibility.evaluate(entry,facts,eligibility.criteria(topic,'US'),providers)


def test_provider_alternative_cannot_relax_both_required_genres():
    assert errors('Horror and Comedy movies on Netflix or Hulu',available=('Netflix','Hulu'))
    assert not errors('Horror and Comedy movies on Netflix or Hulu',genres=('Horror','Comedy'))


def test_genre_alternative_cannot_relax_both_required_providers():
    assert errors('Horror or Comedy movies on Netflix and Hulu')
    assert not errors('Horror or Comedy movies on Netflix and Hulu',available=('Netflix','Hulu'))


def test_explicit_country_conjunction_requires_a_coproduction():
    assert errors('Korean and Japanese Horror movies')
    assert not errors('Korean and Japanese Horror movies',countries=('KR','JP'))
    assert not errors('Korean or Japanese Horror movies',countries=('JP',))


@pytest.mark.parametrize('topic',[
    'Horror or Comedy and Thriller movies',
    'Korean movies or Japanese TV shows',
    'Horror movies on Netflix or Comedy movies on Hulu',
    'Movies without Netflix',
    'Movies not from Japan',
    'Movies without Horror or Comedy',
    'Movies in 1999 or 2026',
    'Movies before 2000 or after 2010',
    'Movies in the 1980s and 1990s',
    'Movies after 2000 and before 2020',
    'Horror movies or after 2000',
    'Horror or an unknown category',
    'Movies before May 2000',
])
def test_unsupported_compound_or_negative_meaning_needs_explicit_rephrasing(topic):
    rule=eligibility.criteria(topic,'US')
    assert rule['requires_independent_review'], rule
    assert errors(topic,genres=('Horror','Comedy','Thriller'),countries=('KR','JP'),available=('Netflix','Hulu'),year=2005)


@pytest.mark.parametrize('topic,year,allowed',[
    ('Movies from 1999 to 2001',2000,True),
    ('Movies from 1999 to 2001',2002,False),
    ('Movies from 1999 through 2001',1999,True),
    ('Movies between 1999 and 2001',2000,True),
    ('Movies in 1999-2001',2000,True),
    ('Movies before 2000',1999,True),
    ('Movies after 2000',2000,False),
    ('Movies since 2000',2001,True),
    ('Movies since 2000',1999,False),
    ('Movies until 2000',1999,True),
    ('Movies until 2000',2001,False),
])
def test_supported_explicit_inclusive_range_and_single_bounds(topic,year,allowed):
    assert bool(errors(topic,year=year)) is not allowed


def test_ambiguous_category_never_retrieves_or_creates_partial_segments(monkeypatch):
    calls=[]
    monkeypatch.setattr(eligibility.tmdb,'fact_sheet',lambda *a,**k:calls.append(a) or FACTS)
    entries=[{**ENTRY,'rank':i,'tmdb_id':ENTRY['tmdb_id']+i,'title':f'Synthetic Film {i}'} for i in range(1,6)]
    with SessionLocal() as s:
        c=CountdownProject(topic='Horror or Comedy and Thriller movies',fmt='top5',ranking={'contract_version':1,'entries':entries})
        s.add(c);s.commit();before=s.query(StudioProject).count()
        with pytest.raises(HTTPException,match='independent evidence'):countdown.confirm_titles(s,c)
        assert not calls and c.segment_ids is None and s.query(StudioProject).count()==before


def test_old_criteria_cannot_borrow_new_boolean_meaning(reviewable):
    with SessionLocal() as s:
        c=s.get(CountdownProject,reviewable)
        proof=c.ranking['eligibility'];old={**proof['criteria'],'version':1}
        c.ranking={**c.ranking,'eligibility':{**proof,'criteria':old}};s.commit()
        with pytest.raises(HTTPException,match='Recheck title eligibility'):countdown.require_current_eligibility(s,c)


def test_old_criteria_cannot_reach_direct_child_narration(reviewable,monkeypatch):
    from backend.app.studio import runner
    with SessionLocal() as s:
        c=s.get(CountdownProject,reviewable);pid=c.segment_ids[0];words=c.script
        proof=c.ranking['eligibility']
        c.ranking={**c.ranking,'eligibility':{**proof,'criteria':{**proof['criteria'],'version':1}}};s.commit()
    calls=[];monkeypatch.setitem(runner.STAGES,'plan',lambda pid:calls.append(pid))
    with pytest.raises(HTTPException,match='Recheck title eligibility'):runner.run_one(pid,'plan')
    assert not calls
    with SessionLocal() as s:assert s.get(CountdownProject,reviewable).script==words


def test_retained_criteria_are_not_silently_reinterpreted(reviewable):
    # Tests the criteria fence before segment/media inspection; the format
    # sentinel is deliberately not represented as real export acceptance.
    from types import SimpleNamespace
    from backend.app import queue_sources
    with SessionLocal() as s:
        c=s.get(CountdownProject,reviewable);proof=c.ranking['eligibility']
        retained=SimpleNamespace(topic=c.topic,fmt=c.fmt,segment_ids=c.segment_ids,
            ranking={**c.ranking,'eligibility':{**proof,'criteria':{**proof['criteria'],'version':1}}},
            review={'export_approval':{'countdown_format':c.fmt}})
        with pytest.raises(HTTPException,match='category evidence'):queue_sources._countdown(s,retained,{})
