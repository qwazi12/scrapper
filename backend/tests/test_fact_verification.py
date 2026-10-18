"""All retrieval uses synthetic records or mocked DNS; no live requests."""
import hashlib
import socket
from datetime import datetime, timedelta, timezone

import pytest
from fastapi.testclient import TestClient

from backend.app.db import SessionLocal
from backend.app.models import ResearchSourceRecord, StudioProject
from backend.app.studio import fact_verification as fv, sources, stage_script


@pytest.mark.parametrize('url', ['http://apnews.com/a', 'https://apnews.com:8443/a',
    'https://u:p@apnews.com/a', 'https://apnews.com.evil.test/a', 'https://127.0.0.1/a',
    'file:///etc/passwd', 'https://apnews.com/a#fragment'])
def test_unsafe_urls_rejected_before_connection(url):
    with pytest.raises(ValueError): fv._destination(url)


def test_mixed_private_dns_is_rejected(monkeypatch):
    monkeypatch.setattr(socket, 'getaddrinfo', lambda *a, **k: [(2,1,6,'',('8.8.8.8',443)),(2,1,6,'',('127.0.0.1',443))])
    with pytest.raises(ValueError, match='non-public'): fv._destination('https://apnews.com/test')


def test_owner_semantic_review_binds_record_claim_identity_freshness_and_project(monkeypatch):
    from backend.app.main import app
    text = 'Synthetic Film (2026) is a movie. The article denies that Synthetic Film opens in June.'
    url = 'https://apnews.com/article/synthetic'
    monkeypatch.setattr(fv, 'fetch', lambda u: dict(url=u, requested_url=u, text=text, sha256=hashlib.sha256(text.encode()).hexdigest()))
    facts = dict(title='Synthetic Film', primary_date='2026-12-01', media_type='movie', region='US')
    claim = dict(text='Synthetic Film opens in June', sources=[0])
    with SessionLocal() as s:
        p=StudioProject(tmdb_id=911, title='Synthetic Film', facts=facts,
            research=dict(sources=[dict(url=url,title='Synthetic article')], claims=[claim]),
            script={'sentences':[{'text':'Owner words','check':'ok'}]}, plan=[{}], render={'file':'old'})
        s.add(p); s.commit(); pid=p.id
    c=TestClient(app, headers={'x-access-token':'test-token'})
    base=f'/api/studio/projects/{pid}'
    rid=c.post(base+'/source-records', json={'url':url}).json()['id']
    assert not sources.usable(claim,[{'url':url}],facts=facts,project_id=pid)  # denial contains phrase, still blocked
    data=dict(record_id=rid,excerpt=text,context=fv.context(facts), verdict='supported', rationale='The cited text directly denies the proposition.', conflicts=['Denial'])
    assert c.post(base+'/claims/0/review',json=data).status_code==422
    data.update(verdict='contradicted')
    r=c.post(base+'/claims/0/review',json=data); assert r.status_code==200, r.text
    assert r.json()['script']['sentences'][0]['text']=='Owner words'
    assert not r.json()['script_ready'] and r.json()['render'] is None
    rejected=r.json()['research']['claims'][0]
    assert not sources.usable(rejected,[{'url':url}],facts=facts,project_id=pid)
    with SessionLocal() as s:
        p=s.get(StudioProject,pid)
        p.research={**p.research, 'claims':[{**claim, 'text':'The article denies a June release.'}]}; s.commit()
    # Separate positive fixture: provenance is server-owned, semantic verdict is explicitly human-owned.
    data.update(verdict='supported',conflicts=[],rationale='The source explicitly denies June; the revised claim accurately describes that denial.')
    r=c.post(base+'/claims/0/review',json=data); assert r.status_code==200
    reviewed=r.json()['research']['claims'][0]
    assert sources.usable(reviewed,[{'url':url}],facts=facts,project_id=pid)
    assert not sources.usable(reviewed,[{'url':url}],facts={**facts,'media_type':'tv'},project_id=pid)
    assert not sources.usable(reviewed,[{'url':url}],facts=facts,project_id=pid+1)
    assert not sources.usable({**reviewed,'text':'Changed claim'},[{'url':url}],facts=facts,project_id=pid)
    with SessionLocal() as s:
        row=s.get(ResearchSourceRecord,rid); row.retrieved_at=datetime.now(timezone.utc)-timedelta(days=8); s.commit()
    assert not sources.usable(reviewed,[{'url':url}],facts=facts,project_id=pid)
    assert stage_script.storyboard(facts, r.json()['research'], [], pid)['R']==[]


@pytest.mark.parametrize('mode',['redirect_private','oversize','compressed','script_stripping'])
def test_retrieval_pins_public_ip_bounds_content_and_revalidates_redirects(monkeypatch, mode):
    from backend.app import e2e_safety
    monkeypatch.setattr(e2e_safety,'deny_live',lambda *a: None)
    connections=[]
    def dns(host,*a,**kw):
        return [(2,1,6,'',('127.0.0.1' if host=='variety.com' else '8.8.8.8',443))]
    monkeypatch.setattr(socket,'getaddrinfo',dns)
    class Sock:
        def close(self): pass
    def connect(addr,**kw): connections.append(addr); return Sock()
    monkeypatch.setattr(socket,'create_connection',connect)
    class TLS:
        def wrap_socket(self,raw,server_hostname): assert server_hostname=='apnews.com'; return raw
    monkeypatch.setattr(fv.ssl,'create_default_context',lambda: TLS())
    class Response:
        status=302 if mode=='redirect_private' else 200
        body=(b'x'*(fv.MAX_BYTES+1) if mode=='oversize' else
              b'<script>invented claim</script><p>Original independently retrieved synthetic record describing the source context.</p>')
        def getheader(self,key,default=None):
            return {'Location':'https://variety.com/private','Content-Type':'text/html',
                    'Content-Encoding':'gzip' if mode=='compressed' else 'identity'}.get(key,default)
        def read(self,n): result,self.body=self.body[:n],self.body[n:]; return result
    class Connection:
        def __init__(self,*a,**kw): pass
        def request(self,*a,**kw): pass
        def getresponse(self): return Response()
        def close(self): pass
    monkeypatch.setattr(fv.http.client,'HTTPSConnection',Connection)
    if mode=='script_stripping':
        data=fv.fetch('https://apnews.com/record'); assert 'invented claim' not in data['text']
        assert data['sha256']==hashlib.sha256(data['text'].encode()).hexdigest()
    else:
        with pytest.raises(ValueError): fv.fetch('https://apnews.com/record')
    assert connections==[('8.8.8.8',443)]
