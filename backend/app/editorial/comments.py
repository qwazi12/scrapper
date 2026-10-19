"""Published YouTube comments: bounded read-only pages, redacted evidence, private drafts."""
from __future__ import annotations
from datetime import datetime,timezone,timedelta
import re
from uuid import uuid4
from typing import Literal

from fastapi import APIRouter,Depends,HTTPException
import httpx
from pydantic import Field
from sqlalchemy.orm import Session

from ..auth import require_token
from ..budget_reservations import lock
from ..config import settings
from ..db import get_session
from ..models import CommentCursor,CommentEvidence,ContentLineage,EditorialRecord,FeedbackDecision
from ..worker_leases import clock,scope,guard,revoke,LeaseLost
from ..publication_journal import identity
from .content_lineage import latest
from .learning import deidentify,classify
from .routes import Strict,digest

router=APIRouter(prefix='/api/editorial/comments',dependencies=[Depends(require_token)])
BASE='https://www.googleapis.com/youtube/v3/'
REMOTE=re.compile(r'^[A-Za-z0-9._-]{1,255}$')


class CommentReadError(RuntimeError):pass


def _request(endpoint,params):
    from ..e2e_safety import deny_live
    deny_live('YouTube comment access');guard()
    if endpoint not in ('commentThreads','comments'):raise CommentReadError('Unsupported read-only endpoint')
    if not settings.youtube_api_key:raise CommentReadError('YouTube read-only Data API key is not configured')
    try:
        with httpx.Client(timeout=20,follow_redirects=False) as client:
            response=client.get(BASE+endpoint,params=params,headers={'x-goog-api-key':settings.youtube_api_key})
        from .. import costs
        costs.record_free('youtube-comments')
        if response.status_code!=200:
            # Do not echo an upstream body, request URL or API key into logs/UI.
            reason=None
            try:reason=response.json().get('error',{}).get('errors',[{}])[0].get('reason')
            except (ValueError,IndexError,AttributeError,TypeError):pass
            message={'commentsDisabled':'Comments are disabled for this video','videoNotFound':'Video comments are inaccessible',
                'quotaExceeded':'YouTube read quota is exhausted; resume later with the retained cursor'}.get(reason)
            raise CommentReadError(message or f'YouTube comment read returned HTTP{response.status_code}; no page was promoted')
        data=response.json()
        if not isinstance(data,dict):raise CommentReadError('Comment response is not an object')
        return data
    except httpx.HTTPError as exc:raise CommentReadError('Comment read failed; the prior cursor and entries remain available') from exc
    except ValueError as exc:raise CommentReadError('Comment response could not be decoded') from exc


def active(s,lid):
    binding=s.get(ContentLineage,lid)
    if not binding:raise HTTPException(404,'Content mapping not found')
    if latest(s).get(binding.binding_key).id!=lid:raise HTTPException(409,'Use the current content mapping for comment context')
    if binding.platform!='youtube' or not re.fullmatch(r'UC[\w-]{22}',binding.account_id) or not re.fullmatch(r'[\w-]{11}',binding.content_id):
        raise HTTPException(409,'Published-video comments require a canonical YouTube channel/video mapping')
    return binding


def cursor_id(lid,parent=None):return digest(['comment-page',lid,parent])
def version(cursor,identifier):return digest([identifier,cursor.data if cursor else 'initial'])


def parent_for(s,lid,parent_id):
    if not parent_id:return None
    parent=s.get(CommentEvidence,parent_id)
    if not parent or parent.lineage_id!=lid or parent.parent_key:raise HTTPException(409,'Choose a top-level comment from this exact current mapping')
    return parent


def cursor_view(s,lid,parent=None):
    identifier=cursor_id(lid,parent.comment_key if parent else None)
    row=s.get(CommentCursor,identifier)
    data=row.data if row else {'token':None,'pages':0,'complete':False,'status':'not_started','last_error':None}
    return {'id':identifier,'version':version(row,identifier),'pages':data.get('pages',0),'pagination_exhausted':bool(data.get('complete')),
        'can_resume':not data.get('complete',False),'status':data.get('status'),'last_error':data.get('last_error'),
        'seen_entries':data.get('seen_entries',0),'new_versions':data.get('new_versions',0),'duplicates':data.get('duplicates',0),
        'as_of':data.get('as_of'),'scope':'published_replies' if parent else 'published_top_level_comments'}


def configured():return bool(settings.youtube_api_key)


