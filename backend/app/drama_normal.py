"""Versioned ordinary Drama release: explicit rights, vacant lanes, native targets.

Installation grants no rights, selects no series and does not activate a plan.
Only authenticated, conditional coordinator operations record existing authority.
"""
from __future__ import annotations

import asyncio
import contextvars
import datetime as dt
import time
from contextlib import contextmanager
from dataclasses import dataclass
from pathlib import Path

from . import drama_controls as dc, drama_media as dm, pending_metadata as pm, publication_journal as journal
from .models import DramaRecord, PublicationEvent, QueueItem
from .social import upload_post

VERSION = 'drama-normal-v2'
RIGHTS_VERSION = 'drama-series-permission-v1'
_dispatch = contextvars.ContextVar('drama_normal_dispatch', default=None)


def target_identity(value):
    keys=('profile','accounts','facebook_connection_id','facebook_page_id','facebook_pinned_page_id',
        'profile_pinned_page_id','x_account_id','x_handle')
    if any(value.get(k) in (None,'',[]) for k in keys):
        dc.fail('complete native identity/pins are required; display names are not destination IDs')
    return {k:value[k] for k in keys}


def rows(s, kind):
    return s.query(DramaRecord).filter_by(kind=kind).order_by(DramaRecord.id.desc()).all()


def rights(s, series):
    found = next((r for r in rows(s, 'normal_rights') if r.data['document']['series_key'] == series), None)
    if found and any(r.data['rights_id'] == found.operation_id for r in rows(s, 'normal_rights_revoke')):
        return None
    return found


def permission_state(s, series):
    history = [r for r in rows(s, 'normal_rights') if r.data['document']['series_key'] == series]
    revoked = [r.operation_id for r in rows(s, 'normal_rights_revoke')
               if r.data['series_key'] == series]
    return pm.digest({'latest': history[0].operation_id if history else None, 'revocations': revoked})


def permission(s, evidence, expected_id=None):
    row = rights(s, evidence.series_key)
    if not row or (expected_id and row.operation_id != expected_id):
        dc.fail('current versioned permission for this exact series is missing, revoked or superseded')
    doc = row.data['document']
    if (doc['version'] != RIGHTS_VERSION or doc['catalog_hash'] != evidence.data['catalog_hash']
            or doc['series_id'] != evidence.data['series_id'] or doc['season'] != evidence.data['season']
            or doc['destinations'] != ['Dramarama:facebook', 'Dramarama:x']):
        dc.fail('permission does not bind this canonical inventory and exact Facebook/X pair')
    return row


def is_plan(s):
    plan = dc.state(s)['plan']
    return bool(plan and plan.get('version') == VERSION)


def snapshot(s, item, changes=None):
    value = {c.key: getattr(item, c.key) for c in QueueItem.__table__.columns
             if c.key not in ('status', 'publish_requests', 'published_at')}
    value.update(changes or {})
    return pm.digest({'queue': value, 'source_binding': pm._binding(s, item)})


