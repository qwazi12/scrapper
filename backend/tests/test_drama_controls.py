"""Real synthetic bytes, strict provider fakes, independent database sessions."""
import asyncio
import copy
import datetime as dt
import hashlib
import json
import itertools
import shutil
import subprocess
import struct
import threading
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from types import SimpleNamespace
from uuid import uuid4

import pytest
from fastapi import HTTPException
from fastapi.testclient import TestClient
from sqlalchemy import create_engine, event, text
from sqlalchemy.exc import DBAPIError

from backend.app import drama_archive, drama_controls as dc, pending_metadata as pm, publication_journal as journal
from backend.app.db import SessionLocal, engine
from backend.app.models import AppSetting, DramaEpisode, DramaRecord, PublicationEvent, QueueItem
from backend.app.social import queue_manager as qm, upload_post

_SCENARIOS = itertools.count()


@pytest.fixture(scope='module')
def original_video(tmp_path_factory):
    root = tmp_path_factory.mktemp('drama-original')
    originals = {}
    for series in range(2):
        for episode in range(1,7):
            path = root / f'original-{series}-{episode}.mp4'
            subprocess.run(['ffmpeg', '-v', 'error', '-y', '-f', 'lavfi', '-i',
                f'color=c=blue:s=540x960:r=24:d=3,drawbox=x={20+episode*15}:y={30+series*100}:w=80:h=80:color=white:t=fill',
                '-f', 'lavfi', '-i', f'sine=f={400+series*100+episode*20}:d=3', '-c:v', 'libx264', '-pix_fmt', 'yuv420p',
                '-c:a', 'aac', '-ar','48000','-ac','2','-b:a','128k','-shortest', str(path)], check=True)
            originals[series,episode] = path
    return originals


@pytest.fixture
def archive(session, monkeypatch, tmp_path, original_video):
    from backend.app.main import app
    unique = uuid4().hex
    channel = 'synthetic-' + unique
    root = 'synthetic-root-' + unique
    mid = 'synthetic-manifest-' + unique
    manifest = {'channel_id': channel, 'platform': 'tiktok', 'folder_id': root,
                'inventory_complete': True, 'items': {}}
    metas = {}
    rows, source_files = {}, {}
    for series in range(2):
        sid = 'synthetic-series-' + unique + '-' + str(series)
        parent = 'synthetic-folder-' + unique + '-' + str(series)
        metas[parent] = {'id': parent, 'parents': [root], 'appProperties': {
            'scrapper_archive_channel': channel, 'scrapper_archive_series': sid}}
        for ep in range(1, 7):
            # A valid MP4 free atom gives independent scenarios their own bytes;
            # immutable history from earlier tests must not be hidden/reset.
            local = tmp_path / f'original-{series}-{ep}.mp4'
            local.write_bytes(original_video[series,ep].read_bytes() + struct.pack('>I4s',8+len(unique),b'free') + unique.encode())
            body = local.read_bytes()
            sha, md5 = hashlib.sha256(body).hexdigest(), hashlib.md5(body).hexdigest()
            vid = str(7700000000000000000 + int(unique[:6], 16) * 100 + series * 10 + ep)
            fid = 'synthetic-video-' + unique + '-' + str(series) + '-' + str(ep)
            row = {'video_id': vid, 'series_id': sid, 'episode_number': ep,
                   'original_url': 'https://www.tiktok.com/@synthetic/video/' + vid,
                   'status': 'uploaded', 'drive_file_id': fid, 'drive_parent_id': parent,
                   'sha256': sha, 'md5': md5, 'file_size': len(body), 'full_decode_passed': True}
            manifest['items'][vid] = row
            source_files[fid] = local
            metas[fid] = {'id': fid, 'size': str(len(body)), 'md5Checksum': md5, 'sha256Checksum': sha,
                'parents': [parent], 'mimeType': 'video/mp4', 'appProperties': {
                    'scrapper_platform': 'tiktok', 'scrapper_account': channel, 'scrapper_video': vid}}
            item = QueueItem(title=f'Unreliable display name {7-ep}', description='Owner-reviewed synthetic copy',
                tags='#Synthetic', status='review', accounts=['Dramarama:facebook', 'Dramarama:x'],
                pipeline='DramaRama', drive_link='https://drive.google.com/file/d/' + fid + '/view',
                source='Synthetic archive', research={})
            session.add(item); session.flush()
            rows[series, ep] = (item.id, vid)
    session.commit()
    setting = session.get(AppSetting, dc.PLAN_KEY)
    if setting: setting.value = None
    session.commit()
    gets, downloads, transfers = [], [], []

    class Result:
        def __init__(self, value): self.value = value
        def execute(self): return copy.deepcopy(self.value)

    class Files:
        def get(self, fileId, **kw):
            gets.append(fileId)
            if fileId == mid:
                raw = json.dumps(manifest).encode()
                return Result({'id': mid, 'size': str(len(raw)), 'md5Checksum': hashlib.md5(raw).hexdigest(),
                    'parents': [root], 'appProperties': {'scrapper_archive_manifest': 'tiktok:' + channel}})
            return Result(metas[fileId])
        def get_media(self, fileId, **kw): return Result(json.dumps(manifest).encode())

    monkeypatch.setattr(drama_archive.drive_sync, 'get_drive_service', lambda: SimpleNamespace(files=lambda: Files()))
    def download(fid, **kw):
        fid = drama_archive.drive_sync.extract_drive_file_id(fid)
        path = tmp_path / (uuid4().hex + '.mp4'); shutil.copyfile(source_files[fid], path)
        downloads.append(path); return path
    monkeypatch.setattr(drama_archive.drive_sync, 'download_drive_file', download)
    monkeypatch.setattr(qm, 'wake', lambda: pytest.fail('Drama operations must not wake the generic poster'))
    clock = [dt.datetime.combine(dt.date(2027,1,1)+dt.timedelta(days=next(_SCENARIOS)), dt.time(8), dc.TZ).astimezone(dc.UTC)]
    def journal_clock(connection, clause, multiparams, params, options):
        if (getattr(clause, 'is_insert', False) and getattr(getattr(clause,'table',None),'name',None)=='publication_events'
                and 'created_at' not in params and 'created_at' not in (getattr(clause,'_values',{}) or {})):
            clause=clause.values(created_at=clock[0])
        return clause,multiparams,params
    event.listen(engine,'before_execute',journal_clock,retval=True)
    monkeypatch.setattr(dc, 'now', lambda s: clock[0])
    real_activation_gate = dc.require_activation_capability
    # Deliberate synthetic-only boundary; production has no bypass parameter.
    monkeypatch.setattr(dc, 'require_activation_capability', lambda: None)
    monkeypatch.setattr(dc, 'delivery_fidelity_verified', lambda *args: True)
    async def accounts():
        return [{'id': 'Dramarama:' + p, 'profile': 'Dramarama'} for p in ('facebook', 'x')]
    async def upload(path, **kwargs):
        assert Path(path).read_bytes() in {p.read_bytes() for p in source_files.values()}
        rid = 'synthetic-receipt-' + str(len(transfers))
        transfers.append({'id': rid, **kwargs}); return rid
    async def status(rid):
        entry = next(r for r in transfers if r['id'] == rid)
        return {'status': 'completed', 'results': [{'profile_username': 'Dramarama', 'platform': p,
            'success': True, 'post_url': 'https://example.invalid/' + rid} for p in entry['platforms']]}
    monkeypatch.setattr(upload_post, 'list_accounts', accounts)
    monkeypatch.setattr(upload_post, 'upload_video', upload)
    monkeypatch.setattr(upload_post, 'get_status', status)
    yield SimpleNamespace(client=TestClient(app, headers={'x-access-token': 'test-token'}), rows=rows,
        manifest=manifest, mid=mid, metas=metas, gets=gets, downloads=downloads, transfers=transfers,
        clock=clock, original=original_video, download=download, real_activation_gate=real_activation_gate)
    event.remove(engine,'before_execute',journal_clock)


