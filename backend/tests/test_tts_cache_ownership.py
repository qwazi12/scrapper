"""Actual audio bytes/DB file claims; speech-provider boundary is simulated."""
from concurrent.futures import ThreadPoolExecutor
from contextlib import contextmanager
import threading

import pytest
from backend.app import cleanup,costs,file_use
from backend.app.studio import tts,media,runner


@pytest.fixture
def voice(tmp_path,monkeypatch):
    from uuid import uuid4
    from backend.app.config import settings
    original=tmp_path/'original-tone.mp3'
    media.run(['ffmpeg','-v','error','-y','-f','lavfi','-i','sine=f=523:d=1','-c:a','libmp3lame',str(original)])
    cache=settings.data_path/'studio'/'_ttscache'/('case-'+uuid4().hex);cache.mkdir(parents=True)
    monkeypatch.setattr(tts,'cache_dir',lambda:cache)
    monkeypatch.setattr(tts,'engine',lambda:'chirp')
    monkeypatch.setattr(costs,'check_budget',lambda:None)
    return cache,original.read_bytes()


def test_cache_hit_survives_lru_attempt_during_actual_copy(voice,tmp_path,monkeypatch):
    cache,audio=voice;calls=[]
    def provide(text,dest):calls.append(text);dest.write_bytes(audio)
    monkeypatch.setattr(tts,'_chirp',provide)
    tts.synth('Original tone cache reader fixture.',tmp_path/'first.mp3')
    original_copy=tts.shutil.copyfile
    def copy_with_cleanup(source,dest,*args,**kwargs):
        # Cleanup is a separate invocation. Same-invocation nested mutations
        # intentionally share ownership and are not a background LRU race.
        with ThreadPoolExecutor(max_workers=1) as pool:result=pool.submit(cleanup.prune_lru_cache,cache,0).result(timeout=5)
        assert result['files_removed']==0,'An active cache reader lost its source'
        return original_copy(source,dest,*args,**kwargs)
    monkeypatch.setattr(tts.shutil,'copyfile',copy_with_cleanup)
    out=tts.synth('Original tone cache reader fixture.',tmp_path/'second.mp3')
    assert out.read_bytes()==audio and len(calls)==1 and media.duration(out)>0


def test_partial_provider_file_survives_lru_until_atomic_publication(voice,tmp_path,monkeypatch):
    cache,audio=voice;entered=threading.Event();release=threading.Event()
    def provide(text,dest):
        dest.write_bytes(audio[:100]);entered.set();assert release.wait(5)
        dest.write_bytes(audio)
    monkeypatch.setattr(tts,'_chirp',provide)
    with ThreadPoolExecutor(max_workers=1) as pool:
        future=pool.submit(tts.synth,'Original protected publication fixture.',tmp_path/'complete.mp3')
        try:
            assert entered.wait(5)
            result=cleanup.prune_lru_cache(cache,0)
            assert result['files_removed']==0,'Cleanup removed a provider response still being written'
        finally:release.set()
        assert future.result(timeout=5).read_bytes()==audio
    assert not list(cache.glob('*.tmp.mp3'))