@router.get('/{lid}')
def progress(lid:str,parent_id:str|None=None,s:Session=Depends(get_session)):
    active(s,lid);parent=parent_for(s,lid,parent_id)
    return {'cursor':cursor_view(s,lid,parent),'configured':configured(),
        'mode':'explicit_read_only_pages','author_identifiers':'not_requested_or_stored'}


class PageIn(Strict):
    expected_version:str=Field(min_length=64,max_length=64)
    parent_id:str|None=Field(default=None,min_length=64,max_length=64)
    reviewed:Literal[True]


def stamp(value,asof):
    try:result=datetime.fromisoformat(value.replace('Z','+00:00'))
    except (ValueError,AttributeError) as exc:raise CommentReadError('Comment timestamp is invalid') from exc
    if not result.tzinfo or result>asof+timedelta(minutes=5):raise CommentReadError('Comment timestamp lacks a valid reporting boundary')
    return result.astimezone(timezone.utc).isoformat()


def parse(data,binding,parent,asof):
    items=data.get('items')
    token=data.get('nextPageToken')
    if not isinstance(items,list) or len(items)>100 or (token is not None and (not isinstance(token,str) or not 1<=len(token)<=2048)):
        raise CommentReadError('Comment page shape is invalid')
    result=[]
    for item in items:
        if not isinstance(item,dict):raise CommentReadError('Comment resource is invalid')
        if parent:
            value=item;snippet=value.get('snippet',{})
            if not isinstance(snippet,dict):raise CommentReadError('Reply snippet is invalid')
            if snippet.get('parentId')!=parent.remote_id:raise CommentReadError('Reply belongs to a different top-level comment')
            if snippet.get('videoId') not in (None,binding.content_id):raise CommentReadError('Reply belongs to a different video')
            thread_key=parent.thread_key;parent_key=parent.comment_key;count=None
        else:
            thread=item.get('snippet',{})
            if not isinstance(thread,dict):raise CommentReadError('Thread snippet is invalid')
            if thread.get('channelId')!=binding.account_id or thread.get('videoId')!=binding.content_id or thread.get('isPublic') is not True:
                raise CommentReadError('Thread does not match the mapped public channel/video')
            if not isinstance(item.get('id'),str) or not REMOTE.fullmatch(item['id']):raise CommentReadError('Thread identifier is invalid')
            thread_key=digest(['youtube-thread',binding.account_id,binding.content_id,item['id']]);parent_key=None
            value=thread.get('topLevelComment',{})
            if not isinstance(value,dict):raise CommentReadError('Top-level comment is invalid')
            snippet=value.get('snippet',{})
            if not isinstance(snippet,dict):raise CommentReadError('Comment snippet is invalid')
            count=thread.get('totalReplyCount')
            if count is not None and (type(count) is not int or count<0):raise CommentReadError('Reply count is invalid')
        remote=value.get('id');text=snippet.get('textDisplay')
        if not isinstance(remote,str) or not REMOTE.fullmatch(remote):raise CommentReadError('Comment identifier is invalid')
        if not isinstance(text,str) or not 1<=len(text)<=10000:raise CommentReadError('Comment text is missing or exceeds the local bound')
        published=stamp(snippet.get('publishedAt'),asof);updated=stamp(snippet.get('updatedAt'),asof)
        if datetime.fromisoformat(updated)<datetime.fromisoformat(published):raise CommentReadError('Comment update predates its publication')
        key=digest(['youtube-comment',binding.account_id,binding.content_id,remote])
        clean=deidentify(text)
        result.append({'comment_key':key,'thread_key':thread_key,'parent_key':parent_key,'remote_id':remote,
            'data':{'text':clean,'provider_published_at':published,'provider_updated_at':updated,'reported_reply_count':count,
                'scope':'published_only','author_fields_requested':False,'source_fingerprint':binding.data['source_fingerprint']}})
    seen={}
    for entry in result:
        if entry['comment_key'] in seen and seen[entry['comment_key']]!=entry:raise CommentReadError('Conflicting versions of the same comment appeared in one page')
        seen[entry['comment_key']]=entry
    return result,token


