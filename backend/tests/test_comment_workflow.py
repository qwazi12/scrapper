"""Comment/reply transport, durable pages, private drafts and exact correction lineage."""
import json
from datetime import datetime,timedelta,timezone
from uuid import uuid4

from fastapi.testclient import TestClient
import pytest
from sqlalchemy import text
from sqlalchemy.exc import IntegrityError

from backend.app.db import SessionLocal
from backend.app.main import app
from backend.app import worker_leases
from backend.app.models import CommentEvidence,CommentCursor,EditorialRecord,FeedbackDecision
from backend.app.editorial import comments
from backend.tests.test_audience_lineage import binding
from backend.tests.test_native_manual_formats import campaign


@pytest.fixture
def client():return TestClient(app,headers={'x-access-token':'test-token'})


@pytest.fixture
def mapped(client):
    cid,_=campaign(client,['image'])
    exported=client.post(f'/api/editorial/campaigns/{cid}/render/image').json()
    row,body=binding(client,exported['id'],source_kind='native_export')
    return row,body


def item(body,remote='parent-one',message='Actually that explanation needs a source. @private person@example.org',reply_count=0,updated=None):
    at=(datetime.now(timezone.utc)-timedelta(hours=3)).isoformat()
    return {'id':'thread-'+remote,'snippet':{'channelId':body['account_id'],'videoId':body['content_id'],'isPublic':True,'totalReplyCount':reply_count,
        'topLevelComment':{'id':remote,'snippet':{'textDisplay':message,'publishedAt':at,'updatedAt':updated or at,
            'authorDisplayName':'SensitiveAuthorNotStored','authorChannelId':{'value':'SensitiveAuthorChannel'},'authorProfileImageUrl':'https://example.invalid/private-avatar'}}}}


def progress(client,lid,parent=None):
    return client.get(f'/api/editorial/comments/{lid}',params={'parent_id':parent} if parent else {}).json()['cursor']


def page(client,lid,parent=None):
    current=progress(client,lid,parent)
    return client.post(f'/api/editorial/comments/{lid}/page',json={'expected_version':current['version'],'parent_id':parent,'reviewed':True})


def rows(client,lid):return client.get('/api/editorial/feedback',params={'lineage_id':lid}).json()['feedback']


def draft(client,row,text='A private owner draft preserved while source context changes.',approved=False,**extra):
    current=client.get(f"/api/editorial/feedback/{row['id']}/context").json()
    return client.put(f"/api/editorial/feedback/{row['id']}/draft",json={'text':text,'expected_version':current['reply_version'],
        'approved_for_manual_use':approved,'reviewed':approved,'context_lineage_id':current['context_lineage_id'],**extra})


def test_paginated_threads_resume_deduplicate_and_never_store_author_identifiers(client,mapped,monkeypatch):
    record,body=mapped;calls=[];first=item(body)
    def request(endpoint,params):
        calls.append((endpoint,params))
        assert endpoint=='commentThreads' and params['textFormat']=='plainText' and 'author' not in params['fields']
        return {'items':[first,item(body,'parent-two','How was this original image made?')] } if params.get('pageToken') else {'items':[first],'nextPageToken':'next-page'}
    monkeypatch.setattr(comments,'_request',request)
    initial=progress(client,record['id'])
    result=page(client,record['id']);assert result.status_code==200,result.text
    assert result.json()['cursor']['pages']==1 and result.json()['cursor']['can_resume']
    stale=client.post(f"/api/editorial/comments/{record['id']}/page",json={'expected_version':initial['version'],'reviewed':True})
    assert stale.status_code==409 and len(calls)==1
    final=page(client,record['id']);assert final.status_code==200,final.text
    assert final.json()['cursor']['pagination_exhausted'] and final.json()['cursor']['new_versions']==2 and final.json()['cursor']['duplicates']==1
    assert calls[1][1]['pageToken']=='next-page'
    feedback=rows(client,record['id']);assert len(feedback)==2
    assert all(not row['approved_for_manual_use'] for row in feedback)
    with SessionLocal() as s:
        evidence=s.query(CommentEvidence).filter_by(lineage_id=record['id']).all()
        saved=str([entry.data for entry in evidence])+str(feedback)
        for private in ['SensitiveAuthorNotStored','SensitiveAuthorChannel','private-avatar','person@example.org','@private']:
            assert private not in saved
        with pytest.raises(IntegrityError,match='append-only'):
            s.execute(text('DELETE FROM comment_evidence WHERE id=:id'),{'id':evidence[0].id});s.commit()
        s.rollback()
    assert client.post(f"/api/editorial/feedback/{feedback[0]['id']}/send").status_code==404