def view(a, key):
    r = a.client.get('/api/drama/items/' + str(a.rows[key][0])); assert r.status_code == 200, r.text
    return r.json()


def inspect(a, key):
    qid, vid = a.rows[key]
    req = {'operation_id': str(uuid4()), 'expected_fingerprint': view(a, key)['fingerprint'],
           'manifest_id': a.mid, 'source_video_id': vid}
    r = a.client.post(f'/api/drama/items/{qid}/inspect', json=req)
    assert r.status_code == 200, r.text
    assert all(not p.exists() for p in a.downloads)
    return r.json(), req


def reviewed(a, key):
    proof, _ = inspect(a, key)
    qid = a.rows[key][0]
    req = {'operation_id': str(uuid4()), 'expected_fingerprint': view(a, key)['fingerprint'],
        'evidence_id': proof['evidence_id'], 'approved_revision': 'Synthetic exact-copy revision',
        'approved_workbook_sha256': hashlib.sha256(b'original synthetic review register').hexdigest(),
        'approved_proposal_sha256': pm.digest({k:view(a,key)[k] for k in pm.PUBLIC_FIELDS}),
        'decision': 'Accept this exact canonical episode and public copy for guarded release'}
    preview = a.client.post(f'/api/drama/items/{qid}/review-preview', json=req)
    assert preview.status_code == 200, preview.text
    req['acknowledged_fingerprint'] = preview.json()['acknowledged_fingerprint']
    r = a.client.post(f'/api/drama/items/{qid}/review', json=req)
    assert r.status_code == 200, r.text
    return proof, r.json(), req


def ready(a, key):
    proof, review, _ = reviewed(a, key)
    qid = a.rows[key][0]
    req = {'operation_id': str(uuid4()), 'expected_fingerprint': view(a, key)['fingerprint'],
        'evidence_id': proof['evidence_id'], 'review_id': review['review_id']}
    r = a.client.post(f'/api/drama/items/{qid}/ready', json=req)
    assert r.status_code == 200, r.text
    return r.json(), req


def planned(a, count=1):
    keys = [(series, ep) for series in range(2) for ep in range(1, count + 1)]
    for key in keys: ready(a, key)
    return plan(a, keys)


