"""Motion cache bytes/claims; CLI is a local synthetic-media boundary fixture."""
from concurrent.futures import ThreadPoolExecutor
from contextlib import contextmanager
from types import SimpleNamespace
from pathlib import Path
import threading
import shutil
import json
from uuid import uuid4

import pytest
from backend.app import file_use,cleanup
from backend.app.config import settings
from backend.app.studio import motion,media,runner
from backend.tests.test_studio import three_scene_video,_render_project

VALUES={'channel':'Original synthetic cache fixture','duration':1.0}


@pytest.fixture
def piece(tmp_path,monkeypatch):
    assert media._font_path(True) and media._font_path(False),'Installed application fonts required'
    video=tmp_path/'original-procedural.mp4'
    media.run(['ffmpeg','-v','error','-y','-f','lavfi','-i','testsrc2=s=320x180:r=24:d=1',
        '-c:v','libx264','-pix_fmt','yuv420p',str(video)])
    cache=settings.data_path/'studio'/'_motioncache'/('case-'+uuid4().hex);cache.mkdir(parents=True)
    gsap=tmp_path/'fixture.js';gsap.write_text('// CLI boundary fixture; not a real HyperFrames render')
    monkeypatch.setattr(motion,'available',lambda:(True,''))
    monkeypatch.setattr(motion,'binary',lambda:'synthetic-motion-cli')
    monkeypatch.setattr(motion,'_gsap',lambda:gsap)
    monkeypatch.setattr(motion,'cache_dir',lambda:cache)
    return cache,video.read_bytes()


@pytest.mark.parametrize('through_runner',[False,True])
def test_same_key_motion_workers_do_not_delete_each_others_work(piece,tmp_path,monkeypatch,through_runner):
    cache,original=piece;entered=threading.Event();contested=threading.Event();release=threading.Event();calls=[]
    original_pin=file_use.pin
    @contextmanager
    def observe(*args,**kwargs):
        try:
            with original_pin(*args,**kwargs) as handle:yield handle
        except file_use.FileBusy:contested.set();raise
    monkeypatch.setattr(file_use,'pin',observe)
    def render(command,**kwargs):
        calls.append(command);target=Path(command[command.index('-o')+1]);target.write_bytes(original)
        if len(calls)>1:contested.set()
        entered.set();assert release.wait(5)
        assert target.read_bytes()==original,'Another cache worker removed or replaced the active render'
        return SimpleNamespace(stdout='',stderr='')
    monkeypatch.setattr(media,'run',render)
    if through_runner:
        from backend.app.db import SessionLocal
        from backend.app.models import StudioProject
        with SessionLocal() as s:
            rows=[StudioProject(tmdb_id=997880+i,title=f'Original motion consumer {i}') for i in range(2)]
            s.add_all(rows);s.commit();ids=[row.id for row in rows]
        outputs={}
        def stage(pid):outputs[pid]=motion.render_piece('endscreen',VALUES);return 'synthetic motion boundary completed'
        monkeypatch.setitem(runner.STAGES,'render',stage)
        def invoke(i):runner.run_one(ids[i],'render');return outputs[ids[i]]
    else:
        def invoke(i):return motion.render_piece('endscreen',VALUES)
    with ThreadPoolExecutor(max_workers=2) as pool:
        first=pool.submit(invoke,0)
        try:
            assert entered.wait(5);second=pool.submit(invoke,1);assert contested.wait(5)
        finally:release.set()
        outputs=[first.result(timeout=5),second.result(timeout=5)]
    assert len(calls)==1 and all(path.read_bytes()==original for path in outputs)
    assert not list(cache.glob('_work-*'))


