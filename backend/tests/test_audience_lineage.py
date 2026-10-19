"""Read-only provider fixtures, processed-day semantics and immutable local lineage."""
import copy
from datetime import datetime,timedelta,timezone
from uuid import uuid4

from fastapi.testclient import TestClient
import pytest
from sqlalchemy import text
from sqlalchemy.exc import IntegrityError

from backend.app.db import SessionLocal
from backend.app.main import app
from backend.app.models import ContentLineage,ExportVersion,PerformanceObservation,UsageEvent,BudgetReservation
from backend.app.editorial import audience,content_lineage
from backend.app.editorial.routes import digest

CHANNEL='UC'+('x'*22)


@pytest.fixture
def client():return TestClient(app,headers={'x-access-token':'test-token'})


@pytest.fixture
def retained():
    now=datetime.now(timezone.utc)
    ident=uuid4().hex
    eid=digest(['retained',ident])
    with SessionLocal() as s:
        s.add(ExportVersion(id=eid,kind='studio',project_id=900001,project_identity=digest(ident),
            created_at=now-timedelta(days=1),manifest={'title':'Original lineage fixture','project_created_at':(now-timedelta(days=5)).isoformat(),
                'snapshot':{'render':{},'inputs':{},'approval':None},'assets':[]}));s.commit()
    return eid


def binding(client,retained,**changes):
    sources=client.get('/api/editorial/content-sources').json()['sources']
    item=next(row for row in sources if row['id']==retained)
    body={'platform':'youtube','account_id':CHANNEL,'content_id':uuid4().hex[:11],'format':'short',
        'published_at':(datetime.now(timezone.utc)-timedelta(days=4)).isoformat(),
        'source_kind':'export_version','source_id':retained,'expected_source_fingerprint':item['fingerprint'],
        'mapping_evidence':'Owner reviewed the exact local export against the synthetic public identifier.', 'reviewed':True,**changes}
    result=client.post('/api/editorial/content-lineage',json=body)
    assert result.status_code==200,result.text
    return result.json(),body


def response(*,day=None,views=100,percent=130):
    names=[*(['day'] if day else []),*audience.NAMES]
    values=[*([day] if day else []),views,80,4,2,12.5,percent]
    return {'columnHeaders':[{'name':name} for name in names],'rows':[values]}


def provider(monkeypatch,*,views=100,percent=130,missing=False):
    calls=[]
    day=(datetime.now(audience.PACIFIC).date()-timedelta(days=2)).isoformat()
    def query(params):
        calls.append(params)
        result=response(day=day if params.get('dimensions') else None,views=views,percent=percent)
        if missing:result['rows']=[]
        return result
    monkeypatch.setattr(audience,'_query',query)
    return calls,day


def test_mapping_corrections_are_versioned_fenced_and_immutable(client,retained):
    row,body=binding(client,retained)
    assert client.post('/api/editorial/content-lineage',json=body).json()['created'] is False
    change={**body,'format':'reel','mapping_evidence':'Owner corrected the format after checking the exact published treatment.'}
    assert client.post('/api/editorial/content-lineage',json=change).status_code==409
    revised=client.post('/api/editorial/content-lineage',json={**change,'expected_current_id':row['id']})
    assert revised.status_code==200,revised.text
    assert revised.json()['revision']==2 and revised.json()['data']['supersedes']==row['id']
    with SessionLocal() as s:
        assert s.get(ContentLineage,row['id']).data['decision']['format']=='short'
        with pytest.raises(IntegrityError,match='append-only'):
            s.execute(text('DELETE FROM content_lineages WHERE id=:id'),{'id':row['id']});s.commit()
        s.rollback()
    assert client.post(f"/api/editorial/audience/{row['id']}/sync",json={'reviewed':True}).status_code==409
    assert TestClient(app).get('/api/editorial/content-lineage').status_code==401