def plan(a, keys):
    state = a.client.get('/api/drama/plan').json()
    series = [view(a, (n, 1))['evidence']['series_key'] for n in range(2)]
    req = {'operation_id': str(uuid4()), 'expected_plan_fingerprint': state['fingerprint'], 'series': series,
           'items': [{'id': a.rows[k][0], 'expected_fingerprint': view(a, k)['fingerprint']} for k in keys]}
    r = a.client.post('/api/drama/plan', json=req)
    assert r.status_code == 200, r.text
    return r.json(), req


def submit(a, key):
    qid = a.rows[key][0]
    with SessionLocal() as s:
        return asyncio.run(qm.publish_queue_item(qid, s))


def at_slot(a, receipt, key):
    a.clock[0] = dt.datetime.fromisoformat(receipt['plan']['assignments'][str(a.rows[key][0])]['at'])


def reconcile(a):
    with SessionLocal() as s: return qm.reconcile_posting(s, a.clock[0])


def test_canonical_inspection_decode_and_idempotent_receipt_do_not_use_display_title(archive):
    a = archive
    proof, req = inspect(a, (0, 1))
    assert proof['evidence']['episode'] == 1 and proof['evidence']['decode'] == 'passed'
    assert 'not filename' in proof['evidence']['mapping_basis']
    before = len(a.gets)
    result = a.client.post(f'/api/drama/items/{a.rows[0,1][0]}/inspect', json=req)
    assert result.status_code == 200 and result.json()['replayed']
    assert len(a.gets) == before
    assert a.client.get('/api/drama/contract').json()['enabled_by_deployment'] is False
    assert a.client.get('/api/drama/plan').json()['plan'] is None


@pytest.mark.parametrize('damage', ['gap', 'duplicate', 'trashed', 'hash', 'props', 'media', 'incomplete', 'restricted'])
def test_false_source_or_media_proofs_are_rejected_without_binding(archive, monkeypatch, damage):
    a = archive; qid, vid = a.rows[0, 1]
    source = a.manifest['items'][vid]
    if damage == 'gap':
        del a.manifest['items'][a.rows[0, 2][1]]
    elif damage == 'duplicate': a.manifest['items'][a.rows[0,2][1]]['episode_number'] = 1
    elif damage == 'trashed': a.metas[source['drive_file_id']]['trashed'] = True
    elif damage == 'hash': a.metas[source['drive_file_id']]['sha256Checksum'] = '0' * 64
    elif damage == 'props': a.metas[source['drive_file_id']]['appProperties']['scrapper_video'] = 'wrong'
    elif damage == 'incomplete': a.manifest['inventory_complete'] = False
    elif damage == 'restricted': source['restricted'] = True
    elif damage == 'media':
        def corrupt(fid):
            path = a.download(fid); path.write_bytes(b'not playable media'); return path
        monkeypatch.setattr(drama_archive.drive_sync, 'download_drive_file', corrupt)
    req = {'operation_id': str(uuid4()), 'expected_fingerprint': view(a, (0,1))['fingerprint'],
           'manifest_id': a.mid, 'source_video_id': vid}
    result = a.client.post(f'/api/drama/items/{qid}/inspect', json=req)
    assert result.status_code in (400,409), result.text
    with SessionLocal() as s:
        assert not s.query(DramaEpisode).filter_by(queue_id=qid).first()
    assert all(not p.exists() for p in a.downloads)


def test_review_acknowledgement_covers_exact_approved_revision_and_copy(archive):
    a = archive
    _, _, req = reviewed(a, (0,1))
    qid = a.rows[0,1][0]
    req['operation_id'] = str(uuid4()); req['approved_revision'] += ' Changed approval context.'
    result = a.client.post(f'/api/drama/items/{qid}/review', json=req)
    assert result.status_code == 409
    req['acknowledged_fingerprint'] = True
    assert a.client.post(f'/api/drama/items/{qid}/review', json=req).status_code == 422


def test_guarded_ready_requires_review_and_predecessors_and_replays(archive):
    a = archive
    proof, review, _ = reviewed(a, (0,2)); qid = a.rows[0,2][0]
    req = {'operation_id': str(uuid4()), 'expected_fingerprint': view(a, (0,2))['fingerprint'],
           'evidence_id': proof['evidence_id'], 'review_id': review['review_id']}
    assert a.client.post(f'/api/drama/items/{qid}/ready', json=req).status_code == 409
    ready(a, (0,1))
    result = a.client.post(f'/api/drama/items/{qid}/ready', json=req)
    assert result.status_code == 200, result.text
    assert a.client.post(f'/api/drama/items/{qid}/ready', json=req).json()['replayed']
    with SessionLocal() as s:
        item = s.get(QueueItem, qid); assert item.status == 'ready' and item.scheduled_at is None