def test_reply_pages_complete_context_and_exact_private_review_is_invalidated_by_source_edit(client,mapped,monkeypatch):
    record,body=mapped;parent=item(body,reply_count=1)
    def request(endpoint,params):
        if endpoint=='commentThreads':return {'items':[parent]}
        assert endpoint=='comments' and params['parentId']=='parent-one'
        at=(datetime.now(timezone.utc)-timedelta(hours=2)).isoformat()
        return {'items':[{'id':'reply-one','snippet':{'parentId':'parent-one','textDisplay':'I have the same question about the original source.',
            'publishedAt':at,'updatedAt':at,'authorDisplayName':'PrivateReplyAuthor'}}]}
    monkeypatch.setattr(comments,'_request',request)
    assert page(client,record['id']).status_code==200
    feedback=rows(client,record['id'])[0]
    assert feedback['coverage']['requires_decision']
    assert draft(client,feedback,approved=True).status_code==409
    assert draft(client,feedback).status_code==200
    assert page(client,record['id'],feedback['latest_evidence_id']).status_code==200
    current=next(row for row in rows(client,record['id']) if row['id']==feedback['id'])
    assert current['coverage']['retained_replies']==1 and not current['coverage']['requires_decision']
    reviewed=draft(client,current,approved=True)
    assert reviewed.status_code==200,reviewed.text
    assert reviewed.json()['approved_for_manual_use']
    old_version=reviewed.json()['reply_version']
    # Source edit keeps the owner's draft words and retains prior evidence.
    parent=item(body,reply_count=1,message='Actually please inspect a different detail in that explanation.',updated=(datetime.now(timezone.utc)-timedelta(minutes=1)).isoformat())
    cursor=progress(client,record['id'])
    assert client.post(f"/api/editorial/comments/{record['id']}/restart",json={'expected_version':cursor['version'],'reviewed':True}).status_code==200
    assert page(client,record['id']).status_code==200
    changed=next(row for row in rows(client,record['id']) if row['id']==feedback['id'])
    assert changed['reply_draft']==reviewed.json()['reply_draft'] and not changed['approved_for_manual_use']
    assert client.put(f"/api/editorial/feedback/{feedback['id']}/draft",json={'text':'Stale overwriting draft must never replace owner words.',
        'expected_version':old_version,'approved_for_manual_use':True,'reviewed':True,'context_lineage_id':record['id']}).status_code==409