def prepare_plan(s, operation_id, lanes, items, identities):
    if len(lanes) != 2 or not any(lanes) or (lanes[0] is not None and lanes[0] == lanes[1]):
        dc.fail('supply exactly lane A and lane B; vacant means null, never a replacement series')
    active = [key for key in lanes if key is not None]
    if len(items) != len({item.id for item in items}) or not items:
        dc.fail('an explicit nonempty unique item selection is required')
    previous = dc.state(s)['plan']
    selected_ids = {str(item.id) for item in items}
    for qid, assignment in (previous or {}).get('assignments', {}).items():
        if str(qid) in selected_ids:
            continue
        prior = s.get(QueueItem, int(qid), populate_existing=True)
        if (not prior or journal.identity(prior) != assignment['queue_identity'] or not dc.complete(s, prior)):
            dc.fail('replacement plan omits an unresolved assignment; include it with its current fingerprint or reconcile it before replacing the plan')
    if previous and previous.get('series')!=lanes and attempts_today(s,None,dc.now(s)):
        dc.fail('occupied/vacant lane positions cannot rotate or refill on a day with retained attempts')
    old = [key for key in (previous or {}).get('series', []) if key is not None]
    if old != active:
        dc.require_plan_switch(s, {**previous, 'series': old} if previous else None, active)
    grouped = {key: [] for key in active}
    permissions = {}
    for item in items:
        dc.require_item(s, item)
        evidence, ready = dc.readiness(s, item)
        if evidence.series_key not in grouped or item.status not in ('ready', 'retry'):
            dc.fail('every selected item must be reviewed Ready or a proven failed retry in an occupied lane')
        grant = permission(s, evidence)
        if ready.data['targets']!=grant.data['document']['destinations']:
            dc.fail('Ready destinations differ from the exact permission pair')
        permissions[evidence.series_key] = grant
        if identities != grant.data['document']['target_identity']:
            dc.fail('native Page/X identity or pin changed; no automatic account switching')
        if (journal.unresolved_late_receipt(s, item)
                or any(e.get('state') in ('intent', 'unknown', 'accepted') for e in item.publish_requests or [])):
            dc.fail('partial in-flight or uncertain submissions cannot be replanned')
        if item.status == 'retry' and not item.publish_requests:
            dc.fail('retry lacks retained definite failure evidence')
        dc.dispatch_targets(s, item)
        dc.require_source_history(s, item, evidence.data)
        dc.payload(s, item)
        grouped[evidence.series_key].append((evidence, item, ready))
    # An occupied empty lane still needs its own current exact permission. A
    # vacant lane has no identity or permission and never consumes another quota.
    for key in active:
        grant = rights(s, key)
        if not grant or grant.data['document']['target_identity'] != identities:
            dc.fail('every occupied lane needs its own current permission and matching native identity')
        permissions[key] = grant
    for key, entries in grouped.items():
        entries.sort(key=lambda value: value[0].episode)
        selected = {value[0].episode for value in entries}
        for evidence, item, _ in entries:
            permission(s, evidence, permissions[key].operation_id)
            for ep in range(1, evidence.episode):
                if ep in selected:
                    continue
                from .models import DramaEpisode
                prior = s.query(DramaEpisode).filter_by(series_key=key, episode=ep).first()
                row = s.get(QueueItem, prior.queue_id, populate_existing=True) if prior else None
                if (not row or journal.identity(row) != prior.queue_identity
                        or prior.data['catalog_hash'] != evidence.data['catalog_hash'] or not dc.complete(s, row)):
                    dc.fail(f'selected series skips unresolved EP{ep}')
    cutoff = dc.now(s) + dt.timedelta(minutes=1)
    first_day = cutoff.astimezone(dc.TZ).date()
    consumed = {r.payload.get('drama_slot') for r in s.query(PublicationEvent).filter_by(kind='batch_intent')}
    assignments = {}
    for offset in range(len(items) + 2):
        day = first_day + dt.timedelta(days=offset)
        start = dt.datetime.combine(day, dt.time.min, dc.TZ).astimezone(dc.UTC)
        used = {key: attempts_today(s, key, start) for key in active}
        total = attempts_today(s, None, start)
        for index, hhmm in enumerate(dc.TIMES):
            key = lanes[index % 2]
            if key is None or not grouped[key]:
                continue
            hour, minute = map(int, hhmm.split(':'))
            slot = dt.datetime.combine(day, dt.time(hour, minute), dc.TZ).astimezone(dc.UTC)
            if slot <= cutoff or slot.isoformat() in consumed:
                continue
            evidence, item, ready = grouped[key][0]
            identity = journal.identity(item)
            # A retry counts a transfer attempt on its actual new local day as
            # well; crossing midnight never grants unbounded attempt capacity.
            if len(used[key]) >= 4 or len(total) >= 8:
                continue
            grouped[key].pop(0)
            used[key].add('planned:'+identity); total.add('planned:'+identity)
            assignments[str(item.id)] = {'queue_identity': identity, 'at': slot.isoformat(),
                'evidence_id': evidence.id, 'series_key': key, 'episode': evidence.episode,
                'ready_id': ready.operation_id, 'rights_id': permissions[key].operation_id,
                'review_id': ready.data['review_id'],
                'contract': snapshot(s, item, {'scheduled_at': slot, 'pinned_at': None})}
        if not any(grouped.values()):
            break
    if any(grouped.values()):
        dc.fail('future plan could not allocate every selected item without quota transfer')
    return {'version': VERSION, 'operation_id': operation_id, 'series': lanes,
        'rights': {key: row.operation_id for key, row in permissions.items()},
        'target_identity': identities, 'times': list(dc.TIMES), 'timezone': dc.TZ.key,
        'assignments': assignments,
        'max_daily_posts_with_current_lanes': 4 * len(active),
        'no_catchup': True, 'vacant_lane_refill': False}


def transport_proof(s, item, original_hash, transport):
    evidence = dc.binding(s, item)
    selected = dm.selected(s, item)
    expected = {'original_sha256': evidence.data['sha256'],
        'artifact_sha256': selected['artifact_sha256'] if selected else evidence.data['sha256'],
        'lineage': dm.binding(selected)}
    if original_hash != evidence.data['sha256'] or transport != expected:
        dc.fail('final original/artifact/lineage differs from the exact reviewed transport')
    return expected


