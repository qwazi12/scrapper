"""Drama-only exact-copy readiness, isolated future plans and dispatch admission.

Nothing is enabled by installing this module. Owner operations create immutable
receipts; the ordinary publisher still owns transport and per-destination results.
"""
from __future__ import annotations

import datetime as dt
import hmac
from zoneinfo import ZoneInfo

from fastapi import HTTPException
from sqlalchemy import delete, update

from . import pending_metadata as pm, publication_journal as journal, worker_leases
from .budget_reservations import lock
from .models import AppSetting, DramaDispatchPermit, DramaEpisode, DramaRecord, PublicationEvent, QueueItem

VERSION = 'drama-two-series-v1'
PLAN_KEY = 'drama_two_series_plan_v1'
PIPELINE = 'DramaRama'
TZ = ZoneInfo('America/New_York')
TIMES = ('10:00', '11:30', '13:00', '14:30', '16:00', '17:30', '19:00', '20:30')
UTC = dt.timezone.utc
COPY_FIELDS = ('title', 'description', 'tags', 'pipeline', 'source', 'drive_link', 'video_path',
               'thumb_path', 'media_kind', 'media_files', 'media_url', 'compilation_id', 'clip_id', 'research')


def activation_contract():
    return {'supported': False, 'blocker': 'delivery_fidelity_verifier_unavailable',
        'detail': 'Provider acceptance/success URLs do not prove full episode duration and exact visible copy. A supported server-side delivered-media/copy verifier is required before activating this plan.',
        'baseline_entitlements': '280-weight X caption, 140-second/512-MiB video until account entitlements are verified; no automatic threads or truncation'}


def require_activation_capability():
    contract = activation_contract()
    if not contract['supported']:
        fail(contract['blocker'] + ': ' + contract['detail'])


def delivery_fidelity_verified(s, item, ready):
    # No supported platform read currently supplies an independently checked
    # delivered duration AND exact visible copy. Do not turn a success URL or a
    # client checkbox into this missing proof. Keep the lane held until that
    # separate bounded capability is implemented and tested.
    return False


def fail(message):
    raise HTTPException(409, 'Drama controls: ' + message)


def aware(value):
    return value.replace(tzinfo=UTC) if value and value.tzinfo is None else value


def now(s):
    return worker_leases.clock(s)


def binding(s, item):
    return s.query(DramaEpisode).filter_by(queue_identity=journal.identity(item)).first()


def managed(s, item):
    return item.pipeline == PIPELINE or binding(s, item) is not None


def copy_hash(item):
    return pm.digest({'identity': journal.identity(item), **{k: getattr(item, k) for k in COPY_FIELDS}})


def targets(accounts, connected=None):
    if accounts == ['Dramarama:*']:
        if connected is None:
            fail('freeze the two allowed destinations from a current server account read before reviewing the wildcard')
        # A newly connected YouTube account is deliberately excluded. This
        # two-destination contract never inherits a future wildcard expansion.
        accounts = [a['id'] for a in connected if a.get('profile') == 'Dramarama'
                    and a.get('id') in ('Dramarama:facebook', 'Dramarama:x', 'Dramarama:twitter')]
    accounts = sorted(accounts or [])
    nets = [a.partition(':')[2] for a in accounts if a.partition(':')[0] == 'Dramarama']
    if len(accounts) != 2 or nets.count('facebook') != 1 or len(set(nets) & {'x', 'twitter'}) != 1:
        fail('select exactly the DramaRama Facebook and X destinations')
    return accounts


def require_item(s, item, *, pristine=False):
    if not item:
        raise HTTPException(404, 'Queue item not found')
    if item.pipeline != PIPELINE or item.media_kind != 'video' or not item.drive_link:
        fail('only exact archive-backed DramaRama video rows are supported')
    if item.compilation_id or item.clip_id or item.media_files or (item.research or {}).get('native_export_id'):
        fail('retain the original Studio/native workflow for this source')
    if pristine:
        pm.require_pending(s, item)
        if item.status != 'review':
            fail('readiness requires the current never-submitted Review row')


def expected(s, item, fingerprint):
    if not hmac.compare_digest(pm.fingerprint(s, item), fingerprint):
        fail('queue metadata, media, destination or protected state changed; refresh')


