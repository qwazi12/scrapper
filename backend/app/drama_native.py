"""Official native read adapters; disconnected by default, no upload/history echo.

X: https://docs.x.com/x-api/posts/get-post-by-id
Meta video fields: facebook/facebook-python-business-sdk adobjects/advideo.py
Existing Upload-Post credentials are never sent to these native services.
"""
import re
import json
from urllib.parse import parse_qs, urlsplit

import httpx

from .config import settings
from . import pending_metadata as pm


def capability():
    enabled = settings.drama_native_reads_enabled
    return {'official_native_read_adapters_implemented': True, 'enabled': enabled,
        'facebook_read_configured': enabled and bool(settings.drama_fb_read_token),
        'x_read_configured': enabled and bool(settings.drama_x_read_token),
        'posting_token_is_native_read_authority': False,
        'unconfigured_behavior': 'Per-item delivered-proof hold; never assume title/audio/video from a success URL or preview image',
        'live_reads_enabled_by_deployment': False}


def post_id(url, platform):
    try:
        u = urlsplit(url)
        host = (u.hostname or '').lower().removeprefix('www.').removeprefix('m.')
        if u.scheme != 'https' or u.username or u.password or u.port not in (None, 443):
            return None
        if platform == 'x' and host in ('x.com', 'twitter.com'):
            m = re.fullmatch(r'/([A-Za-z0-9_]{1,15})/status/(\d{5,30})/?', u.path)
            return m.group(2) if m else None
        if platform == 'facebook' and host == 'facebook.com':
            m = re.fullmatch(r'/(?:[^/]+/videos|reel)/(\d{5,30})/?', u.path)
            if m:
                return m.group(1)
            if u.path.rstrip('/') == '/watch':
                values = parse_qs(u.query).get('v', [])
                if len(values) == 1 and re.fullmatch(r'\d{5,30}', values[0]):
                    return values[0]
    except (ValueError, TypeError):
        pass
    return None


async def read(platform, url, target, expected):
    missing = {'state': 'held', 'platform': platform, 'native_observed': False,
        'missing': [], 'verified': False}
    ident = post_id(url, platform)
    if not ident:
        return {**missing, 'missing': ['supported unambiguous native video/Post identity in the retained provider URL']}
    key = settings.drama_fb_read_token if platform == 'facebook' else settings.drama_x_read_token
    if not settings.drama_native_reads_enabled or not key:
        needed = ('Facebook native Video GET: id/from/title/description/source/length/published'
            if platform == 'facebook' else 'X GET /2/tweets/{id}: author_id/text/attached video variants/duration_ms')
        return {**missing, 'native_post_id': ident, 'missing': [needed + '; native-read connection/authority is not enabled/configured'],
            'provider_media_list_substitute': False}
    try:
        async with httpx.AsyncClient(timeout=30, follow_redirects=False) as client:
            if platform == 'facebook':
                endpoint = f'https://graph.facebook.com/{settings.drama_fb_graph_version}/{ident}'
                params = {'fields': 'id,from,title,description,source,length,published,permalink_url'}
            else:
                endpoint = f'https://api.x.com/2/tweets/{ident}'
                params = {
                        'tweet.fields': 'id,text,author_id,attachments,entities',
                        'expansions': 'author_id,attachments.media_keys', 'user.fields': 'id,username',
                        'media.fields': 'media_key,type,duration_ms,variants'}
            async with client.stream('GET', endpoint, headers={'Authorization': 'Bearer ' + key}, params=params) as r:
                r.raise_for_status()
                if r.is_redirect:
                    raise ValueError('unsupported native redirect')
                data = bytearray()
                async for chunk in r.aiter_bytes(65536):
                    if len(data) + len(chunk) > 1024 * 1024:
                        raise ValueError('oversized native response')
                    data.extend(chunk)
                body = json.loads(data)
        if platform == 'facebook':
            return facebook(body, ident, target, expected)
        return x_post(body, ident, target, expected)
    except (httpx.HTTPError, ValueError, TypeError, KeyError, AttributeError):
        return {**missing, 'native_post_id': ident, 'missing': ['native GET unavailable or incomplete; no caller/provider echoes substituted']}


def facebook(body, ident, target, expected):
    required = ('id', 'from', 'title', 'description', 'source', 'length', 'published', 'permalink_url')
    if not isinstance(body, dict) or any(k not in body for k in required):
        raise ValueError('missing native video fields')
    checks = {'native_id': body['id'] == ident,
        'native_owner': body['from'].get('id') == target['facebook_page_id'],
        'public_copy': body['title'] == expected['facebook_title'] and body['description'] == expected['facebook_description'],
        'published': body['published'] is True,
        'permalink_id': post_id('https://www.facebook.com'+body['permalink_url'] if body['permalink_url'].startswith('/') else body['permalink_url'], 'facebook') == ident}
    return {'platform': 'facebook', 'state': 'observed', 'native_observed': True,
        'native_post_id': ident, 'native_owner_id': body['from'].get('id'), 'checks': checks,
        'observed_copy_sha256': pm.digest({k: body[k] for k in ('title', 'description')}),
        'native_duration_seconds': body['length'], 'media_url': body['source'],
        'read_source': 'Facebook Graph native Video GET', 'verified': False,
        'missing': [k for k, v in checks.items() if not v]}


def x_post(body, ident, target, expected):
    data = body['data']; includes = body['includes']; keys = data['attachments']['media_keys']
    if (not isinstance(keys, list) or len(keys) != 1 or body.get('errors')
            or data['id'] != ident or data['author_id'] != target['x_account_id']):
        raise ValueError('ambiguous post/author/attachment')
    authors = [u for u in includes['users'] if u.get('id') == data['author_id']]
    videos = [v for v in includes['media'] if v.get('media_key') in keys and v.get('type') == 'video']
    if len(authors) != 1 or authors[0]['username'] != target['x_handle'] or len(videos) != 1:
        raise ValueError('native user/handle/video mismatch')
    video = videos[0]
    variants = [v for v in video['variants'] if v.get('content_type') == 'video/mp4'
                and isinstance(v.get('url'), str) and type(v.get('bit_rate')) is int]
    if not variants:
        raise ValueError('no actual native video variant')
    # A native structural attachment t.co URL is distinct from the reviewed
    # public text. Remove only one proven appended attachment entity; never
    # truncate, strip arbitrary links or turn an echoed title into native text.
    text = data['text']; attachment_suffix = False
    if text != expected['x_title']:
        urls = data.get('entities', {}).get('urls', [])
        valid = [e for e in urls if e.get('media_key') == keys[0] and isinstance(e.get('url'), str)
                 and text == expected['x_title'] + ' ' + e['url']]
        attachment_suffix = len(valid) == 1 and len(urls) == 1
    matches = text == expected['x_title'] or attachment_suffix
    chosen = max(variants, key=lambda v: v['bit_rate'])
    return {'platform': 'x', 'state': 'observed', 'native_observed': True, 'native_post_id': ident,
        'native_owner_id': data['author_id'], 'checks': {'native_id': True, 'native_owner': True, 'public_copy': matches,
            'published': True, 'single_exact_video_attachment': True},
        'observed_copy_sha256': pm.digest(text), 'structural_attachment_URL_suffix_only': attachment_suffix,
        'native_duration_seconds': video['duration_ms'] / 1000, 'media_url': chosen['url'],
        'media_key': keys[0], 'read_source': 'X API v2 native Post GET with author/media expansions',
        'verified': False, 'missing': [] if matches else ['public_copy']}