def test_cumulative_aggregate_keeps_report_day_latency_and_rewatch_percent(client,retained,monkeypatch):
    row,body=binding(client,retained)
    calls,day=provider(monkeypatch)
    route=f"/api/editorial/audience/{row['id']}/sync"
    result=client.post(route,json={'reviewed':True})
    assert result.status_code==200,result.text
    data=result.json();assert data['status']=='observed' and data['created']
    assert data['metrics']['average_view_percent']==130 and data['metrics']['retention_percent'] is None
    assert data['metrics']['impressions'] is None and data['metrics']['clicks'] is None
    assert calls[0]['ids']=='channel=='+CHANNEL and calls[0]['filters']=='video=='+body['content_id']
    assert calls[0]['dimensions']=='day' and calls[0]['sort']=='-day'
    assert 'dimensions' not in calls[1] and calls[1]['endDate']==day
    assert datetime.fromisoformat(data['observed_at'])<datetime.fromisoformat(data['fetched_at'])-timedelta(hours=12)
    assert client.post(route,json={'reviewed':True}).json()['created'] is False
    with SessionLocal() as s:
        observation=s.get(PerformanceObservation,data['observation_key'])
        assert observation.provenance['account_id']==CHANNEL and observation.provenance['lineage_id']==row['id']
        assert observation.provenance['through_day']==day
    # Changing a mapping does not rewrite old measurements; it removes the old mapping from active learning.
    updated=client.post('/api/editorial/content-lineage',json={**body,'format':'reel','expected_current_id':row['id'],
        'mapping_evidence':'Owner corrected this actual format while keeping the original metric evidence.'}).json()
    with SessionLocal() as s:
        assert s.get(PerformanceObservation,data['observation_key'])
        assert data['observation_key'] not in {record.key for record in content_lineage.effective_observations(s)}
    assert updated['revision']==2


def test_missing_rows_are_unavailable_not_zero_and_unconfigured_does_not_request(client,retained,monkeypatch):
    row,_=binding(client,retained)
    calls,_=provider(monkeypatch,missing=True)
    result=client.post(f"/api/editorial/audience/{row['id']}/sync",json={'reviewed':True}).json()
    assert result['status']=='missing' and not result['created'] and result['observation_key'] is None and len(calls)==1
    # The real entry point fails clearly before credentials/network when configuration is absent.
    monkeypatch.undo()
    with pytest.raises(audience.AdapterError,match='not configured'):audience._query({})
    assert client.post(f"/api/editorial/audience/{row['id']}/sync",json={'reviewed':False}).status_code==422


def test_invalid_headers_or_metrics_never_create_fake_observation(client,retained,monkeypatch):
    row,_=binding(client,retained)
    for malformed in [{'columnHeaders':[],'rows':[]},response(day='not-date'),response(day='2001-01-01'),response(day=(datetime.now(audience.PACIFIC).date()-timedelta(days=2)).isoformat(),views=-1)]:
        monkeypatch.setattr(audience,'_query',lambda params,value=malformed:value)
        result=client.post(f"/api/editorial/audience/{row['id']}/sync",json={'reviewed':True}).json()
        assert result['status']=='unavailable' and result['observation_key'] is None


def test_owner_import_links_current_identity_and_rejects_conflicting_context(client,retained):
    row,body=binding(client,retained)
    payload={'platform':'youtube','content_id':body['content_id'],'format':'short','published_at':body['published_at'],
        'observed_at':(datetime.now(timezone.utc)-timedelta(days=3)).isoformat(),'metrics':{'views':15},
        'source':'Owner authorized source export','lineage_id':row['id']}
    result=client.post('/api/editorial/observations',json=payload)
    assert result.status_code==200,result.text
    assert client.post('/api/editorial/observations',json={**payload,'format':'reel'}).status_code==409
    with SessionLocal() as s:assert s.get(PerformanceObservation,result.json()['key']).provenance['account_id']==CHANNEL


def test_cost_snapshot_includes_matched_late_adjustment_without_claiming_invoice_proof(client,retained):
    now=datetime.now(timezone.utc);rid=str(uuid4());ref='studio:900001'
    with SessionLocal() as s:
        s.add(BudgetReservation(id=rid,service='fixture',operation='fixture',ref=ref,reserved_micros=500000,state='settled',created_at=now-timedelta(days=2)))
        for cost,basis,at in [(.3,'configured_usage_estimate',now-timedelta(days=2)),(-.1,'owner_attested_adjustment',now)]:
            s.add(UsageEvent(service='fixture',ref=ref,month=at.strftime('%Y-%m'),cost_usd=cost,cost_basis=basis,reservation_id=rid,created_at=at))
        s.commit()
    row,_=binding(client,retained)
    costs=row['data']['cost_snapshot']
    assert costs['amount_usd']==pytest.approx(.2) and costs['by_basis']['owner_attested_adjustment']==pytest.approx(-.1)
    assert 'not marginal per-cut' in costs['scope'] and 'not independently verified' in costs['limitations'][0]