def test_unverified_correction_targets_exact_retained_claim_and_creates_only_draft_followup(client,mapped,monkeypatch):
    record,body=mapped
    monkeypatch.setattr(comments,'_request',lambda *a:{'items':[item(body)]})
    assert page(client,record['id']).status_code==200
    feedback=rows(client,record['id'])[0]
    detail=client.get(f"/api/editorial/feedback/{feedback['id']}/context").json()
    assert detail['claims'] and detail['context_title']
    target=detail['claims'][0]
    result=client.post(f"/api/editorial/feedback/{feedback['id']}/correction",json={'expected_version':detail['reply_version'],
        'claim_key':target['key'],'proposal':'Review the exact source detail before changing the explanation.',
        'evidence_url':'https://example.org/owner-reference','evidence_excerpt':'Owner supplied reference excerpt remains unverified until independent review.',
        'rationale':'This proposed clarification refers to the exact retained text, not a newer overwritten export.','reviewed':True})
    assert result.status_code==200,result.text
    data=result.json();assert data['status']=='proposal_requires_independent_source_review'
    replay=client.post(f"/api/editorial/feedback/{feedback['id']}/correction",json=json.loads(result.request.content)).json()
    assert not replay['created'] and replay['id']==data['id'] and replay['followup_opportunity_id']==data['followup_opportunity_id']
    assert data['claim']==target and not data['script_changed'] and not data['published_content_changed']
    with SessionLocal() as s:
        followup=s.get(EditorialRecord,data['followup_opportunity_id'])
        assert followup.data['status']=='draft' and followup.data['evidence']==[] and followup.data['feedback_refs']==[feedback['id']]
        with pytest.raises(IntegrityError,match='append-only'):
            s.execute(text('UPDATE feedback_decisions SET action=:action WHERE id=:id'),{'action':'claimed_verified','id':data['id']});s.commit()
        s.rollback()


def test_repeated_page_token_and_cross_channel_resources_never_promote(client,mapped,monkeypatch):
    record,body=mapped
    monkeypatch.setattr(comments,'_request',lambda *args:{'items':[item(body)],'nextPageToken':'repeat'})
    assert page(client,record['id']).json()['cursor']['pages']==1
    second=page(client,record['id']).json()['cursor']
    assert second['status']=='unavailable' and second['pages']==1 and not second['pagination_exhausted']
    current=progress(client,record['id'])
    client.post(f"/api/editorial/comments/{record['id']}/restart",json={'expected_version':current['version'],'reviewed':True})
    bad=item(body,'different-parent');bad['snippet']['channelId']='UC'+'q'*22
    monkeypatch.setattr(comments,'_request',lambda *args:{'items':[bad]})
    result=page(client,record['id']).json()['cursor']
    assert result['status']=='unavailable' and result['pages']==0
    assert len(rows(client,record['id']))==1


def test_owner_stop_fences_late_page_before_any_evidence_promotion(client,mapped,monkeypatch):
    record,body=mapped
    def request(*args):
        with worker_leases.bind(None),SessionLocal() as other:
            current=comments.cursor_view(other,record['id'])
            comments.stop(record['id'],comments.PageIn(expected_version=current['version'],reviewed=True),other)
        return {'items':[item(body)]}
    monkeypatch.setattr(comments,'_request',request)
    assert page(client,record['id']).status_code==409
    assert progress(client,record['id'])['status']=='stopped'
    assert rows(client,record['id'])==[]


def test_reply_history_and_stale_writes_cannot_cross_reused_feedback_ids(client):
    from backend.app.publication_journal import identity
    payload={'platform':'youtube','content_id':'manual-identity','source':'Authorized local fixture text','comments':['How can this original composition change the idea?']}
    fid=client.post('/api/editorial/feedback/import',json=payload).json()['created'][0]
    old=next(row for row in client.get('/api/editorial/learning').json()['feedback'] if row['id']==fid)
    reviewed=draft(client,old,approved=True).json()
    with SessionLocal() as s:
        row=s.get(EditorialRecord,fid);data=dict(row.data);created=row.created_at
        s.delete(row);s.commit()
        new=EditorialRecord(id=fid,kind='feedback',data=data,created_at=created+timedelta(seconds=1))
        s.add(new);s.commit()
    fresh=client.get(f'/api/editorial/feedback/{fid}/context').json()
    assert fresh['feedback_identity']!=reviewed['feedback_identity'] and fresh['decisions']==[] and not fresh['approved_for_manual_use']
    stale=client.put(f'/api/editorial/feedback/{fid}/draft',json={'text':'Old request must not overwrite the recreated feedback row.',
        'expected_version':reviewed['reply_version'],'approved_for_manual_use':False})
    assert stale.status_code==409


