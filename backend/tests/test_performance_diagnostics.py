from datetime import datetime,timedelta,timezone
from types import SimpleNamespace
import pytest
from backend.app.editorial.performance_diagnostics import POLICY,selected_cohorts,diagnose,recorded_cost

NOW=datetime(2026,10,18,tzinfo=timezone.utc)
COHORT=('youtube','UC'+'a'*22,'short',24)


def observation(cid,*,metrics=None,account=COHORT[1],fmt='short',age=24,observed=NOW):
    return SimpleNamespace(key='observation-'+cid,content_id=cid,platform='youtube',format=fmt,
        observed_at=observed,published_at=observed-timedelta(hours=age),
        provenance={'account_id':account,'source':'Original authorized synthetic observation'},metrics=metrics or {'views':100})


def test_matched_peer_signals_are_descriptive_exclude_subject_and_keep_missing_unknown():
    subject=observation('subject',metrics={'views':100,'impressions':100,'clicks':1,'average_view_percent':30})
    peers=[observation(str(n),metrics={'views':1000,'impressions':1000,'clicks':100,'average_view_percent':80}) for n in range(5)]
    report=diagnose(subject,peers,COHORT)
    by={item['metric']:item for item in report['measures']}
    assert by['impressions']['peer_n']==5 and by['impressions']['peer_median']==1000
    assert by['click_through_percent']['value']==1 and by['click_through_percent']['peer_median']==10
    assert by['retention_percent']['value'] is None and by['retention_percent']['status']=='missing_measure'
    assert {item['kind'] for item in report['suggestions']}=={'topic_timing','packaging','viewing'}
    assert report['cost']['amount_usd'] is None and not report['policy']['can_spend'] and not report['policy']['can_publish']
    assert 'drop-off' in report['unknowns'][0]
    assert report['version']==diagnose(subject,list(reversed(peers)),COHORT)['version']
    peers[0].metrics={'views':1001}
    assert report['version']!=diagnose(subject,peers,COHORT)['version']


def test_cohorts_are_account_format_age_separated_and_ties_choose_later_boundary():
    rows=[observation('a'),observation('b',account='UC'+'b'*22),observation('c',fmt='longform'),observation('d',age=72),observation('e',age=40)]
    older=observation('a',age=24,observed=NOW-timedelta(hours=1));older.key='older'
    groups=selected_cohorts([older,*rows])
    assert len(groups)==4 and groups[COHORT]['a'].key=='observation-a'
    assert selected_cohorts([*rows,older])[COHORT]['a'].key=='observation-a'


def test_metric_specific_small_samples_and_zero_baseline_never_invent_efficiency_or_winners():
    row=observation('subject',metrics={'views':0,'impressions':0,'clicks':0,'retention_percent':False})
    peers=[observation(str(n),metrics={'views':0,'impressions':100}) for n in range(5)]
    result=diagnose(row,peers,COHORT)
    by={item['metric']:item for item in result['measures']}
    assert by['views']['status']=='zero_peer_baseline' and by['views']['ratio'] is None
    assert by['click_through_percent']['status']=='missing_measure' and by['retention_percent']['value'] is None
    fewer=diagnose(observation('positive',metrics={'impressions':1}),peers[:4],COHORT)
    assert next(m for m in fewer['measures'] if m['metric']=='impressions')['status']=='insufficient_comparable_peers'
    assert fewer['suggestions'][0]['kind']=='collect_or_replicate'


def test_recorded_cost_is_scoped_to_known_snapshot_not_current_profit_or_unsettled_work():
    row=observation('a',metrics={'views':1000,'engaged_views':500})
    binding=SimpleNamespace(id='mapping',data={'cost_snapshot':{'amount_usd':2,'by_basis':{'configured_usage_estimate':2},
        'as_of':'2026-10-18T00:00:00+00:00','unsettled_request_ids':[]}})
    cost=recorded_cost(row,binding)
    assert cost['status']=='recorded_estimate_only' and cost['per_1000']==4 and cost['denominator']=='engaged_views'
    assert 'not current billing or profit' in cost['scope']
    binding.data['cost_snapshot']['unsettled_request_ids']=['unknown']
    assert recorded_cost(row,binding)['per_1000'] is None
    binding.data['cost_snapshot'].update(unsettled_request_ids=[],by_basis={'legacy_unknown':2})
    assert recorded_cost(row,binding)['status']=='unknown_cost_basis'
    binding.data['cost_snapshot']['by_basis']={'unrecognized_basis':2}
    assert recorded_cost(row,binding)['per_1000'] is None
    binding.data['cost_snapshot'].update(by_basis={'configured_usage_estimate':0},amount_usd=0)
    assert recorded_cost(row,binding)['per_1000']==0
    binding.data['cost_snapshot']['amount_usd']=None
    assert recorded_cost(row,binding)['per_1000'] is None