def test_plan_is_alternating_four_each_future_only_and_preserves_other_brands(archive):
    a = archive
    with SessionLocal() as s:
        other = QueueItem(title='Other brand stays pinned', pipeline='The ICK Room', status='ready',
            accounts=['The ICK Room:facebook'], video_path='synthetic-path', scheduled_at=a.clock[0], pinned_at=a.clock[0])
        s.add(other); s.commit(); other_id = other.id
        before = pm.fingerprint(s, other)
        general = s.get(AppSetting, 'schedule')
        original_general = copy.deepcopy(general.value) if general else None
    receipt, req = planned(a, 4)
    assignments = sorted(receipt['plan']['assignments'].values(), key=lambda r:r['at'])
    assert [dt.datetime.fromisoformat(r['at']).astimezone(dc.TZ).strftime('%H:%M') for r in assignments] == list(dc.TIMES)
    assert [r['episode'] for r in assignments] == [1,1,2,2,3,3,4,4]
    assert [r['series_key'] for r in assignments] == req['series'] * 4
    assert a.client.post('/api/drama/plan', json=req).json()['replayed']
    with SessionLocal() as s:
        assert pm.fingerprint(s, s.get(QueueItem, other_id)) == before
        general = s.get(AppSetting, 'schedule')
        assert (general.value if general else None) == original_general
        actual = {i.id: i.scheduled_at for i in s.query(QueueItem).filter(QueueItem.pipeline=='DramaRama')}
        qm.plan_schedule(s, a.clock[0] + dt.timedelta(days=20))
        for qid, at in actual.items():
            assert s.get(QueueItem, qid, populate_existing=True).scheduled_at == at


def test_publisher_holds_unverified_and_future_rows_before_download_or_provider(archive):
    a = archive
    with pytest.raises(upload_post.UploadPostError, match='review|Ready'):
        submit(a, (0,1))
    assert not a.downloads and not a.transfers
    receipt, _ = planned(a)
    before = len(a.downloads)
    with pytest.raises(upload_post.UploadPostError, match='slot'):
        submit(a, (0,1))
    assert len(a.downloads) == before and not a.transfers


def test_real_publisher_transport_boundary_and_per_destination_results_gate_next_episode(archive):
    a = archive; receipt, _ = planned(a, 2)
    at_slot(a, receipt, (0,1)); item = submit(a, (0,1))
    assert item.status == 'posting' and item.published_at is None
    assert len(a.transfers) == 2 and {r['platforms'][0] for r in a.transfers} == {'facebook','x'}
    at_slot(a, receipt, (0,2))
    with pytest.raises(upload_post.UploadPostError, match='predecessor'):
        submit(a, (0,2))
    assert reconcile(a) == 1
    submit(a, (0,2)); assert len(a.transfers) == 4
    assert reconcile(a) == 1


def test_partial_facebook_success_retries_x_only_and_blocks_next_episode(archive, monkeypatch):
    a = archive; receipt, _ = planned(a, 2)
    original_status = upload_post.get_status
    async def partial(rid):
        result = await original_status(rid)
        if result['results'][0]['platform'] == 'x':
            result['results'][0].update(success=False, error_message='Synthetic X rejection')
        return result
    monkeypatch.setattr(upload_post, 'get_status', partial)
    at_slot(a, receipt, (0,1)); submit(a,(0,1)); assert reconcile(a) == 1
    with SessionLocal() as s:
        row=s.get(QueueItem,a.rows[0,1][0]); assert row.status=='retry' and row.accounts==['Dramarama:x']
        assert row.published_at is None
    at_slot(a,receipt,(0,2))
    with pytest.raises(upload_post.UploadPostError,match='predecessor'): submit(a,(0,2))
    # Explicit new future plan retains the same approval but retries only X.
    monkeypatch.setattr(upload_post,'get_status',original_status)
    retried,_=plan(a,[(0,1),(0,2),(1,1),(1,2)])
    at_slot(a,retried,(0,1)); submit(a,(0,1)); assert reconcile(a)==1
    assert [r['platforms'][0] for r in a.transfers]==['facebook','x','x']
    at_slot(a,retried,(0,2)); submit(a,(0,2))
    assert len(a.transfers)==5


def test_stale_plan_and_rollback_never_overwrite_newer_metadata(archive):
    a=archive; receipt,req=planned(a)
    qid=a.rows[0,1][0]
    with SessionLocal() as s:
        s.get(QueueItem,qid).title='Independent newer owner copy'; s.commit()
    rollback={'operation_id':str(uuid4()),'expected_plan_fingerprint':a.client.get('/api/drama/plan').json()['fingerprint']}
    r=a.client.post('/api/drama/receipts/'+receipt['operation_id']+'/rollback',json=rollback)
    assert r.status_code==409
    req['operation_id']=str(uuid4())
    assert a.client.post('/api/drama/plan',json=req).status_code==409
    with SessionLocal() as s: assert s.get(QueueItem,qid).title=='Independent newer owner copy'


def test_plan_rollback_and_ready_rollback_are_conditional_and_keep_old_receipts_revoked(archive):
    a=archive; one, _=ready(a,(0,1)); ready(a,(1,1))
    receipt,_=plan(a,[(0,1),(1,1)])
    req={'operation_id':str(uuid4()),'expected_plan_fingerprint':a.client.get('/api/drama/plan').json()['fingerprint']}
    result=a.client.post('/api/drama/receipts/'+receipt['operation_id']+'/rollback',json=req)
    assert result.status_code==200,result.text
    assert a.client.get('/api/drama/plan').json()['plan'] is None
    req={'operation_id':str(uuid4()),'expected_plan_fingerprint':a.client.get('/api/drama/plan').json()['fingerprint']}
    result=a.client.post('/api/drama/receipts/'+one['operation_id']+'/rollback',json=req)
    assert result.status_code==200,result.text
    with SessionLocal() as s:
        row=s.get(QueueItem,a.rows[0,1][0]); assert row.status=='review'
        row.status='ready';s.commit()
        with pytest.raises(HTTPException,match='rolled back'): dc.readiness(s,row)


