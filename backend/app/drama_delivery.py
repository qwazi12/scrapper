"""Server-observed normal-delivery evidence, independent of transport acceptance."""
import asyncio
import datetime as dt
import math
import tempfile
import time
from pathlib import Path
from urllib.parse import urlsplit

import httpx

from . import drama_controls as dc, drama_media as dm, drama_native as native, drama_normal as normal
from . import drama_observed_media as observed, pending_metadata as pm, publication_journal as journal
from .models import DramaRecord, PublicationEvent
from .social import upload_post

VERSION = 'drama-delivery-observation-v1'


async def download(url, platform, destination):
    parsed = urlsplit(url); host = (parsed.hostname or '').lower()
    allowed = host.endswith('.fbcdn.net') if platform == 'facebook' else host == 'video.twimg.com'
    if (not allowed or parsed.scheme != 'https' or parsed.username or parsed.password
            or parsed.port not in (None, 443)):
        raise ValueError('unsupported native media host; no fetch or redirect')
    deadline = time.monotonic() + 90; total = 0
    async with httpx.AsyncClient(timeout=60, follow_redirects=False) as client:
        async with client.stream('GET', url) as response:
            response.raise_for_status()
            if response.is_redirect or not response.headers.get('content-type', '').startswith('video/'):
                raise ValueError('native response is not a complete video')
            size = response.headers.get('content-length')
            if size and (not size.isdigit() or int(size) > observed.POLICY['max_bytes']):
                raise ValueError('native video exceeds bounded size')
            with Path(destination).open('wb') as file:
                async for chunk in response.aiter_bytes(1024 * 1024):
                    total += len(chunk)
                    if total > observed.POLICY['max_bytes'] or time.monotonic() > deadline:
                        raise ValueError('native media exceeds bounded size/time')
                    file.write(chunk)


def requests_for(s, item, ready):
    # Immutable normal intents, not mutable catchup/notes or a supplied URL.
    found = {}
    for event in s.query(PublicationEvent).filter_by(queue_identity=journal.identity(item)).order_by(PublicationEvent.id):
        value = event.payload
        if (value.get('drama_ready_id') != ready.operation_id or value.get('drama_normal_version') != normal.VERSION
                or not value.get('request_id') or len(value.get('platforms', [])) != 1):
            continue
        account = value.get('profile', '') + ':' + value['platforms'][0]
        if account in ready.data['targets']:
            found[value['request_id']] = {'entry': value, 'event_id': event.id, 'account': account}
    if len(found) > 12:
        dc.fail('more than twelve retained normal requests need explicit reconciliation before bounded verification')
    return found


async def collect(requests, target, payload, artifact_path):
    results = {}
    from .social.queue_manager import resolve_results
    for rid, request in requests.items():
        entry = request['entry']; account = request['account']; platform = entry['platforms'][0]
        try:
            status = await upload_post.get_status(rid)
            # Acceptance is only the selector for an independently fetched native
            # observation. It never constitutes delivered-content proof.
            decision = resolve_results([entry], {rid: status}, dt.datetime.now(dt.timezone.utc))
            if decision is None:
                results[account] = {'state': 'held', 'missing': ['provider completion remains uncertain'], 'verified': False}
                continue
            matches = [(a, url) for a, url in decision['ok'] if a == account]
            if len(matches) != 1:
                results[account] = {'state': 'held', 'missing': ['destination not confirmed successful'], 'verified': False}
                continue
            url = matches[0][1]
            result = await native.read(platform, url, target, payload)
            media_url = result.pop('media_url', None)  # Signed CDN credentials are never retained.
            result.update(provider_request_id=rid, source_event_id=request['event_id'],
                provider_native_post_id=native.post_id(url, platform),
                retained_attempt_id=entry['client_request_id'], plan_id=entry.get('drama_plan_id'),
                provider_status={'status':'completed','results':[{'profile_username':entry['profile'],
                    'platform':platform,'success':True,'post_url':url}]})
            if result.get('native_observed') and not result.get('missing') and media_url:
                duration = result.get('native_duration_seconds')
                if type(duration) not in (int, float) or not math.isfinite(duration) or not 0 < duration <= 142:
                    raise ValueError('native duration missing or outside bounded scope')
                with tempfile.TemporaryDirectory(prefix='drama-normal-observed-') as root:
                    path = Path(root) / 'observed.mp4'
                    await download(media_url, platform, path)
                    result['media'] = await asyncio.to_thread(observed.compare, artifact_path, path)
                media = result['media']
                duration_agrees = abs(duration - media['observed']['duration']) <= media['duration_tolerance_seconds']
                result['native_duration_matches_actual'] = duration_agrees
                result['verified'] = media['media_checks_passed'] and duration_agrees
                result['state'] = 'verified' if result['verified'] else 'held'
                if not result['verified']:
                    result['missing'] = [*media['errors'], *([] if duration_agrees else ['native duration conflicts with actual decoded video'])]
            results[account] = result
        except Exception as exc:
            # No exception message containing a URL, token, raw native body or
            # provider headers enters the durable operator evidence.
            results[account] = {'state': 'held', 'verified': False, 'provider_request_id': rid,
                'missing': ['supported native content observation unavailable: ' + type(exc).__name__]}
    return results