def initial_reply(binding,text,category):
    manifest=binding.data['source_manifest']
    title=deidentify(manifest.get('title') or manifest.get('inputs',{}).get('brief',{}).get('title') or 'this video')[:100]
    if category=='possible_spam':return ''
    if category=='correction':return f'Thank you for flagging the detail in {title}. We can check the exact claim against its retained sources before confirming a correction.'
    if category=='question':return f'Thanks for your question about {title}. Which part of the explanation would you like us to examine against the linked sources?'
    if category=='debate':return f'Thanks for sharing your view on {title}. Which scene or comparison most influenced your interpretation?'
    return f'Thanks for the feedback on {title}. Which visual or explanation would you like us to revisit?'


def promote(s,binding,entries,asof):
    new,duplicates=0,0
    seen=set()
    for entry in entries:
        identifier=digest([binding.id,entry])
        existing=s.get(CommentEvidence,identifier)
        row=s.query(EditorialRecord).filter(EditorialRecord.kind=='feedback',EditorialRecord.data['evidence_key'].as_string()==entry['comment_key']).first()
        if identifier in seen or existing:
            duplicates+=1
            if existing and row and row.data.get('latest_evidence_id')!=identifier:
                prior=s.get(CommentEvidence,row.data.get('latest_evidence_id')) if row.data.get('latest_evidence_id') else None
                if not prior or prior.data['provider_updated_at']<=existing.data['provider_updated_at']:
                    row.data={**row.data,'text':existing.data['text'],'category':classify(existing.data['text']),
                        'latest_evidence_id':identifier,'lineage_id':binding.id,'approved_for_manual_use':False,'context_changed_at':asof.isoformat()}
            continue
        seen.add(identifier)
        category=classify(entry['data']['text'])
        if row:
            old=row.data
            prior=s.get(CommentEvidence,old.get('latest_evidence_id')) if old.get('latest_evidence_id') else None
            if prior and prior.data['provider_updated_at']>entry['data']['provider_updated_at']:
                s.add(CommentEvidence(id=identifier,comment_key=entry['comment_key'],thread_key=entry['thread_key'],parent_key=entry['parent_key'],
                    lineage_id=binding.id,remote_id=entry['remote_id'],data={**entry['data'],'feedback_id':row.id,'feedback_identity':identity(row),'promotion':'older_source_version_retained_without_replacing_current'},created_at=asof))
                new+=1;continue
            # Preserve owner words; a source edit only invalidates review.
            row.data={**old,'text':entry['data']['text'],'category':category,'approved_for_manual_use':False,
                'latest_evidence_id':identifier,'lineage_id':binding.id,'thread_key':entry['thread_key'],
                'context_changed_at':asof.isoformat()}
        else:
            row=EditorialRecord(kind='feedback',data={'platform':'youtube','account_id':binding.account_id,'content_id':binding.content_id,
                'source':'YouTube published comment resource','text':entry['data']['text'],'category':category,
                'classification':'heuristic_needs_review','evidence_key':entry['comment_key'],'lineage_id':binding.id,
                'latest_evidence_id':identifier,'thread_key':entry['thread_key'],
                'reply_draft':initial_reply(binding,entry['data']['text'],category),'approved_for_manual_use':False,
                'privacy_note':'Author names/IDs/avatars were not requested. Common contacts redacted; owner must inspect remaining personal details.'})
            s.add(row);s.flush()
        s.add(CommentEvidence(id=identifier,comment_key=entry['comment_key'],thread_key=entry['thread_key'],parent_key=entry['parent_key'],
            lineage_id=binding.id,remote_id=entry['remote_id'],data={**entry['data'],'feedback_id':row.id,'feedback_identity':identity(row)},created_at=asof))
        new+=1
    return new,duplicates


def params(binding,cursor,parent):
    common={'part':'snippet','maxResults':100,'textFormat':'plainText'}
    token=cursor.data.get('token')
    if token:common['pageToken']=token
    if parent:return 'comments',{**common,'parentId':parent.remote_id,
        'fields':'nextPageToken,items(id,snippet(parentId,videoId,textDisplay,publishedAt,updatedAt))'}
    return 'commentThreads',{**common,'videoId':binding.content_id,'order':'time',
        'fields':'nextPageToken,items(id,snippet(channelId,videoId,isPublic,totalReplyCount,topLevelComment(id,snippet(textDisplay,publishedAt,updatedAt))))'}