def test_pre_controls_v1_only_engine_is_blocked_for_drama_but_other_brand_can_claim(archive):
    a=archive; receipt,_=planned(a)
    old=create_engine(engine.url)
    if old.dialect.name=='sqlite':
        @event.listens_for(old,'connect')
        def old_v1(connection,record): connection.create_function('scrapper_publisher_protocol_v1',0,lambda:1)
    else:
        @event.listens_for(old,'connect')
        def old_v1(connection,record):
            with connection.cursor() as c:c.execute("SET scrapper.publisher_protocol_v1='original-studio-source-v1'")
            connection.commit()
    try:
        with old.begin() as c,pytest.raises(DBAPIError):
            c.execute(text("UPDATE queue_items SET status='posting' WHERE id=:qid"),{'qid':a.rows[0,1][0]})
        with SessionLocal() as s:
            other=QueueItem(title='Other brand rollback',pipeline='Movie Clips',status='ready');s.add(other);s.commit();qid=other.id
        with old.begin() as c:
            assert c.execute(text("UPDATE queue_items SET status='posting' WHERE id=:qid"),{'qid':qid}).rowcount==1
    finally:old.dispose()


def test_real_activation_is_explicitly_held_without_fidelity_capability(archive, monkeypatch):
    a=archive
    for key in [(0,1),(1,1)]: ready(a,key)
    monkeypatch.setattr(dc,'require_activation_capability',a.real_activation_gate)
    req={'operation_id':str(uuid4()),'expected_plan_fingerprint':a.client.get('/api/drama/plan').json()['fingerprint'],
         'series':[view(a,(n,1))['evidence']['series_key'] for n in range(2)],
         'items':[{'id':a.rows[n,1][0],'expected_fingerprint':view(a,(n,1))['fingerprint']} for n in range(2)]}
    preview=a.client.post('/api/drama/plan-preview',json=req)
    assert preview.status_code==200 and preview.json()['preview_only'] and preview.json()['writes']==0
    assert not preview.json()['activation']['supported']
    result=a.client.post('/api/drama/plan',json=req)
    assert result.status_code==409 and 'delivery_fidelity_verifier_unavailable' in result.text
    assert a.client.get('/api/drama/plan').json()['plan'] is None
    req['checked']=True
    assert a.client.post('/api/drama/plan',json=req).status_code==422
    assert not a.transfers


def test_one_blocked_lane_leaves_its_slots_empty_without_transferring_quota(archive):
    a=archive;inspect(a,(0,1))
    for ep in range(1,5):ready(a,(1,ep))
    receipt,_=plan(a,[(1,ep) for ep in range(1,5)])
    times=sorted(dt.datetime.fromisoformat(r['at']).astimezone(dc.TZ).strftime('%H:%M') for r in receipt['plan']['assignments'].values())
    assert times==['11:30','14:30','17:30','20:30']
    for ep in range(1,5):
        at_slot(a,receipt,(1,ep));submit(a,(1,ep));reconcile(a)
    assert len(a.transfers)==8
    with pytest.raises(upload_post.UploadPostError):submit(a,(0,1))


def test_exact_copy_overflow_holds_until_separately_approved_hashtag_preserving_variant(archive):
    a=archive;qid=a.rows[0,1][0]
    with SessionLocal() as s:
        row=s.get(QueueItem,qid);row.description='Original approved content. '*20;row.tags='#One #Two';s.commit()
    proof,_=inspect(a,(0,1))
    req={'operation_id':str(uuid4()),'expected_fingerprint':view(a,(0,1))['fingerprint'],'evidence_id':proof['evidence_id'],
         'approved_revision':'Synthetic exact copy','approved_workbook_sha256':'a'*64,
         'approved_proposal_sha256':pm.digest({k:view(a,(0,1))[k] for k in pm.PUBLIC_FIELDS}),
         'decision':'Accept this exact canonical episode and public copy for guarded release'}
    preview=a.client.post(f'/api/drama/items/{qid}/review-preview',json=req).json()
    assert any('280' in e for e in preview['document']['payload']['errors'])
    req['acknowledged_fingerprint']=preview['acknowledged_fingerprint']
    assert a.client.post(f'/api/drama/items/{qid}/review',json=req).status_code==409
    variant='Separately reviewed synthetic short caption. #One #Two'
    req.pop('acknowledged_fingerprint');req['x_variant']={'text':variant,'approved_revision':'Separate synthetic X approval','approved_sha256':pm.digest(variant)}
    preview=a.client.post(f'/api/drama/items/{qid}/review-preview',json=req).json()
    assert not preview['document']['payload']['errors']
    req['acknowledged_fingerprint']=preview['acknowledged_fingerprint']
    saved=a.client.post(f'/api/drama/items/{qid}/review',json=req)
    assert saved.status_code==200,saved.text
    assert saved.json()['review_fingerprint']==req['acknowledged_fingerprint']
    req['operation_id']=str(uuid4());req['x_variant']['text']='Drops required tags';req['x_variant']['approved_sha256']=pm.digest('Drops required tags')
    req.pop('acknowledged_fingerprint')
    assert any('hashtags' in e for e in a.client.post(f'/api/drama/items/{qid}/review-preview',json=req).json()['document']['payload']['errors'])