def latest(s, item, kind):
    return (s.query(DramaRecord).filter_by(queue_identity=journal.identity(item), kind=kind)
            .order_by(DramaRecord.id.desc()).first())


def replay(s, operation_id, request_hash):
    row = s.query(DramaRecord).filter_by(operation_id=operation_id).first()
    if not row:
        return None
    if not hmac.compare_digest(row.request_hash, request_hash):
        fail('operation ID already records a different request')
    return {**row.data['response'], 'replayed': True}


def record(s, operation_id, request_hash, kind, response, *, item=None, **data):
    response = {**response, 'operation_id': operation_id, 'response_state': 'saved_snapshot', 'replayed': False}
    row = DramaRecord(operation_id=operation_id, request_hash=request_hash, kind=kind,
        queue_id=item.id if item else None, queue_identity=journal.identity(item) if item else None,
        data={**data, 'version': VERSION, 'actor_scope': 'authenticated owner-token operation', 'response': response})
    s.add(row)
    s.flush()
    return response


def require_review(s, item, evidence_id=None, review_id=None):
    require_item(s, item)
    evidence = binding(s, item)
    review = latest(s, item, 'review')
    if (not evidence or (evidence_id and evidence.id != evidence_id) or not review
            or (review_id and review.operation_id != review_id)
            or review.data.get('evidence_id') != evidence.id or review.data.get('copy_hash') != copy_hash(item)):
        fail('current canonical media and exact approved metadata review are required')
    return evidence, review


def readiness(s, item):
    evidence, review = require_review(s, item)
    ready = latest(s, item, 'ready')
    if (not ready or ready.data['review_id'] != review.operation_id
            or ready.data['evidence_id'] != evidence.id or ready.data['copy_hash'] != copy_hash(item)):
        fail('no matching guarded Ready receipt exists for this media/copy')
    if s.query(DramaRecord.id).filter(DramaRecord.kind == 'rollback',
            DramaRecord.data['response']['rolled_back'].as_string() == ready.operation_id).first():
        fail('this Ready receipt was explicitly rolled back; a new guarded review-to-Ready operation is required')
    return evidence, ready


def dispatch_targets(s, item, ready=None):
    if ready is None:
        _, ready = readiness(s, item)
    owed = set(ready.data['targets']) - successes(s, item, ready)
    initial = ready.data.get('initial_accounts', ready.data['targets'])
    if list(item.accounts or []) != initial and not (
            reserved_before(s, journal.identity(item)) and set(item.accounts or []) == owed):
        fail('current destination selection differs from its frozen two-destination review')
    if not owed:
        fail('both reviewed destinations already succeeded; do not submit them again')
    return sorted(owed)


def payload(s, item):
    evidence, ready = readiness(s, item)
    review = latest(s, item, 'review')
    from .drama_payloads import build
    current = build(item, evidence.data, review.data['document']['review'])
    if current['errors'] or current['sha256'] != review.data['document']['payload']['sha256']:
        fail('visible platform payload differs from its exact accepted copy or supported bounds')
    return current['values']


def require_source_history(s, item, proof):
    """Include exact-hash aliases and full server-side identity journal history."""
    from .drive_sync import extract_drive_file_id
    aliases = set(proof['alias_drive_ids'])
    current = {journal.identity(row): row for row in s.query(QueueItem)}
    for identity, row in current.items():
        if identity == journal.identity(item):
            continue
        other = binding(s, row)
        same = extract_drive_file_id(row.drive_link or '') in aliases or (other and other.data['sha256'] == proof['sha256'])
        if same and (row.status not in ('review', 'ready') or row.published_at or row.outstand_post_id
                or row.publish_requests or row.catchup or s.query(PublicationEvent.id).filter_by(queue_identity=identity).first()):
            fail('this canonical media/hash alias has retained submission history on another queue identity')
    for event in s.query(PublicationEvent):
        if event.queue_identity in current:
            continue
        entry = event.payload
        is_drama = entry.get('profile') == 'Dramarama' or any(str(a).startswith('Dramarama:') for a in entry.get('accounts', []))
        if not is_drama:
            continue
        other = s.query(DramaEpisode).filter_by(queue_identity=event.queue_identity).first()
        if not other:
            fail('an orphaned historical Drama submission has no immutable media attribution; reconcile it before release')
        if other.data['sha256'] == proof['sha256']:
            fail('a deleted/replaced identity has already submitted these canonical bytes')