@pytest.mark.parametrize('through_runner',[False,True])
def test_two_consumers_reuse_one_provider_generation_without_directory_deadlock(voice,tmp_path,monkeypatch,through_runner):
    cache,audio=voice;entered=threading.Event();contested=threading.Event();release=threading.Event();calls=[]
    original_pin=file_use.pin
    @contextmanager
    def observe_claim(*args,**kwargs):
        try:
            with original_pin(*args,**kwargs) as handle:yield handle
        except file_use.FileBusy:
            contested.set();raise
    monkeypatch.setattr(file_use,'pin',observe_claim)
    def provide(text,dest):
        calls.append(text)
        if len(calls)>1:contested.set()
        entered.set();assert release.wait(5);dest.write_bytes(audio)
    monkeypatch.setattr(tts,'_chirp',provide)
    if through_runner:
        from backend.app.db import SessionLocal
        from backend.app.models import StudioProject
        with SessionLocal() as s:
            projects=[StudioProject(tmdb_id=997771+i,title=f'Cache consumer {i}') for i in range(2)];s.add_all(projects);s.commit();ids=[p.id for p in projects]
        monkeypatch.setitem(runner.STAGES,'plan',lambda pid:tts.synth('Original shared voice fixture.',tmp_path/f'{pid}.mp3') and 'voiced')
        def invoke(i):runner.run_one(ids[i],'plan');return tmp_path/f'{ids[i]}.mp3'
    else:
        def invoke(i):return tts.synth('Original shared voice fixture.',tmp_path/f'{i}.mp3')
    with ThreadPoolExecutor(max_workers=2) as pool:
        first=pool.submit(invoke,0)
        try:
            assert entered.wait(5);second=pool.submit(invoke,1)
            assert contested.wait(5),'Second consumer never reached shared-cache contention'
        finally:release.set()
        outputs=[first.result(timeout=5),second.result(timeout=5)]
    assert len(calls)==1,'Same-key concurrent misses produced duplicate provider requests'
    assert all(out.read_bytes()==audio for out in outputs)
    assert len(list(cache.glob('*.mp3')))==1


def test_busy_cache_wait_is_bounded_and_never_enters_the_consumer(tmp_path):
    from backend.app import cache_use
    target=tmp_path/'claimed.mp3';entered=[]
    def consume():
        with pytest.raises(file_use.FileBusy):
            with cache_use.writer([target],wait_seconds=.02):entered.append(True)
    with file_use.pin([target],exclusive=True),ThreadPoolExecutor(max_workers=1) as pool:
        pool.submit(consume).result(timeout=5)
    assert not entered


def test_cache_body_busy_error_does_not_repeat_generation(tmp_path):
    from backend.app import cache_use
    entered=[]
    with pytest.raises(file_use.FileBusy):
        with cache_use.writer([tmp_path/'claimed.mp3']):
            entered.append(True);raise file_use.FileBusy()
    assert len(entered)==1


def test_lost_cache_generation_cannot_publish_provider_bytes(voice,tmp_path,monkeypatch):
    from sqlalchemy import update
    from backend.app.db import engine
    from backend.app.models import WorkerLease
    from backend.app.worker_leases import LeaseLost
    cache,audio=voice
    def provide(text,dest):
        dest.write_bytes(audio)
        handle=file_use._active.get()[-1]
        # Simulate an independent worker taking over the DB generation.
        with engine.begin() as db:
            db.execute(update(WorkerLease).where(WorkerLease.resource==handle.resource).values(generation=WorkerLease.generation+1))
    monkeypatch.setattr(tts,'_chirp',provide)
    output=tmp_path/'must-not-exist.mp3'
    with pytest.raises(LeaseLost):tts.synth('Original stale writer fixture.',output)
    assert not output.exists() and not list(cache.glob('*.mp3'))


def test_stopped_consumer_leaves_cache_wait_before_generation(tmp_path,monkeypatch):
    from backend.app import cache_use,control
    busy=threading.Event();jobs=[];entered=[];original_pin=file_use.pin
    @contextmanager
    def observed(*args,**kwargs):
        try:
            with original_pin(*args,**kwargs) as handle:yield handle
        except file_use.FileBusy:busy.set();raise
    monkeypatch.setattr(file_use,'pin',observed)
    target=tmp_path/'claimed.mp3'
    def consume():
        with pytest.raises(control.Cancelled):
            with control.job('cache-test','Original cache wait','cache-test'):
                jobs.append(control.current())
                with cache_use.writer([target],wait_seconds=3):entered.append(True)
    with file_use.pin([target],exclusive=True),ThreadPoolExecutor(max_workers=1) as pool:
        future=pool.submit(consume)
        try:
            assert busy.wait(5);jobs[0].stop.set()
        finally:
            if jobs:jobs[0].stop.set()
        future.result(timeout=5)
    assert not entered