def test_unicode_weight_and_url_transformations_are_reported_without_editing_text(archive):
    from backend.app.drama_payloads import build,weight_upper_bound
    assert weight_upper_bound('A'*280)==280 and weight_upper_bound('界'*141)==282
    assert weight_upper_bound('😀')==2
    item=SimpleNamespace(title='Title',description='Read https://example.invalid/story',tags='#Synthetic')
    result=build(item,{'duration':3,'file_size':1024},{})
    assert any('strip' in e for e in result['errors'])
    assert result['values']['x_title']=='Title\n\nRead https://example.invalid/story\n\n#Synthetic'


def test_ready_rechecks_live_trash_after_an_earlier_good_inspection(archive):
    a=archive;proof,review,_=reviewed(a,(0,1));qid,vid=a.rows[0,1]
    a.metas[a.manifest['items'][vid]['drive_file_id']]['trashed']=True
    req={'operation_id':str(uuid4()),'expected_fingerprint':view(a,(0,1))['fingerprint'],
         'evidence_id':proof['evidence_id'],'review_id':review['review_id']}
    result=a.client.post(f'/api/drama/items/{qid}/ready',json=req)
    assert result.status_code==409
    with SessionLocal() as s:assert s.get(QueueItem,qid).status=='review'


def test_concurrent_ready_conflict_is_recoverable_only_by_committed_receipt(archive,monkeypatch):
    a=archive;proof,review,_=reviewed(a,(0,1));qid=a.rows[0,1][0]
    req={'operation_id':str(uuid4()),'expected_fingerprint':view(a,(0,1))['fingerprint'],
         'evidence_id':proof['evidence_id'],'review_id':review['review_id']}
    entered,finish=threading.Event(),threading.Event();original=drama_archive.inspect
    def held(*args):
        entered.set();assert finish.wait(10);return original(*args)
    monkeypatch.setattr(drama_archive,'inspect',held)
    with ThreadPoolExecutor(max_workers=1) as pool:
        running=pool.submit(a.client.post,f'/api/drama/items/{qid}/ready',json=req)
        assert entered.wait(10)
        assert a.client.get('/api/drama/receipts/'+req['operation_id']).status_code==404
        refused=a.client.post(f'/api/drama/items/{qid}/ready',json=req)
        assert refused.status_code==409
        finish.set();saved=running.result(timeout=20);assert saved.status_code==200,saved.text
    assert a.client.get('/api/drama/receipts/'+req['operation_id']).status_code==200
    assert a.client.post(f'/api/drama/items/{qid}/ready',json=req).json()['replayed']
    with SessionLocal() as s:assert s.query(DramaRecord).filter_by(operation_id=req['operation_id']).count()==1


def test_external_edit_during_real_hashing_rejects_stale_publisher_and_reclaims_permit(archive,monkeypatch):
    from backend.app.studio import media_quality
    from backend.app.models import DramaDispatchPermit
    a=archive;receipt,_=planned(a);qid=a.rows[0,1][0];at_slot(a,receipt,(0,1))
    original=media_quality.file_hash;changed=[]
    def racing(path):
        digest=original(path)
        if not changed:
            changed.append(True)
            def external():
                with SessionLocal() as other:
                    other.get(QueueItem,qid).title='Independent newer owner words';other.commit()
            with ThreadPoolExecutor(max_workers=1) as pool:pool.submit(external).result(timeout=10)
        return digest
    monkeypatch.setattr(media_quality,'file_hash',racing)
    with pytest.raises(upload_post.UploadPostError,match='changed|review'):submit(a,(0,1))
    assert not a.transfers
    with SessionLocal() as s:
        row=s.get(QueueItem,qid);assert row.title=='Independent newer owner words' and row.status=='ready'
        assert not s.get(DramaDispatchPermit,qid)
        assert not s.query(PublicationEvent.id).filter_by(queue_id=qid).first()


def test_atomic_permit_is_consumed_and_never_allows_a_second_legacy_claim(archive):
    from backend.app.models import DramaDispatchPermit
    a=archive;receipt,_=planned(a);at_slot(a,receipt,(0,1));submit(a,(0,1));qid=a.rows[0,1][0]
    with SessionLocal() as s:assert not s.get(DramaDispatchPermit,qid)
    with pytest.raises(upload_post.UploadPostError):submit(a,(0,1))
    assert len(a.transfers)==2


