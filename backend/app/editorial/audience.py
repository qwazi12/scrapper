"""Explicit read-only YouTube Analytics snapshots through an existing OAuth grant."""
from __future__ import annotations
from datetime import date,datetime,time,timedelta,timezone
import json
import math
import re
from zoneinfo import ZoneInfo

from fastapi import APIRouter,Depends,HTTPException
import httpx
from sqlalchemy.orm import Session

from ..auth import require_token
from ..config import settings
from ..db import get_session
from ..models import ContentLineage,EditorialRecord,PerformanceObservation
from ..worker_leases import clock,scope,LeaseLost
from .content_lineage import latest
from .routes import Strict,Metrics,digest,_utc
from typing import Literal

router=APIRouter(prefix='/api/editorial/audience',dependencies=[Depends(require_token)])
SCOPE='https://www.googleapis.com/auth/yt-analytics.readonly'
URL='https://youtubeanalytics.googleapis.com/v2/reports'
PACIFIC=ZoneInfo('America/Los_Angeles')
NAMES={'views':'views','engagedViews':'engaged_views','likes':'likes','comments':'comments',
    'averageViewDuration':'average_view_seconds','averageViewPercentage':'average_view_percent'}
COUNTS={'views','engaged_views','likes','comments'}


class AdapterError(RuntimeError):pass


def status():
    configured=bool(settings.youtube_analytics_authorized_user_json.strip())
    return {'platform':'youtube','status':'configured_not_validated' if configured else 'unavailable',
        'reason':'Existing read-only grant configured; ownership and report availability are checked only by an explicit sync' if configured else 'Existing read-only Analytics OAuth grant not configured; owner export import remains available',
        'live_metrics':None,'mode':'explicit_read_only_sync','real_account_verified':False,
        'latency':'Provider processed-day snapshots; fetch time is not measurement time'}


def _query(params):
    from ..worker_leases import guard
    guard()
    from ..e2e_safety import deny_live
    deny_live('YouTube Analytics access')
    raw=settings.youtube_analytics_authorized_user_json.strip()
    if not raw:raise AdapterError('Read-only YouTube Analytics grant is not configured')
    try:
        info=json.loads(raw)
        scopes=info.get('scopes',[])
        if isinstance(scopes,str):scopes=scopes.split()
        if info.get('type')!='authorized_user' or SCOPE not in scopes:
            raise ValueError('Existing grant must explicitly include yt-analytics.readonly')
        if info.get('token_uri','https://oauth2.googleapis.com/token')!='https://oauth2.googleapis.com/token':
            raise ValueError('Unsupported OAuth token endpoint')
        from google.oauth2.credentials import Credentials
        from google.auth.transport.requests import Request
        credentials=Credentials.from_authorized_user_info(info,scopes=[SCOPE])
        if not credentials.valid:credentials.refresh(Request())
        token=credentials.token
    except Exception as exc:raise AdapterError('Existing read-only OAuth credential could not be used; no new grant or scope was requested') from exc
    try:
        guard()
        with httpx.Client(timeout=20,follow_redirects=False) as client:
            response=client.get(URL,params=params,headers={'Authorization':'Bearer '+token})
        from .. import costs
        costs.record_free('youtube-analytics')
        if response.status_code!=200:raise AdapterError(f'YouTube Analytics returned HTTP{response.status_code}; account/report access was not established')
        data=response.json()
        if not isinstance(data,dict):raise AdapterError('YouTube Analytics returned an unsupported response')
        return data
    except httpx.HTTPError as exc:raise AdapterError('YouTube Analytics request failed; no observation was recorded') from exc
    except (ValueError,TypeError) as exc:raise AdapterError('YouTube Analytics response could not be decoded') from exc


def rows(data,expected):
    headers=data.get('columnHeaders');values=data.get('rows',[])
    if not isinstance(headers,list) or not isinstance(values,list):raise AdapterError('Analytics response is missing a typed table')
    names=[row.get('name') if isinstance(row,dict) else None for row in headers]
    if len(names)!=len(set(names)) or set(names)!=set(expected):raise AdapterError('Analytics report columns differ from the requested measures')
    result=[]
    for row in values:
        if not isinstance(row,list) or len(row)!=len(names):raise AdapterError('Analytics report row has the wrong number of columns')
        result.append(dict(zip(names,row)))
    return result


def measurements(row):
    values={}
    for remote,local in NAMES.items():
        number=row[remote]
        if number is None:values[local]=None;continue
        if type(number) not in (int,float) or not math.isfinite(number) or number<0:
            raise AdapterError('Analytics report contains an invalid metric value')
        if local in COUNTS:
            if number!=int(number):raise AdapterError('Analytics count is not a whole number')
            number=int(number)
        values[local]=number
    if not any(value is not None for value in values.values()):raise AdapterError('Analytics report contains no observed metrics')
    return Metrics(**values).model_dump()


