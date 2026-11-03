"""Independent native media-list observations; missing proof always stays held.

No publish, analytics, credential creation or queue-state mutation. Caption/native
IDs come from the provider's platform media-list endpoint, never upload echoes.
Facebook playable media can be decoded. X media-list video URLs are previews;
this contract deliberately cannot certify X video duration/audio or FB title.
"""
import asyncio
import datetime as dt
import tempfile
import time
from pathlib import Path
from urllib.parse import parse_qsl, urlsplit

import httpx

from . import pending_metadata as pm
from .uploads import validate_video
from .studio.media_quality import file_hash
from .social import upload_post

MAX_BYTES = 256 * 1024 * 1024


def public_identity(url, platform):
    if not isinstance(url, str): return None
    try:
        value=urlsplit(url)
        host=(value.hostname or '').lower().removeprefix('www.').removeprefix('m.')
        if value.scheme!='https' or value.username or value.password or value.port not in (None,443): return None
        if platform=='x' and host in ('x.com','twitter.com'): host='x.com'
        if host != ('facebook.com' if platform=='facebook' else 'x.com'): return None
        query=tuple(sorted((k,v) for k,v in parse_qsl(value.query) if k in ('v','story_fbid','id')))
        return host,value.path.rstrip('/'),query
    except ValueError:
        return None


def native_view(row, platform):
    return {'native_post_id':row.get('id'), 'observed_caption':row.get('caption'),
        'permalink':row.get('permalink'), 'media_type':row.get('media_type'), 'timestamp':row.get('timestamp'),
        'media_url_kind':('video_preview_image_only' if platform=='x' and row.get('media_type')=='VIDEO'
                          else 'facebook_playable_candidate' if platform=='facebook' and row.get('media_type')=='VIDEO'
                          else 'other_or_unavailable') if row.get('media_url') else 'unavailable',
        'returned_field_names':sorted(row),
        'duration_observed':None, 'audio_observed':None}


async def facebook_media(url):
    """Decode one bounded provider-returned CDN video; never follow redirects."""
    try:
        value=urlsplit(url)
        host=(value.hostname or '').lower()
        if (value.scheme!='https' or value.username or value.password or value.port not in (None,443)
                or not host.endswith('.fbcdn.net')):
            return {'status':'unknown','reason':'No supported Facebook playable CDN URL was returned'}
        with tempfile.TemporaryDirectory(prefix='drama-published-readback-') as root:
            path=Path(root)/'published.mp4'
            deadline=time.monotonic()+90
            async with httpx.AsyncClient(timeout=60, follow_redirects=False) as client:
                async with client.stream('GET',url) as response:
                    response.raise_for_status()
                    if response.is_redirect: raise ValueError('Redirect is outside the bounded readback')
                    advertised=response.headers.get('content-length')
                    if advertised and (not advertised.isdigit() or int(advertised)>MAX_BYTES):
                        raise ValueError('Published media exceeds the bounded readback')
                    total=0
                    with path.open('wb') as file:
                        async for block in response.aiter_bytes(1024*1024):
                            total+=len(block)
                            if total>MAX_BYTES or time.monotonic()>deadline:
                                raise ValueError('Published media exceeds the bounded readback')
                            file.write(block)
            probe=await asyncio.to_thread(validate_video,path)
            audio=next((stream for stream in probe['streams'] if stream.get('codec_type')=='audio'),None)
            return {'status':'decoded','decode':'passed','sha256':file_hash(path),'bytes':total,
                'duration_observed':float(probe['format']['duration']),
                'audio_observed':{'codec':audio.get('codec_name'),'duration':audio.get('duration'),
                    'sample_rate':audio.get('sample_rate'),'channels':audio.get('channels')} if audio else None,
                'audio_content_fidelity':'not established by stream presence alone'}
    except (httpx.HTTPError,ValueError,TypeError,OSError) as exc:
        return {'status':'unknown','reason':'Published media read/decode unavailable: '+type(exc).__name__}
    except Exception as exc:
        # validate_video raises controlled HTTPException for invalid media. No
        # remote signed URL, headers or provider response is copied into errors.
        return {'status':'unknown','reason':'Published media validation unavailable: '+type(exc).__name__}


async def inspect_destination(platform, post_url, expected_payload, source):
    body=await upload_post.published_media('Dramarama',platform)
    wanted=public_identity(post_url,platform)
    matches=[row for row in body['media'] if wanted is not None and public_identity(row.get('permalink'),platform)==wanted]
    common={'readback_source':'native platform media list via Upload-Post; not upload metadata',
        'checked_at':dt.datetime.now(dt.timezone.utc).isoformat(),'fidelity_verified':False,
        'source_sha256':source['sha256'],'source_duration':source['duration'],
        'reviewed_payload_sha256':pm.digest(expected_payload),'bounded_pages':1,
        'has_more':(body.get('pagination') or {}).get('has_more')}
    if len(matches)!=1:
        return {**common,'status':'unknown','reason':'Exactly one matching native post was not found in this bounded page'}
    row=matches[0]
    if not isinstance(row.get('id'),str) or not row['id'] or not isinstance(row.get('caption'),str):
        return {**common,'status':'unknown','reason':'Native post ID or observed caption is missing'}
    result={**common,**native_view(row,platform),'status':'partial_observation'}
    expected=expected_payload['x_title' if platform=='x' else 'facebook_description']
    result['caption_matches_reviewed_field']=row['caption']==expected
    result['caption_field']='x_title' if platform=='x' else 'facebook_description'
    if platform=='facebook':
        result['facebook_title_observed']=None  # Normalized media-list schema has no separate title.
        if row.get('media_type')=='VIDEO' and isinstance(row.get('media_url'),str):
            result['published_media']=await facebook_media(row['media_url'])
            observed=result['published_media'].get('duration_observed')
            result['duration_matches_source']=abs(observed-source['duration'])<=0.5 if observed is not None else None
        result['missing_proofs']=['independent Facebook title readback','audio content fidelity']
    else:
        result['missing_proofs']=['X actual video duration and audio; media-list video URL is a preview image']
    if not result['caption_matches_reviewed_field']:
        result['missing_proofs'].append('Exact observed caption differs or readback does not cover the complete public text')
    result['observation_sha256']=pm.digest(result)
    return result