def verify_document(document, binding):
    if (document.get('version') != VERSION or document.get('binding') != binding
            or document.get('policy_sha256') != pm.digest(observed.POLICY)):
        return False
    targets = binding['targets']; observations = document.get('observations', {})
    if set(observations) != set(targets):
        return False
    for account in targets:
        value = observations[account]; media = value.get('media', {})
        required = ('native_id', 'native_owner', 'public_copy', 'published')
        if (value.get('native_observed') is not True or value.get('native_post_id') != value.get('provider_native_post_id')
                or not value.get('native_post_id') or not value.get('provider_request_id') or not value.get('retained_attempt_id')
                or any(value.get('checks', {}).get(k) is not True for k in required)
                or value.get('missing') or value.get('native_duration_matches_actual') is not True
                or not observed.passes(media)
                or media.get('source', {}).get('sha256') != binding['transport']['artifact_sha256']):
            return False
        owner = binding['target_identity']['facebook_page_id' if account.endswith(':facebook') else 'x_account_id']
        if value.get('native_owner_id') != owner:
            return False
    return True


def binding(s, item, ready):
    evidence = dc.binding(s, item); grant = normal.permission(s, evidence)
    selected = dm.selected(s, item)
    transport = {'original_sha256': evidence.data['sha256'],
        'artifact_sha256': selected['artifact_sha256'] if selected else evidence.data['sha256'],
        'lineage': dm.binding(selected)}
    return {'queue_identity': journal.identity(item), 'evidence_id': evidence.id,
        'ready_id': ready.operation_id, 'review_id': ready.data['review_id'], 'copy_hash': dc.copy_hash(item),
        'payload_sha256': pm.digest(dc.payload(s, item)), 'transport': transport,
        'rights_id': grant.operation_id, 'rights_document_sha256': pm.digest(grant.data['document']),
        'targets': ['Dramarama:facebook', 'Dramarama:x'], 'target_identity': grant.data['document']['target_identity']}


def verified(s, item, ready):
    row = dc.latest(s, item, 'normal_delivery')
    if not row:
        return False
    document = row.data['document']
    try:
        # A completed, immutable publication is a historical sequencing fact,
        # not a lease that expires daily. No native read occurs here. A newer
        # held observation or contradictory journal entry still closes it.
        age = (dc.now(s) - dt.datetime.fromisoformat(document['observed_at'])).total_seconds()
        if age < 0 or journal.unresolved_late_receipt(s, item):
            return False
        current = binding(s, item, ready)
        historical = document['binding']
        grant = s.query(DramaRecord).filter_by(kind='normal_rights', operation_id=historical['rights_id']).first()
        if (not grant or pm.digest(grant.data['document']) != historical['rights_document_sha256']
                or any(historical.get(k) != value for k, value in current.items()
                    if k not in ('rights_id', 'rights_document_sha256'))
                or not verify_document(document, historical)):
            return False
        # A later held observation supersedes an earlier pass, and every request
        # remains tied to its original journaled normal plan/ready/native owner.
        known = requests_for(s, item, ready)
        terminal = dc.successful_observations(s, item, ready)
        return all(value['provider_request_id'] in known
            and known[value['provider_request_id']]['entry']['client_request_id'] == value['retained_attempt_id']
            and terminal.get(account, {}).get('request_id') == value['provider_request_id']
            and terminal[account]['attempt_id'] == value['retained_attempt_id']
            and native.post_id(terminal[account]['url'], account.split(':')[-1]) == value['native_post_id']
            for account, value in document['observations'].items())
    except Exception:
        return False