def retrieve(binding,asof):
    decision=binding.data['decision']
    if binding.platform!='youtube':raise AdapterError('This platform has no connected private metrics adapter')
    if not re.fullmatch(r'UC[\w-]{22}',binding.account_id) or not re.fullmatch(r'[\w-]{11}',binding.content_id):
        raise AdapterError('Use the canonical YouTube channel ID and11-character video ID')
    published=datetime.fromisoformat(decision['published_at'])
    start=published.astimezone(PACIFIC).date()
    requested_end=asof.astimezone(PACIFIC).date()-timedelta(days=1)
    if requested_end<start:return {'status':'pending_reporting','reason':'No complete reporting day is available yet'}
    common={'ids':'channel=='+binding.account_id,'startDate':start.isoformat(),'endDate':requested_end.isoformat(),
        'metrics':','.join(NAMES),'filters':'video=='+binding.content_id,'maxResults':1}
    from ..worker_leases import guard
    guard()
    table=rows(_query({**common,'dimensions':'day','sort':'-day'}),['day',*NAMES])
    if not table:return {'status':'missing','reason':'No processed rows were returned; missing measures were not converted to zero'}
    if len(table)!=1:raise AdapterError('Latest-day probe returned an unexpected number of rows')
    measurements(table[0])
    try:through=date.fromisoformat(table[0]['day'])
    except Exception as exc:raise AdapterError('Analytics day was not a valid date') from exc
    if not start<=through<=requested_end:raise AdapterError('Analytics day is outside the requested reporting window')
    guard()
    aggregate=rows(_query({**common,'endDate':through.isoformat()}),NAMES)
    if not aggregate:return {'status':'missing','reason':'Aggregate report has no processed rows; no observation was created'}
    if len(aggregate)!=1:raise AdapterError('Expected one aggregate report for the bound video')
    metrics=measurements(aggregate[0])
    observed=datetime.combine(through+timedelta(days=1),time.min,tzinfo=PACIFIC).astimezone(timezone.utc)-timedelta(microseconds=1)
    if observed< published or observed>asof:raise AdapterError('Processed-day boundary does not form a valid measurement window')
    return {'status':'observed','metrics':metrics,'observed_at':observed,'through_day':through.isoformat(),
        'period_start':start.isoformat(),'fetched_at':asof.isoformat(),
        'latency_note':'Cumulative provider aggregate through the last returned complete Pacific reporting day; fetched later. Missing days after the last active day are not assumed complete.'}


class SyncIn(Strict):reviewed:Literal[True]


@router.get('/status')
def adapter_status():return status()


@router.post('/{lineage_id}/sync')
def sync(lineage_id:str,req:SyncIn,s:Session=Depends(get_session)):
    try:
        with scope('audience-sync:'+lineage_id,session=s):
            binding=s.get(ContentLineage,lineage_id)
            if not binding:raise HTTPException(404,'Content mapping not found')
            if latest(s).get(binding.binding_key).id!=lineage_id:raise HTTPException(409,'Mapping was superseded; sync the current mapping')
            asof=clock(s)
            try:result=retrieve(binding,asof)
            except AdapterError as exc:result={'status':'unavailable','reason':str(exc)}
            from ..budget_reservations import lock
            from ..worker_leases import guard
            lock(s);guard(s);s.expire_all()
            if latest(s).get(binding.binding_key).id!=lineage_id:raise HTTPException(409,'Mapping changed during retrieval; no observation was promoted')
            asof=clock(s)
            if result['status']=='observed':result['fetched_at']=asof.isoformat()
            key=None;created=False
            if result['status']=='observed':
                key=digest(['youtube',binding.account_id,binding.content_id,lineage_id,result['observed_at'].isoformat()])
                prior=[record for record in s.query(PerformanceObservation).filter_by(platform='youtube',content_id=binding.content_id,observed_at=result['observed_at'])
                    if record.provenance.get('lineage_id')==lineage_id]
                previous=max(prior,key=lambda record:record.provenance.get('fetched_at','')) if prior else None
                existing=previous if previous and previous.metrics==result['metrics'] else None
                if existing:key=existing.key
                elif prior:key=digest([key,result['metrics'],asof.isoformat()])
                if not existing:
                    decision=binding.data['decision']
                    s.add(PerformanceObservation(key=key,platform='youtube',content_id=binding.content_id,format=decision['format'],
                        published_at=datetime.fromisoformat(decision['published_at']),observed_at=result['observed_at'],metrics=result['metrics'],
                        provenance={'mode':'youtube_analytics_readonly','account_id':binding.account_id,'lineage_id':lineage_id,
                            'source':'https://developers.google.com/youtube/analytics/reference/reports/query',
                            'period_start':result['period_start'],'through_day':result['through_day'],'fetched_at':result['fetched_at'],
                            'latency_note':result['latency_note'],'private_account_access':'provider_authorized_query',
                            'mapping_basis':'owner_attested','impressions_clicks':'not_requested; unavailable'}))
                    created=True
            s.add(EditorialRecord(kind='audience_sync',data={'lineage_id':lineage_id,'status':result['status'],
                'reason':result.get('reason'),'observation_key':key,'fetched_at':asof.isoformat(),'through_day':result.get('through_day')}))
            s.commit()
            return {**result,'observation_key':key,'created':created,'lineage_id':lineage_id}
    except LeaseLost as exc:raise HTTPException(409,str(exc))
