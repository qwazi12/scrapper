"""Actual OS-worker cache contention/crash recovery; cloud speech is simulated."""
import json
import subprocess
import sys
import time
from pathlib import Path
from datetime import timedelta

from backend.tests.test_tts_cache_ownership import voice


WORKER=r'''
import json,os,sys,time
from pathlib import Path
from contextlib import contextmanager
from backend.app.e2e_safety import install_network_guard
install_network_guard()
from backend.app import costs,file_use,control
from backend.app.studio import tts
cache,original,output,gates=map(Path,sys.argv[1:])
tts.cache_dir=lambda:cache
tts.engine=lambda:'chirp'
costs.check_budget=lambda:None  # file protocol only; not provider/billing proof
original_pin=file_use.pin
@contextmanager
def observed(*args,**kwargs):
 try:
  with original_pin(*args,**kwargs) as handle:yield handle
 except file_use.FileBusy:
  (gates/'contended').touch();raise
file_use.pin=observed
def provide(text,dest):
 with (gates/'calls.jsonl').open('a') as ledger:
  ledger.write(json.dumps({'pid':os.getpid(),'real_cloud_tts':False})+'\n');ledger.flush();os.fsync(ledger.fileno())
 audio=original.read_bytes();dest.write_bytes(audio[:100]);(gates/'entered').touch()
 deadline=time.monotonic()+15
 while not (gates/'release').exists():
  control.check()
  if time.monotonic()>deadline:raise RuntimeError('Test coordinator did not release provider')
  time.sleep(.01)
 dest.write_bytes(audio)
tts._chirp=provide
tts.synth('Original process-boundary cache fixture.',output)
print(json.dumps({'pid':os.getpid(),'cache_key':tts._cache_key('Original process-boundary cache fixture.')}),flush=True)
'''


def wait_for(path):
    deadline=time.monotonic()+10
    while not path.exists():
        if time.monotonic()>deadline:raise AssertionError(f'Owned test worker did not reach {path.name}')
        time.sleep(.01)


def start(cache,original,output,gates):
    return subprocess.Popen([sys.executable,'-c',WORKER,str(cache),str(original),str(output),str(gates)],
        stdout=subprocess.PIPE,stderr=subprocess.PIPE,text=True)


def finish(processes,gates):
    (gates/'release').touch()
    for process in processes:
        if process.poll() is None:
            process.terminate()
            try:process.wait(timeout=3)
            except subprocess.TimeoutExpired:process.kill();process.wait(timeout=3)


def test_two_os_workers_share_one_complete_generation(voice,tmp_path):
    cache,audio=voice;gates=tmp_path/'gates';gates.mkdir()
    original=tmp_path/'original.mp3';original.write_bytes(audio)
    outputs=[tmp_path/'first.mp3',tmp_path/'second.mp3'];processes=[]
    try:
        processes.append(start(cache,original,outputs[0],gates));wait_for(gates/'entered')
        processes.append(start(cache,original,outputs[1],gates));wait_for(gates/'contended')
        (gates/'release').touch()
        results=[]
        for process in processes:
            stdout,stderr=process.communicate(timeout=10)
            assert process.returncode==0,stderr
            results.append(json.loads(stdout))
        assert len({row['pid'] for row in results})==2
        assert len({row['cache_key'] for row in results})==1
        assert len((gates/'calls.jsonl').read_text().splitlines())==1
        assert all(output.read_bytes()==audio for output in outputs)
        assert (cache/(results[0]['cache_key']+'.mp3')).read_bytes()==audio
    finally:finish(processes,gates)


def test_confirmed_dead_writer_partial_is_never_promoted_after_forced_expiry(voice,tmp_path):
    from backend.app.db import SessionLocal
    from backend.app.models import FileUse,WorkerLease
    from backend.app.worker_leases import clock
    from backend.app import cleanup
    cache,audio=voice;gates=tmp_path/'gates';gates.mkdir()
    original=tmp_path/'original.mp3';original.write_bytes(audio)
    abandoned=tmp_path/'abandoned.mp3';recovered=tmp_path/'recovered.mp3';processes=[]
    try:
        first=start(cache,original,abandoned,gates);processes.append(first);wait_for(gates/'entered')
        first.terminate();first.wait(timeout=5);first.communicate(timeout=2)
        assert first.returncode is not None and not abandoned.exists()
        partials=list(cache.glob('*.tmp.mp3'));assert len(partials)==1 and partials[0].read_bytes()==audio[:100]
        with SessionLocal() as s:
            claims=[row for row in s.query(FileUse).all() if any(Path(path).resolve().is_relative_to(cache.resolve()) for path in row.paths)]
            assert len(claims)==1
            # Inject expiry only after actual process death. This does not claim
            # that a real75s timeout or unknown provider bill was observed.
            s.get(WorkerLease,claims[0].resource).expires_at=clock(s)-timedelta(seconds=1);s.commit()
        (gates/'release').touch()
        second=start(cache,original,recovered,gates);processes.append(second)
        stdout,stderr=second.communicate(timeout=10);assert second.returncode==0,stderr
        result=json.loads(stdout);complete=cache/(result['cache_key']+'.mp3')
        assert recovered.read_bytes()==audio and complete.read_bytes()==audio and not abandoned.exists()
        assert len((gates/'calls.jsonl').read_text().splitlines())==2
        pruned=cleanup.prune_lru_cache(cache,len(audio),target_ratio=1.0)
        assert pruned['files_removed']==1 and not partials[0].exists() and complete.read_bytes()==audio
    finally:finish(processes,gates)