def admission(s, item, *, final=False, media_sha256=None, transport=None, claimed=False):
    evidence, ready = dc.readiness(s, item)
    plan_row = dc.plan_record(s); plan = plan_row.data['plan']
    if plan.get('version') != VERSION:
        dc.fail('use a versioned normal rights/native-bound plan; legacy plans are not activation authority')
    assignment = plan['assignments'].get(str(item.id))
    if (not assignment or assignment['queue_identity'] != journal.identity(item)
            or assignment['evidence_id'] != evidence.id or assignment['ready_id'] != ready.operation_id
            or assignment['review_id'] != ready.data['review_id']
            or assignment['contract'] != snapshot(s, item)):
        dc.fail('normal plan source/copy/review/protected state changed')
    grant = permission(s, evidence, assignment['rights_id'])
    if grant.data['document']['target_identity'] != plan['target_identity']:
        dc.fail('plan and current permission native targets differ')
    slot = dt.datetime.fromisoformat(assignment['at']); now = dc.now(s)
    if (item.status not in (('posting',) if claimed else ('ready', 'retry'))
            or dc.aware(item.scheduled_at) != slot or not slot <= now < slot + dt.timedelta(minutes=5)):
        dc.fail('only the original future-assigned five-minute slot may dispatch; missed slots need a new conditional plan')
    dc.require_source_history(s, item, evidence.data)
    dc.predecessors(s, evidence, dispatch=True)
    dc.dispatch_targets(s, item, ready)
    dc.payload(s, item)
    if not claimed:
        if s.query(PublicationEvent.id).filter(PublicationEvent.kind == 'batch_intent',
                PublicationEvent.payload['drama_slot'].as_string() == assignment['at']).first():
            dc.fail('this slot already has an attempt; never reuse it')
        if (item.published_at or item.outstand_post_id or journal.unresolved_late_receipt(s, item)
                or any(e.get('state') in ('intent', 'unknown', 'accepted') for e in item.publish_requests or [])):
            dc.fail('published, partial or uncertain submission requires reconciliation without a blind resend')
        if len(attempts_today(s, evidence.series_key, now)) >= 4 or len(attempts_today(s, None, now)) >= 8:
            dc.fail('actual local-day attempt quota exhausted')
    if final or claimed:
        current = _dispatch.get()
        if (not current or current.plan_id != plan_row.operation_id
                or current.identity != plan['target_identity'] or time.monotonic() - current.checked > 30):
            dc.fail('fresh exact native Page/X read is required before an intent')
        for path, expected in current.paths:
            if dm.file_hash(path) != expected:
                dc.fail('actual original or reviewed artifact changed before intent')
        transport_proof(s, item, media_sha256 or evidence.data['sha256'], transport or current.transport)
    return {'drama_ready_id': ready.operation_id, 'drama_plan_id': plan_row.operation_id,
        'drama_series_key': evidence.series_key, 'drama_episode': evidence.episode,
        'drama_slot': assignment['at'], 'drama_normal_version': VERSION,
        'drama_rights_id': grant.operation_id, 'drama_target_sha256': pm.digest(plan['target_identity'])}


def attempts_today(s, series, now):
    day = now.astimezone(dc.TZ).date()
    return {event.attempt_id for event in s.query(PublicationEvent).filter_by(kind='batch_intent')
        if event.payload.get('drama_series_key') and (series is None or event.payload['drama_series_key'] == series)
        and dc.aware(event.created_at).astimezone(dc.TZ).date() == day}


@dataclass
class Dispatch:
    plan_id: str
    identity: dict
    transport: dict
    paths: list
    checked: float


@contextmanager
def scope():
    token = _dispatch.set(None)
    try:
        yield
    finally:
        _dispatch.reset(token)


async def refresh():
    value = _dispatch.get()
    if not value:
        return
    actual = target_identity(await upload_post.canary_target_identity(value.identity['facebook_page_id']))
    if actual != value.identity:
        raise upload_post.PayloadBlocked('Native Page/X identity or pin changed; no automatic retargeting')
    value.checked = time.monotonic()


async def prepare(s, item, original, artifact, transport):
    if not is_plan(s):
        return
    plan = dc.plan_record(s)
    value = Dispatch(plan.operation_id, plan.data['plan']['target_identity'], transport,
        [(Path(original), transport['original_sha256']), (Path(artifact), transport['artifact_sha256'])], 0)
    _dispatch.set(value)
    await refresh()


def payload(values):
    current = _dispatch.get()
    return {**values, 'facebook_page_id': current.identity['facebook_page_id']} if current else values


def current():
    return _dispatch.get()


async def before_destination(account):
    if _dispatch.get():
        await refresh()


def guard_intent(s, item, entry):
    value = _dispatch.get()
    if not value:
        return
    admission(s, item, final=True, claimed=True)
    account = entry.get('profile', '') + ':' + (entry.get('platforms') or [''])[0]
    if account not in dc.dispatch_targets(s, item):
        raise upload_post.PayloadBlocked('Destination already succeeded or differs from the frozen plan')
    expected = payload(dc.payload(s, item)); prefix = 'facebook_' if account.endswith(':facebook') else 'x_'
    if entry.get('visible_payload') != {k: v for k, v in expected.items() if k.startswith(prefix)}:
        raise upload_post.PayloadBlocked('Actual normal transport payload differs from approved exact copy')
    if s.query(PublicationEvent.id).filter_by(queue_identity=journal.identity(item),
            attempt_id=entry['client_request_id'], kind='intent').first():
        raise upload_post.PayloadBlocked('This destination intent already exists; no replay dispatch')