def test_materialized_piece_survives_cache_eviction_during_copy_and_after_return(piece,tmp_path,monkeypatch):
    cache,original=piece
    def render(command,**kwargs):
        Path(command[command.index('-o')+1]).write_bytes(original)
        return SimpleNamespace(stdout='',stderr='')
    monkeypatch.setattr(media,'run',render)
    motion.render_piece('endscreen',VALUES)
    real_copy=shutil.copyfile
    def copy_with_cleanup(source,dest,*args,**kwargs):
        if Path(source).suffix=='.mp4':
            with ThreadPoolExecutor(1) as pool:pruned=pool.submit(cleanup.prune_lru_cache,cache,0).result(timeout=5)
            assert pruned['files_removed']==0,'Motion input was evicted before its owned copy completed'
        return real_copy(source,dest,*args,**kwargs)
    monkeypatch.setattr(shutil,'copyfile',copy_with_cleanup)
    owned=tmp_path/'attempt'/'motion-input.mp4'
    assert motion.render_piece('endscreen',VALUES,destination=owned)==owned
    cleanup.prune_lru_cache(cache,0)
    assert owned.read_bytes()==original


def test_revoked_motion_owner_cannot_promote_completed_cli_bytes(piece,monkeypatch):
    from sqlalchemy import update
    from backend.app.db import engine
    from backend.app.models import WorkerLease
    from backend.app.worker_leases import LeaseLost
    cache,original=piece
    def render(command,**kwargs):
        Path(command[command.index('-o')+1]).write_bytes(original)
        handle=file_use._active.get()[-1]
        with engine.begin() as db:db.execute(update(WorkerLease).where(WorkerLease.resource==handle.resource).values(generation=WorkerLease.generation+1))
        return SimpleNamespace(stdout='',stderr='')
    monkeypatch.setattr(media,'run',render)
    with pytest.raises(LeaseLost),file_use.pin([cache]):motion.render_piece('endscreen',VALUES)
    assert not list(cache.glob('*.mp4'))


def test_copy_failure_preserves_previous_owned_piece_and_releases_partial_space(piece,tmp_path,monkeypatch):
    cache,original=piece
    def render(command,**kwargs):
        Path(command[command.index('-o')+1]).write_bytes(original);return SimpleNamespace(stdout='',stderr='')
    monkeypatch.setattr(media,'run',render)
    cached=motion.render_piece('endscreen',VALUES)
    owned=tmp_path/'owned.mp4';owned.write_bytes(original)
    real_copy=shutil.copyfile
    def fail_copy(source,dest,*args,**kwargs):
        if Path(source)==cached:
            Path(dest).write_bytes(original[:20]);raise OSError('Synthetic disk-full during motion copy')
        return real_copy(source,dest,*args,**kwargs)
    monkeypatch.setattr(shutil,'copyfile',fail_copy);failures=[]
    assert motion.try_piece('endscreen',VALUES,None,failures,destination=owned) is None
    assert 'Synthetic disk-full' in failures[0]
    assert owned.read_bytes()==original and cached.read_bytes()==original
    assert not list(tmp_path.glob('.*.tmp'))


def disposable(cache,*,schema=1):
    key='endscreen-'+'a'*20;work=cache/f'_work-{key}-{uuid4().hex}';work.mkdir()
    (work/'.scrapper-motion-work.json').write_text(json.dumps({'schema':schema,'kind':'motion_cache_work','cache_key':key}))
    (work/'partial.mp4').write_bytes(b'original disposable partial bytes')
    return work


def test_work_cleanup_waits_for_live_owner_then_reclaims_only_its_disposable_namespace(piece):
    cache,original=piece;work=disposable(cache);keep=cache/'retained-cache.mp4';keep.write_bytes(original)
    with file_use.pin([work],exclusive=True),ThreadPoolExecutor(1) as pool:
        result=pool.submit(cleanup.prune_motion_workdirs,cache).result(timeout=5)
        assert result['work_dirs_removed']==0 and work.is_dir()
    result=cleanup.prune_motion_workdirs(cache)
    assert result['work_dirs_removed']==1 and result['bytes_freed']>20
    assert not work.exists() and keep.read_bytes()==original


@pytest.mark.parametrize('schema',[True,2])
def test_work_cleanup_preserves_future_or_malformed_markers(piece,schema):
    cache,_=piece;work=disposable(cache,schema=schema)
    assert cleanup.prune_motion_workdirs(cache)['work_dirs_removed']==0 and work.is_dir()