def successes(s, item, ready):
    """Only complete journaled provider observations prove destination success.

    catchup.done, published_at, free-form notes and an accepted request alone do
    not establish success. A contradictory later observation closes admission.
    """
    result = {}
    events = (s.query(PublicationEvent).filter_by(queue_id=item.id, queue_identity=journal.identity(item))
              .order_by(PublicationEvent.id).all())
    for event in events:
        entry = event.payload
        if entry.get('drama_ready_id') != ready.operation_id:
            continue
        status = entry.get('observed_status') or entry.get('provider_status')
        if not status:
            continue
        from .social.queue_manager import resolve_results
        if not entry.get('request_id'):
            continue
        resolved = resolve_results([entry], {entry['request_id']: status}, now(s))
        if resolved is None:
            continue
        for account, url in resolved['ok']:
            result[account] = (True, url)
        for account in resolved['failed']:
            result[account] = (False, '')
    return {a for a, (ok, _) in result.items() if ok}


def complete(s, item):
    try:
        _, ready = readiness(s, item)
    except HTTPException:
        return False
    return (set(ready.data['targets']) <= successes(s, item, ready)
            and not journal.unresolved_late_receipt(s, item) and delivery_fidelity_verified(s, item, ready))


def predecessors(s, evidence, *, dispatch=False):
    for episode in range(1, evidence.episode):
        prior = s.query(DramaEpisode).filter_by(series_key=evidence.series_key, episode=episode).first()
        if not prior or prior.data['catalog_hash'] != evidence.data['catalog_hash']:
            fail(f'canonical predecessor EP{episode} is missing or from another inventory revision')
        item = s.get(QueueItem, prior.queue_id, populate_existing=True)
        if not item or journal.identity(item) != prior.queue_identity:
            fail(f'predecessor EP{episode} was deleted or replaced')
        if dispatch:
            if not complete(s, item):
                fail(f'predecessor EP{episode} is not confirmed on both required destinations')
        else:
            readiness(s, item)
            if item.status not in ('ready', 'posting', 'archived', 'posted') or (item.status in ('archived', 'posted') and not complete(s, item)):
                fail(f'predecessor EP{episode} is not safely ready or confirmed complete')


def state(s):
    setting = s.get(AppSetting, PLAN_KEY, populate_existing=True)
    value = setting.value if setting else None
    return {'fingerprint': pm.digest(value), 'plan': value, 'version': VERSION,
            'timezone': TZ.key, 'times': TIMES, 'active_series_limit': 2, 'per_series_daily_limit': 4}


def plan_record(s):
    value = state(s)['plan']
    if not value:
        fail('no owner-activated isolated two-series plan')
    row = s.query(DramaRecord).filter_by(operation_id=value.get('operation_id'), kind='plan').first()
    if not row or pm.digest(row.data['plan']) != pm.digest(value):
        fail('active plan does not match its immutable receipt')
    return row


def require_plan_switch(s, previous, series):
    if previous and previous.get('series') != series and attempted_today(s, None, now(s)):
        fail('active lane selection cannot rotate/refill on a day that already has Drama episode reservations')
    for key in (previous or {}).get('series', []):
        if key in series:
            continue
        episodes = s.query(DramaEpisode).filter_by(series_key=key).all()
        if not episodes or {e.episode for e in episodes} != set(range(1, episodes[0].total_episodes + 1)):
            fail('cannot rotate an active series with missing canonical predecessors')
        if any(not (item := s.get(QueueItem, e.queue_id, populate_existing=True)) or
               journal.identity(item) != e.queue_identity or not complete(s, item) for e in episodes):
            fail('cannot rotate an active series until every episode succeeds on both destinations')