@pytest.fixture
def client():
    from fastapi.testclient import TestClient
    from backend.app.main import app
    return TestClient(app,headers={'x-access-token':'test-token'})


def import_rows(client,prefix):
    from uuid import uuid4
    stamp=datetime.now(timezone.utc)-timedelta(hours=1)
    ids=[prefix+'-'+uuid4().hex[:8] for _ in range(6)]
    for index,cid in enumerate(ids):
        result=client.post('/api/editorial/observations',json={'platform':'youtube','content_id':cid,'format':'short',
            'published_at':(stamp-timedelta(hours=24)).isoformat(),'observed_at':stamp.isoformat(),
            'metrics':{'views':100 if index==0 else 1000,'impressions':100 if index==0 else 1000,'clicks':1 if index==0 else 100},
            'source':'Owner authorized synthetic export; account unspecified'})
        assert result.status_code==200,result.text
    return ids


def test_decisions_are_idempotent_private_append_only_and_stale_evidence_is_rejected(client):
    from sqlalchemy import text
    from sqlalchemy.exc import IntegrityError
    from backend.app.db import SessionLocal
    from backend.app.models import LearningDecision,PerformanceObservation,QueueItem,EditorialRecord,AppSetting
    ids=import_rows(client,'diagnostic-contract')
    report=client.get('/api/editorial/learning-diagnostics',params={'q':ids[0]}).json()
    subject=next(item for item in report['items'] if item['content_id']==ids[0])
    assert subject['cost']['amount_usd'] is None and not subject['policy']['can_change_automation']
    with SessionLocal() as s:
        before=(s.query(QueueItem).count(),s.query(EditorialRecord).count(),s.query(AppSetting).count())
    body={'expected_version':subject['version'],'action':'plan_experiment',
        'rationale':'Review topic and packaging confounders before registering a matched creative experiment.','reviewed':True}
    route='/api/editorial/learning-diagnostics/'+subject['key']+'/review'
    result=client.post(route,json=body);assert result.status_code==200,result.text
    receipt=result.json();assert receipt['created'] and receipt['execution']=='none'
    assert client.post(route,json=body).json()['id']==receipt['id']
    assert not client.post(route,json=body).json()['created']
    assert client.post(route,json={**body,'auto_publish':True}).status_code==422
    with SessionLocal() as s:
        assert before==(s.query(QueueItem).count(),s.query(EditorialRecord).count(),s.query(AppSetting).count())
        assert s.get(LearningDecision,receipt['id']).data['diagnostic']['observation_key']==subject['observation_key']
        with pytest.raises(IntegrityError,match='append-only'):
            s.execute(text('DELETE FROM learning_decisions WHERE id=:id'),{'id':receipt['id']});s.commit()
        s.rollback()
        # Simulate a provider-side revised peer report: the stale view cannot authorize a new decision.
        peer=s.query(PerformanceObservation).filter_by(content_id=ids[1]).first()
        peer.metrics={**peer.metrics,'impressions':10};s.commit()
    assert client.post(route,json={**body,'action':'defer'}).status_code==409
    assert client.post(route,json=body).json()['id']==receipt['id']  # replay recovers the old receipt, performs no new action
    newer=next(item for item in client.get('/api/editorial/learning-diagnostics',params={'q':ids[0]}).json()['items'] if item['content_id']==ids[0])
    assert not newer['last_decision']['matches_current_evidence']
    history=client.get('/api/editorial/learning-diagnostics/'+subject['key']+'/history').json()
    assert history['total']==1 and history['decisions'][0]['data']['diagnostic']['version']==subject['version']
    assert client.post('/api/editorial/learning-diagnostics/'+subject['key']+'/execute',json=body).status_code==404