def test_work_cleanup_preserves_unmarked_and_symlinked_content(piece,tmp_path):
    cache,_=piece;unmarked=disposable(cache);(unmarked/'.scrapper-motion-work.json').unlink()
    outside=tmp_path/'original-private-content';outside.mkdir();(outside/'keep').write_text('Original private fixture')
    (cache/f'_work-endscreen-{"a"*20}-{uuid4().hex}').symlink_to(outside,target_is_directory=True)
    assert cleanup.prune_motion_workdirs(cache)['work_dirs_removed']==0
    assert unmarked.is_dir() and (outside/'keep').read_text()=='Original private fixture'


def test_nested_space_reclamation_does_not_delete_its_own_live_work(piece):
    cache,_=piece;work=disposable(cache)
    with file_use.pin([work],exclusive=True):
        assert cleanup.prune_motion_workdirs(cache)['work_dirs_removed']==0
        assert (work/'partial.mp4').is_file()


def test_expired_work_is_reclaimed_and_old_invocation_is_fenced(piece):
    from datetime import timedelta
    from backend.app.db import SessionLocal
    from backend.app.models import WorkerLease
    from backend.app.worker_leases import clock,LeaseLost
    cache,_=piece;work=disposable(cache)
    with file_use.pin([work],exclusive=True) as owner:
        with SessionLocal() as s:
            s.get(WorkerLease,owner.resource).expires_at=clock(s)-timedelta(seconds=1);s.commit()
        with ThreadPoolExecutor(1) as pool:result=pool.submit(cleanup.prune_motion_workdirs,cache).result(timeout=5)
        assert result['work_dirs_removed']==1 and not work.exists()
        with pytest.raises(LeaseLost):file_use.assert_current()


def test_actual_compositor_uses_owned_motion_inputs_after_global_cache_is_evicted(piece,three_scene_video,monkeypatch):
    from backend.app.studio import stage_render
    from backend.app.db import SessionLocal
    from backend.app.models import StudioProject
    cache,_=piece;pid,root=_render_project(three_scene_video)
    original_run=media.run;calls=[]
    def render(command,**kwargs):
        if command[0]!='synthetic-motion-cli':return original_run(command,**kwargs)
        target=Path(command[command.index('-o')+1]);calls.append(str(target))
        if target.suffix=='.mov':
            original_run(['ffmpeg','-v','error','-y','-f','lavfi','-i','color=white@0.5:s=1920x1080:d=1:r=30,format=yuva444p10le',
                '-c:v','prores_ks','-profile:v','4444',str(target)])
        else:
            original_run(['ffmpeg','-v','error','-y','-f','lavfi','-i','color=red:s=1920x1080:d=1:r=30','-pix_fmt','yuv420p',str(target)])
        return SimpleNamespace(stdout='',stderr='')
    monkeypatch.setattr(media,'run',render)
    monkeypatch.setattr(stage_render,'motion_mode',lambda:'on');monkeypatch.setattr(stage_render,'END_CARD',1.0)
    original_pieces=stage_render._motion_pieces;observed=[]
    def assemble(*args,**kwargs):
        info,overlays,end=original_pieces(*args,**kwargs)
        with ThreadPoolExecutor(1) as pool:pruned=pool.submit(cleanup.prune_lru_cache,cache,0).result(timeout=5)
        paths=[path for path,_ in overlays]+([end] if end else [])
        assert pruned['files_removed']>0 and paths
        assert all(path.resolve().is_relative_to(root.resolve()) and path.is_file() for path in paths)
        observed.extend(paths);return info,overlays,end
    monkeypatch.setattr(stage_render,'_motion_pieces',assemble)
    runner.run_one(pid,'render')
    with SessionLocal() as s:rendered=s.get(StudioProject,pid).render
    assert calls and observed and not rendered['quality']['errors']
    assert rendered['motion']['pieces']>0 and not rendered['motion']['failures']
    assert media.duration(root/rendered['file'])>3.8
    assert not (root/rendered['file']).parent.joinpath('motion_inputs').exists()