def test_midnight_retry_remains_the_original_episode_quota_unit(archive,monkeypatch):
    a=archive;receipt,_=planned(a)
    original=upload_post.get_status
    async def failed(rid):
        value=await original(rid);value['results'][0].update(success=False,error_message='Synthetic confirmed refusal');return value
    monkeypatch.setattr(upload_post,'get_status',failed)
    at_slot(a,receipt,(0,1));first_day=a.clock[0];submit(a,(0,1));reconcile(a)
    a.clock[0]+=dt.timedelta(days=1)
    retried,_=plan(a,[(0,1),(1,1)]);at_slot(a,retried,(0,1));submit(a,(0,1))
    with SessionLocal() as s:
        series=view(a,(0,1))['evidence']['series_key']
        assert len(dc.attempted_today(s,series,first_day))==1
        assert not dc.attempted_today(s,series,a.clock[0])


def test_missing_internal_episode_and_implicit_third_series_selection_are_rejected(archive):
    a=archive;receipt,_=planned(a,2)
    req={'operation_id':str(uuid4()),'expected_plan_fingerprint':a.client.get('/api/drama/plan').json()['fingerprint'],
         'series':receipt['plan']['series'],
         'items':[{'id':a.rows[0,2][0],'expected_fingerprint':view(a,(0,2))['fingerprint']}]}
    result=a.client.post('/api/drama/plan',json=req)
    assert result.status_code==409 and 'EP1' in result.text
    req['series'][0]='not-a-canonical-series'
    assert a.client.post('/api/drama/plan',json=req).status_code==409


def test_null_season_is_preserved_and_explicit_seasons_do_not_merge_by_label(archive):
    a=archive
    for ep in range(1,7):
        row=a.manifest['items'][a.rows[0,ep][1]]
        row['season']=1 if ep<=3 else 2;row['episode_number']=(ep-1)%3+1
    first,_=inspect(a,(0,1));second,_=inspect(a,(0,4));unknown,_=inspect(a,(1,1))
    assert first['evidence']['season']==1 and second['evidence']['season']==2
    assert first['evidence']['series_id']==second['evidence']['series_id']
    assert first['evidence']['series_key']!=second['evidence']['series_key']
    assert unknown['evidence']['season'] is None


def test_dst_future_slot_identity_and_restart_do_not_recreate_consumed_quota(archive):
    a=archive
    a.clock[0]=dt.datetime(2027,3,13,20,31,tzinfo=dc.TZ).astimezone(dc.UTC)
    receipt,_=planned(a)
    at=dt.datetime.fromisoformat(receipt['plan']['assignments'][str(a.rows[0,1][0])]['at'])
    assert at.hour==14 and at.astimezone(dc.TZ).hour==10  # spring EDT
    at_slot(a,receipt,(0,1));submit(a,(0,1));reconcile(a)
    source='''import sys,json,datetime as dt
from backend.app.e2e_safety import install_network_guard
install_network_guard()
from backend.app.db import SessionLocal
from backend.app import drama_controls as dc
with SessionLocal() as s:
 state=dc.state(s)
 print(json.dumps({'fingerprint':state['fingerprint'],'units':len(dc.attempted_today(s,sys.argv[1],dt.datetime.fromisoformat(sys.argv[2])))}))
'''
    key=view(a,(0,1))['evidence']['series_key']
    value=json.loads(subprocess.check_output([__import__('sys').executable,'-c',source,key,at.isoformat()],text=True))
    assert value['fingerprint']==a.client.get('/api/drama/plan').json()['fingerprint'] and value['units']==1
    a.clock[0]=dt.datetime(2027,11,6,20,31,tzinfo=dc.TZ).astimezone(dc.UTC)
    # Replan the other lane only; the finished lane remains selected and empty.
    later,_=plan(a,[(1,1)])
    at=dt.datetime.fromisoformat(later['plan']['assignments'][str(a.rows[1,1][0])]['at'])
    assert at.hour==16 and at.astimezone(dc.TZ).strftime('%H:%M')=='11:30'  # fall EST


def test_independent_process_admission_excludes_plan_and_metadata_writers(archive,tmp_path):
    a=archive;receipt,_=planned(a);qid=a.rows[0,1][0]
    req={'operation_id':str(uuid4()),'expected_plan_fingerprint':a.client.get('/api/drama/plan').json()['fingerprint'],
         'series':receipt['plan']['series'],'items':[{'id':a.rows[n,1][0],'expected_fingerprint':view(a,(n,1))['fingerprint']} for n in range(2)]}
    started,finish=tmp_path/'admitted.json',tmp_path/'release'
    source='''import sys,time,datetime as dt
from pathlib import Path
from backend.app.e2e_safety import install_network_guard
install_network_guard()
from backend.app import drama_controls as dc,worker_leases
from backend.app.db import SessionLocal
from backend.app.models import QueueItem
dc.require_activation_capability=lambda:None
dc.delivery_fidelity_verified=lambda *args:True
dc.now=lambda s:dt.datetime.fromisoformat(sys.argv[2])
with worker_leases.scope('publication:'+sys.argv[1]):
 with SessionLocal() as s:dc.admission(s,s.get(QueueItem,int(sys.argv[1])))
 Path(sys.argv[3]).write_text('synthetic admission owns publication lease')
 deadline=time.monotonic()+15
 while not Path(sys.argv[4]).exists():
  if time.monotonic()>deadline:raise RuntimeError('synthetic owner release timed out')
  time.sleep(.02)
'''
    at=receipt['plan']['assignments'][str(qid)]['at']
    process=subprocess.Popen([__import__('sys').executable,'-c',source,str(qid),at,str(started),str(finish)],stdout=subprocess.PIPE,stderr=subprocess.PIPE,text=True)
    try:
        import time
        deadline=time.monotonic()+10
        while not started.exists() and process.poll() is None and time.monotonic()<deadline:time.sleep(.02)
        assert started.exists(),process.communicate(timeout=1) if process.poll() is not None else 'admission did not start'
        assert a.client.post('/api/drama/plan',json=req).status_code==409
        token=a.client.get(f'/api/pending-metadata/{qid}').json()['fingerprint']
        changed=a.client.patch(f'/api/pending-metadata/{qid}',json={'operation_id':str(uuid4()),'expected_fingerprint':token,'title':'Cannot race admission'})
        assert changed.status_code==409
        assert view(a,(0,1))['title']!='Cannot race admission'
    finally:
        finish.touch();output,error=process.communicate(timeout=20)
        assert process.returncode==0,error
    assert not a.transfers