def test_diagnostic_pages_limit_comparison_work_and_require_owner_headers(client,monkeypatch):
    from fastapi.testclient import TestClient
    from backend.app.main import app
    from backend.app.editorial import performance_diagnostics as diagnostics
    import_rows(client,'diagnostic-pages')
    calls=[];original=diagnostics.diagnose
    def capture(*args,**kw):calls.append(args[0].key);return original(*args,**kw)
    monkeypatch.setattr(diagnostics,'diagnose',capture)
    response=client.get('/api/editorial/learning-diagnostics?limit=2&offset=1')
    assert response.status_code==200 and len(response.json()['items'])==2 and len(calls)==2
    assert TestClient(app).get('/api/editorial/learning-diagnostics').status_code==401
    assert client.get('/api/editorial/learning-diagnostics?limit=1000').status_code==422


def test_registered_experiment_keeps_exact_learning_decision_and_cannot_switch_account_or_stale_context(client):
    from backend.app.db import SessionLocal
    from backend.app.models import PerformanceObservation
    ids=import_rows(client,'diagnostic-experiment')
    item=client.get('/api/editorial/learning-diagnostics',params={'q':ids[0]}).json()['items'][0]
    decision=client.post('/api/editorial/learning-diagnostics/'+item['key']+'/review',json={
        'expected_version':item['version'],'action':'plan_experiment','rationale':'Compare truthful packaging while holding topic and publication-age windows comparable.','reviewed':True}).json()
    body={'name':'Recorded packaging comparison','hypothesis':'A clearer truthful thumbnail may change click-through at a comparable publication age.',
        'platform':'youtube','format':'short','age_hours':24,'account_id':None,'metric':'click_through_percent',
        'control_ids':[ids[0]],'variant_ids':[ids[1]],'controls':'Keep topic, duration and publication timing comparable; inspect reporting latency.',
        'learning_decision_id':decision['id']}
    assert client.post('/api/editorial/experiments',json={**body,'account_id':'unreviewed-account'}).status_code==409
    result=client.post('/api/editorial/experiments',json=body)
    assert result.status_code==200,result.text
    assert result.json()['learning_context']['version']==item['version']
    record=next(row for row in client.get('/api/editorial/learning').json()['experiments'] if row['id']==result.json()['id'])
    assert record['result']['groups']['control']['median']==1
    assert record['result']['groups']['control']['observation_keys']==[item['observation_key']]
    assert record['result']['status']=='insufficient_comparable_data' and record['result']['winner'] is None
    with SessionLocal() as s:
        peer=s.query(PerformanceObservation).filter_by(content_id=ids[1]).first()
        peer.metrics={**peer.metrics,'views':1001};s.commit()
    assert client.post('/api/editorial/experiments',json={**body,'name':'A new stale-context registration'}).status_code==409
    replay=client.post('/api/editorial/experiments',json=body).json()
    assert replay['id']==result.json()['id'] and not replay['created']


def test_a_superseding_private_rejection_cannot_be_bypassed_with_an_earlier_plan(client):
    ids=import_rows(client,'diagnostic-revoked-plan')
    item=client.get('/api/editorial/learning-diagnostics',params={'q':ids[0]}).json()['items'][0]
    review_url='/api/editorial/learning-diagnostics/'+item['key']+'/review'
    body={'expected_version':item['version'],'action':'plan_experiment','reviewed':True,
        'rationale':'Plan a matched private experiment after reviewing observational confounders.'}
    planned=client.post(review_url,json=body).json()
    rejected=client.post(review_url,json={**body,'action':'reject','rationale':'Defer this hypothesis because topic and publication timing are insufficiently controlled.'})
    assert rejected.status_code==200
    request={'name':'Old planned hypothesis cannot revive','hypothesis':'Compare one truthful packaging change under matched observation windows.',
        'platform':'youtube','format':'short','age_hours':24,'metric':'views','control_ids':[ids[0]],'variant_ids':[ids[1]],
        'controls':'Match topic and timing before recording any private comparison.','learning_decision_id':planned['id']}
    result=client.post('/api/editorial/experiments',json=request)
    assert result.status_code==409 and 'newer private decision' in result.text
    # Exact response recovery is not a new planning decision and cannot reorder history.
    assert not client.post(review_url,json=body).json()['created']
    latest=client.get('/api/editorial/learning-diagnostics',params={'q':ids[0]}).json()['items'][0]['last_decision']
    assert latest['action']=='reject'