@router.post('/{lid}/page')
def read_page(lid:str,req:PageIn,s:Session=Depends(get_session)):
    binding=active(s,lid);parent=parent_for(s,lid,req.parent_id)
    identifier=cursor_id(lid,parent.comment_key if parent else None)
    try:
        with scope('comment-sync:'+identifier,session=s):
            lock(s);cursor=s.get(CommentCursor,identifier)
            if version(cursor,identifier)!=req.expected_version:raise HTTPException(409,'Comment cursor changed; reload progress before resuming')
            if cursor and cursor.data.get('complete'):raise HTTPException(409,'This page sequence is exhausted; start a new reviewed scan for newer comments')
            if not cursor:
                cursor=CommentCursor(id=identifier,lineage_id=lid,parent_key=parent.comment_key if parent else None,
                    data={'run_id':uuid4().hex,'token':None,'pages':0,'complete':False,'seen_entries':0,'new_versions':0,'duplicates':0,'seen_tokens':[]},updated_at=clock(s))
                s.add(cursor);s.commit()
            else:s.commit()
            before=version(cursor,identifier);asof=clock(s)
            endpoint,arguments=params(binding,cursor,parent)
            try:
                guard();data=_request(endpoint,arguments);entries,token=parse(data,binding,parent,asof)
                token_hash=digest(token) if token else None
                if token_hash and (token_hash in cursor.data.get('seen_tokens',[]) or token==cursor.data.get('token')):
                    raise CommentReadError('Provider repeated a page token; cursor retained without promotion')
                error=None
            except CommentReadError as exc:entries=[];token=None;token_hash=None;error=str(exc)
            lock(s);guard(s);s.expire_all();binding=active(s,lid);cursor=s.get(CommentCursor,identifier)
            if version(cursor,identifier)!=before:raise HTTPException(409,'Comment scan changed during retrieval; no page was promoted')
            asof=clock(s)
            if error:cursor.data={**cursor.data,'status':'unavailable','last_error':error,'as_of':asof.isoformat()}
            else:
                new,duplicates=promote(s,binding,entries,asof)
                cursor.data={**cursor.data,'token':token,'complete':token is None,'pages':cursor.data.get('pages',0)+1,
                    'status':'pagination_exhausted' if token is None else 'more_pages','last_error':None,'as_of':asof.isoformat(),
                    'seen_entries':cursor.data.get('seen_entries',0)+len(entries),'new_versions':cursor.data.get('new_versions',0)+new,
                    'duplicates':cursor.data.get('duplicates',0)+duplicates,
                    'seen_tokens':[*cursor.data.get('seen_tokens',[]),*([token_hash] if token_hash else [])]}
            cursor.updated_at=asof;s.commit()
            return {'cursor':cursor_view(s,lid,parent),'delivery':'private_evidence_and_drafts_only'}
    except LeaseLost as exc:raise HTTPException(409,str(exc))


@router.post('/{lid}/restart')
def restart(lid:str,req:PageIn,s:Session=Depends(get_session)):
    lock(s);active(s,lid);parent=parent_for(s,lid,req.parent_id)
    identifier=cursor_id(lid,parent.comment_key if parent else None);cursor=s.get(CommentCursor,identifier)
    if version(cursor,identifier)!=req.expected_version:raise HTTPException(409,'Comment cursor changed; refresh before restarting')
    revoke(s,'comment-sync:'+identifier)
    data={'run_id':uuid4().hex,'token':None,'pages':0,'complete':False,'seen_entries':0,'new_versions':0,'duplicates':0,'seen_tokens':[],'status':'not_started','last_error':None}
    if cursor:cursor.data=data;cursor.updated_at=clock(s)
    else:s.add(CommentCursor(id=identifier,lineage_id=lid,parent_key=parent.comment_key if parent else None,data=data,updated_at=clock(s)))
    s.commit();return {'cursor':cursor_view(s,lid,parent),'retained_entries':'kept; unchanged comment versions deduplicate'}


@router.post('/{lid}/stop')
def stop(lid:str,req:PageIn,s:Session=Depends(get_session)):
    lock(s);active(s,lid);parent=parent_for(s,lid,req.parent_id)
    identifier=cursor_id(lid,parent.comment_key if parent else None);cursor=s.get(CommentCursor,identifier)
    if version(cursor,identifier)!=req.expected_version:raise HTTPException(409,'Comment scan changed; refresh its current state before stopping')
    revoke(s,'comment-sync:'+identifier)
    if cursor:
        cursor.data={**cursor.data,'status':'stopped','last_error':None};cursor.updated_at=clock(s)
    s.commit();return {'cursor':cursor_view(s,lid,parent),'retained_entries':'kept'}