def prepare_plan(s, operation_id, series, items):
    """Pure preparation under the caller's mutex; no other brand is replanned."""
    if len(series) != 2 or len(set(series)) != 2 or not items or len(items) != len({i.id for i in items}):
        fail('choose two distinct series and an explicit nonempty unique queue selection')
    previous = state(s)['plan']
    require_plan_switch(s, previous, series)
    grouped = {key: [] for key in series}
    if any(not s.query(DramaEpisode.id).filter_by(series_key=key).first() for key in series):
        fail('both lane identities need actual canonical archive evidence; names are not series keys')
    for item in items:
        require_item(s, item)
        evidence, ready = readiness(s, item)
        if evidence.series_key not in grouped or item.status not in ('ready', 'retry'):
            fail('selected rows must be guarded Ready or proven failed retries in the two active series')
        if journal.unresolved_late_receipt(s, item) or any(e.get('state') in ('intent', 'unknown', 'accepted') for e in item.publish_requests or []):
            fail('uncertain/submitted rows cannot be rescheduled')
        dispatch_targets(s, item, ready)
        if item.status == 'retry' and not item.publish_requests:
            fail('retry has no retained provider failure evidence')
        grouped[evidence.series_key].append((evidence, item))
    for key, entries in grouped.items():
        if not entries:
            continue  # Blocked/finished lane stays empty; its quota is not moved.
        entries.sort(key=lambda pair: pair[0].episode)
        catalog_hash = entries[0][0].data['catalog_hash']
        if any(e.data['catalog_hash'] != catalog_hash for e, _ in entries):
            fail('series inventory revision differs')
        selected = {e.episode for e, _ in entries}
        for ep in range(1, entries[-1][0].episode + 1):
            if ep in selected:
                continue
            evidence = s.query(DramaEpisode).filter_by(series_key=key, episode=ep).first()
            prior = s.get(QueueItem, evidence.queue_id, populate_existing=True) if evidence else None
            if not prior or journal.identity(prior) != evidence.queue_identity or not complete(s, prior):
                fail(f'selected series skips unresolved EP{ep}')
    cutoff = now(s) + dt.timedelta(minutes=1)
    first_day = cutoff.astimezone(TZ).date()
    assignments = {}
    consumed_slots = {row.payload.get('drama_slot') for row in s.query(PublicationEvent).filter_by(kind='batch_intent')}
    # All selected future rows receive explicit slots; no missed slot is reused.
    for day in range(len(items) + 1):
        date = first_day + dt.timedelta(days=day)
        day_start = dt.datetime.combine(date, dt.time.min, TZ).astimezone(UTC)
        used = {key: attempted_today(s, key, day_start) for key in series}
        used_total = attempted_today(s, None, day_start)
        for n, hhmm in enumerate(TIMES):
            h, m = map(int, hhmm.split(':'))
            at = dt.datetime.combine(date, dt.time(h, m), TZ).astimezone(UTC)
            key = series[n % 2]
            if at <= cutoff or at.isoformat() in consumed_slots or not grouped[key]:
                continue
            # Attempts already made today count toward the per-series quota,
            # including partial/unknown outcomes; retries cannot make a burst.
            candidate = journal.identity(grouped[key][0][1])
            reserved = reserved_before(s, candidate)
            if not reserved and ((candidate not in used[key] and len(used[key]) >= 4) or (candidate not in used_total and len(used_total) >= 8)):
                continue
            evidence, item = grouped[key].pop(0)
            if not reserved:
                used[key].add(candidate); used_total.add(candidate)
            assignments[str(item.id)] = {'queue_identity': journal.identity(item), 'at': at.isoformat(),
                'evidence_id': evidence.id, 'series_key': key, 'episode': evidence.episode,
                'ready_id': latest(s, item, 'ready').operation_id}
        if not any(grouped.values()):
            break
    if any(grouped.values()):
        fail('future plan could not assign every selected episode')
    return {'operation_id': operation_id, 'version': VERSION, 'series': series, 'timezone': TZ.key,
            'times': list(TIMES), 'assignments': assignments, 'created_at': now(s).isoformat()}


def attempted_today(s, series_key, at):
    day = aware(at).astimezone(TZ).date()
    start = dt.datetime.combine(day, dt.time.min, TZ).astimezone(UTC)
    end = dt.datetime.combine(day + dt.timedelta(days=1), dt.time.min, TZ).astimezone(UTC)
    # The first durable episode reservation owns its local-day unit. Subsequent
    # retries/callbacks (including after midnight) never become a new episode.
    events = s.query(PublicationEvent).filter(PublicationEvent.kind == 'batch_intent').order_by(PublicationEvent.id).all()
    result = set()
    first = set()
    for event in events:
        if event.payload.get('drama_series_key'):
            if event.queue_identity in first:
                continue
            first.add(event.queue_identity)
            if start <= aware(event.created_at) < end and (series_key is None or event.payload['drama_series_key'] == series_key):
                result.add(event.queue_identity)
        elif series_key is None:
            item = s.get(QueueItem, event.queue_id)
            if (item and item.pipeline == PIPELINE and journal.identity(item) == event.queue_identity
                    and start <= aware(event.created_at) < end):
                result.add(event.queue_identity)
    if series_key is None:
        result.update(journal.identity(item) for item in s.query(QueueItem).filter(
            QueueItem.pipeline == PIPELINE, QueueItem.published_at >= start, QueueItem.published_at < end))
    return result