def test_contact_redaction_preserves_material_iso_dates():
    from backend.app.editorial.learning import deidentify
    text=deidentify('Actually the release date is 2026-10-19. Call +1 (555) 123-4567 or test@example.org.')
    assert '2026-10-19' in text and '555' not in text and 'test@example.org' not in text


def test_same_timestamp_reply_count_reversion_uses_current_observation(client,mapped,monkeypatch):
    record,body=mapped;resource=item(body,reply_count=0)
    monkeypatch.setattr(comments,'_request',lambda *a:{'items':[resource]})
    assert page(client,record['id']).status_code==200
    for count in [2,0]:
        state=progress(client,record['id'])
        client.post(f"/api/editorial/comments/{record['id']}/restart",json={'expected_version':state['version'],'reviewed':True})
        resource['snippet']['totalReplyCount']=count
        assert page(client,record['id']).status_code==200
        assert rows(client,record['id'])[0]['coverage']['reported_replies']==count


def test_literal_privacy_markers_and_paragraphs_are_not_changed():
    from backend.app.editorial.learning import deidentify
    original="Keep __SC_DATE_0__ and __DATE_0__ literal.\nThe release was 2026-10-19."
    assert deidentify(original,collapse=False)==original


def test_http_comment_reads_are_get_only_never_follow_redirects_and_hide_provider_secrets(monkeypatch,caplog):
    import httpx
    from backend.app.config import settings
    monkeypatch.setattr(settings,'youtube_api_key','synthetic-secret-not-for-ui')
    caplog.set_level('INFO',logger='httpx')
    real=httpx.Client;requests=[]
    def capture(request):
        requests.append(request)
        assert request.method=='GET' and request.url.host=='www.googleapis.com'
        assert request.headers['x-goog-api-key']=='synthetic-secret-not-for-ui' and 'key' not in request.url.params
        return httpx.Response(302,headers={'Location':'https://example.invalid/no-follow'},json={'secret':'synthetic-secret-not-for-ui'})
    monkeypatch.setattr(comments.httpx,'Client',lambda **kw:real(transport=httpx.MockTransport(capture),**kw))
    with pytest.raises(comments.CommentReadError,match='HTTP302') as error:comments._request('commentThreads',{'videoId':'fixture0001'})
    assert len(requests)==1 and 'synthetic-secret' not in str(error.value) and 'synthetic-secret' not in caplog.text
    with pytest.raises(comments.CommentReadError,match='Unsupported'):comments._request('insert',{})
    assert len(requests)==1


@pytest.mark.parametrize('invalid',[{'items':None},{'items':[{'snippet':[]} ]},{'items':[],'nextPageToken':42}])
def test_malformed_pages_are_saved_as_unavailable_without_promoting_or_losing_cursor(client,mapped,monkeypatch,invalid):
    record,body=mapped
    monkeypatch.setattr(comments,'_request',lambda *a:invalid)
    result=page(client,record['id'])
    assert result.status_code==200,result.text
    state=result.json()['cursor']
    assert state['status']=='unavailable' and state['pages']==0 and not state['pagination_exhausted']
    assert not rows(client,record['id'])


def test_mapping_correction_fences_comment_page_and_requires_new_context(client,mapped,monkeypatch):
    from backend.app.editorial import content_lineage
    record,body=mapped
    def request(*args):
        with worker_leases.bind(None),SessionLocal() as other:
            content_lineage.bind(content_lineage.BindingIn(**{**body,'format':'community','expected_current_id':record['id'],
                'mapping_evidence':'Owner corrected the mapped treatment during a pending synthetic comment read.'}),other)
        return {'items':[item(body)]}
    monkeypatch.setattr(comments,'_request',request)
    result=page(client,record['id'])
    assert result.status_code==409,result.text
    assert not rows(client,record['id'])
    assert client.get('/api/editorial/comments/'+record['id']).status_code==409