def test_revised_then_restored_provider_totals_keep_history_and_use_latest_fetch(client,retained,monkeypatch):
    row,body=binding(client,retained)
    calls,day=provider(monkeypatch,views=100)
    route=f"/api/editorial/audience/{row['id']}/sync"
    first=client.post(route,json={'reviewed':True}).json()
    provider(monkeypatch,views=200)
    second=client.post(route,json={'reviewed':True}).json()
    provider(monkeypatch,views=100)
    third=client.post(route,json={'reviewed':True}).json()
    assert len({item['observation_key'] for item in (first,second,third)})==3
    assert client.post(route,json={'reviewed':True}).json()['created'] is False
    with SessionLocal() as s:
        all_rows=[record for record in s.query(PerformanceObservation) if record.provenance.get('lineage_id')==row['id']]
        active=[record for record in content_lineage.effective_observations(s) if record.provenance.get('lineage_id')==row['id']]
        assert len(all_rows)==3 and [record.key for record in active]==[third['observation_key']]
        assert active[0].metrics['views']==100


def test_read_only_http_boundary_never_follows_redirects_or_constructs_new_grants(monkeypatch):
    import json,httpx
    from types import SimpleNamespace
    from google.oauth2.credentials import Credentials
    from backend.app.config import settings
    monkeypatch.setattr(settings,'youtube_analytics_authorized_user_json',json.dumps({'type':'authorized_user','scopes':[audience.SCOPE]}))
    scopes=[]
    def credentials(info,scopes=None):
        globals_scopes=scopes
        assert globals_scopes==[audience.SCOPE]
        return SimpleNamespace(valid=True,token='synthetic-readonly-token')
    monkeypatch.setattr(Credentials,'from_authorized_user_info',credentials)
    real=httpx.Client;requests=[]
    def capture(request):
        requests.append(request)
        assert request.method=='GET' and request.url.host=='youtubeanalytics.googleapis.com'
        assert request.headers['Authorization']=='Bearer synthetic-readonly-token'
        return httpx.Response(302,headers={'Location':'https://example.invalid/do-not-follow'})
    monkeypatch.setattr(audience.httpx,'Client',lambda **kw:real(transport=httpx.MockTransport(capture),**kw))
    with pytest.raises(audience.AdapterError,match='HTTP302'):audience._query({'ids':'channel=='+CHANNEL})
    assert len(requests)==1
    monkeypatch.setattr(settings,'youtube_analytics_authorized_user_json',json.dumps({'type':'authorized_user','scopes':[]}))
    with pytest.raises(audience.AdapterError,match='no new grant or scope'):audience._query({})
    assert len(requests)==1


def test_account_cohorts_do_not_mix_and_experiments_require_the_matching_account(client,retained):
    from backend.app.editorial.learning import experiment_result
    published=datetime.now(timezone.utc)-timedelta(days=4)
    ids=[]
    for label,value in [('a',10),('b',1000)]:
        account='UC'+label*22
        row,body=binding(client,retained,account_id=account,published_at=published.isoformat())
        ids.append(body['content_id'])
        result=client.post('/api/editorial/observations',json={'platform':'youtube','content_id':body['content_id'],
            'format':'short','published_at':published.isoformat(),'observed_at':(published+timedelta(hours=24)).isoformat(),
            'metrics':{'views':value},'source':'Authorized synthetic account report','lineage_id':row['id']})
        assert result.status_code==200,result.text
    cohorts=client.get('/api/editorial/insights').json()['cohorts']
    assert next(row for row in cohorts if row['account_id']=='UC'+'a'*22)['medians']['views']==10
    assert next(row for row in cohorts if row['account_id']=='UC'+'b'*22)['medians']['views']==1000
    experiment={'platform':'youtube','format':'short','age_hours':24,'metric':'views','control_ids':[ids[0]],'variant_ids':[ids[1]],
        'minimum_per_group':3,'registered_at':datetime.now(timezone.utc).isoformat()}
    with SessionLocal() as s:
        result=experiment_result(experiment,content_lineage.effective_observations(s))
        assert result['groups']['control']['n']==0 and result['groups']['variant']['n']==0


def test_mapping_correction_fences_an_inflight_metrics_worker(client,retained,monkeypatch):
    row,body=binding(client,retained)
    called=[]
    def query(params):
        called.append(params)
        with SessionLocal() as other:
            content_lineage.bind(content_lineage.BindingIn(**{**body,'format':'reel','expected_current_id':row['id'],
                'mapping_evidence':'Owner corrected this mapping while the old metrics request was in flight.'}),other)
        day=(datetime.now(audience.PACIFIC).date()-timedelta(days=2)).isoformat()
        return response(day=day)
    monkeypatch.setattr(audience,'_query',query)
    result=client.post(f"/api/editorial/audience/{row['id']}/sync",json={'reviewed':True})
    assert result.status_code==409,result.text
    assert len(called)==1  # second query is fenced before its provider boundary
    with SessionLocal() as s:
        assert not [record for record in s.query(PerformanceObservation) if record.provenance.get('lineage_id')==row['id']]