def reserved_before(s, identity):
    return bool(s.query(PublicationEvent.id).filter(PublicationEvent.kind == 'batch_intent',
        PublicationEvent.queue_identity == identity,
        PublicationEvent.payload['drama_series_key'].as_string().isnot(None)).first())


def admission(s, item, *, media_sha256=None, final=False):
    """Called inside the existing publication lease; final caller owns DB mutex."""
    require_activation_capability()
    evidence, ready = readiness(s, item)
    require_source_history(s, item, evidence.data)
    plan = plan_record(s)
    assignment = plan.data['plan']['assignments'].get(str(item.id))
    if (not assignment or assignment['queue_identity'] != journal.identity(item)
            or assignment['ready_id'] != ready.operation_id or assignment['evidence_id'] != evidence.id
            or evidence.series_key not in plan.data['plan']['series']):
        fail('no current exact-series assignment for this queue identity')
    at = aware(dt.datetime.fromisoformat(assignment['at']))
    instant = now(s)
    # Manual Post Now has the same cadence and predecessor restrictions.
    if (item.status not in ('ready', 'retry') or aware(item.scheduled_at) != at
            or instant < at or instant >= at + dt.timedelta(minutes=5)):
        fail('only the current five-minute future-assigned slot can submit; missed slots need a new plan')
    if s.query(PublicationEvent.id).filter(PublicationEvent.kind == 'batch_intent',
            PublicationEvent.payload['drama_slot'].as_string() == assignment['at']).first():
        fail('this slot was already attempted; reconcile and assign a new future slot')
    if item.published_at or item.outstand_post_id or journal.unresolved_late_receipt(s, item):
        fail('published, legacy or uncertain rows cannot be dispatched')
    dispatch_targets(s, item, ready)
    if any(e.get('state') in ('intent', 'unknown', 'accepted') for e in item.publish_requests or []):
        fail('a destination submission remains unresolved')
    predecessors(s, evidence, dispatch=True)
    attempted = attempted_today(s, evidence.series_key, instant)
    retry = reserved_before(s, journal.identity(item))
    if not retry and journal.identity(item) not in attempted and len(attempted) >= 4:
        fail('this series already used its four daily episode attempts')
    total = attempted_today(s, None, instant)
    if not retry and journal.identity(item) not in total and len(total) >= 8:
        fail('DramaRama already used its eight daily episode attempts')
    if final and media_sha256 != evidence.data['sha256']:
        fail('transport bytes differ from the server-inspected and owner-reviewed canonical media')
    return {'drama_ready_id': ready.operation_id, 'drama_plan_id': plan.operation_id,
            'drama_series_key': evidence.series_key, 'drama_episode': evidence.episode,
            'drama_slot': assignment['at']}


def conditional_update(s, item, **changes):
    changed = s.execute(update(QueueItem).where(*pm._same_snapshot(item)).values(**changes),
                        execution_options={'synchronize_session': False}).rowcount
    if changed != 1:
        fail('queue changed at the atomic application boundary')
    s.refresh(item)


def grant_dispatch(s, item, attempt_id):
    """Caller has validated under its publisher lease, media claim and mutex.

    Never commit a permit separately. The posting UPDATE consumes it with built-in
    database SQL, including on a legacy connection after a source rollback.
    """
    handle = worker_leases.current()
    if not handle or handle.resource != f'publication:{item.id}':
        fail('a generation-fenced publication owner is required for an atomic permit')
    worker_leases.guard(s)
    s.execute(delete(DramaDispatchPermit).where(DramaDispatchPermit.queue_id == item.id))
    s.add(DramaDispatchPermit(queue_id=item.id, queue_created_at=item.created_at,
        attempt_id=attempt_id, expires_at=worker_leases.clock(s) + dt.timedelta(seconds=30)))
    s.flush()