def test_simultaneous_reconcilers_advance_each_lane_once_without_extra_reservation(archive):
    a=archive;receipt,_=planned(a,2)
    at_slot(a,receipt,(0,1));submit(a,(0,1))
    at_slot(a,receipt,(1,1));submit(a,(1,1))
    with ThreadPoolExecutor(max_workers=2) as pool:
        results=[f.result(timeout=20) for f in [pool.submit(reconcile,a),pool.submit(reconcile,a)]]
    assert sum(results)==2 and len(a.transfers)==4
    with SessionLocal() as s:
        for n in range(2):
            row=s.get(QueueItem,a.rows[n,1][0]);assert row.status=='archived'
            assert len(dc.attempted_today(s,view(a,(n,1))['evidence']['series_key'],a.clock[0]))==1
    at_slot(a,receipt,(0,2));submit(a,(0,2))
    at_slot(a,receipt,(1,2));submit(a,(1,2))
    assert len(a.transfers)==8


def test_unknown_destination_holds_its_lane_without_resending_accepted_destination(archive,monkeypatch):
    a=archive;receipt,_=planned(a,2);original=upload_post.upload_video
    async def uncertain(path,**kwargs):
        if kwargs['platforms']==['x']:raise TimeoutError('Synthetic accepted outcome lost')
        return await original(path,**kwargs)
    monkeypatch.setattr(upload_post,'upload_video',uncertain)
    at_slot(a,receipt,(0,1));submit(a,(0,1))
    at_slot(a,receipt,(0,2))
    with pytest.raises(upload_post.UploadPostError,match='predecessor'):submit(a,(0,2))
    with pytest.raises(upload_post.UploadPostError):submit(a,(0,1))
    monkeypatch.setattr(upload_post,'upload_video',original)
    at_slot(a,receipt,(1,1));submit(a,(1,1))
    assert len(a.transfers)==3  # A Facebook once, then both healthy B destinations.


def test_manual_approve_bulk_and_post_next_cannot_create_a_dispatchable_binding(archive,monkeypatch):
    a=archive;qid=a.rows[0,1][0]
    monkeypatch.setattr(qm,'wake',lambda:None)  # These legacy routes normally wake.
    assert a.client.post(f'/api/queue/{qid}/approve').status_code==200
    assert a.client.post(f'/api/queue/{qid}/post-next').status_code==200
    assert a.client.post('/api/queue/bulk-action',json={'ids':[qid],'action':'approve'}).status_code==200
    with pytest.raises(upload_post.UploadPostError):submit(a,(0,1))
    assert not a.downloads and not a.transfers
    with SessionLocal() as s:assert s.get(QueueItem,qid).scheduled_at is None


def test_alias_history_is_not_clean_just_because_published_at_is_empty(archive):
    a=archive;qid,vid=a.rows[0,1];other_id,other_vid=a.rows[0,2]
    canonical=a.manifest['items'][vid];other=a.manifest['items'][other_vid]
    other['sha256']=canonical['sha256']
    with SessionLocal() as s:
        row=s.get(QueueItem,other_id);row.catchup={'done':[['Dramarama:facebook','https://example.invalid/partial']]};s.commit()
        assert row.published_at is None
    req={'operation_id':str(uuid4()),'expected_fingerprint':view(a,(0,1))['fingerprint'],'manifest_id':a.mid,'source_video_id':vid}
    result=a.client.post(f'/api/drama/items/{qid}/inspect',json=req)
    assert result.status_code==409 and 'history' in result.text


def test_orphaned_unattributed_history_is_a_specific_hold_not_assumed_absent(archive):
    a=archive;proof,_=inspect(a,(0,1));qid=a.rows[0,1][0]
    with SessionLocal() as s:
        s.add(PublicationEvent(event_key=uuid4().hex,queue_id=999999,queue_identity='e'*64,attempt_id='synthetic-orphan',
            kind='accepted',payload={'profile':'Dramarama','platforms':['x'],'state':'accepted'}));s.flush()
        with pytest.raises(HTTPException,match='orphaned historical'):
            dc.require_source_history(s,s.get(QueueItem,qid),proof['evidence'])
        s.rollback()  # Deliberate uncommitted fixture; no journal deletion.
