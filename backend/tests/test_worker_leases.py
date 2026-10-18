import concurrent.futures
from datetime import datetime, timedelta, timezone

import pytest
from sqlalchemy import update

from backend.app import resume, worker_leases as leases, budget_reservations as br
from backend.app.db import SessionLocal
from backend.app.models import ResumableJob, StudioProject, WorkerLease
from backend.app.studio import runner


def acquire(key, owner):
    with SessionLocal() as s:
        h=leases.acquire(s,key,owner); s.commit(); return h


def expire(key):
    with SessionLocal() as s:
        s.get(WorkerLease,key).expires_at=datetime.now(timezone.utc)-timedelta(seconds=1); s.commit()


def test_only_one_concurrent_worker_claims_expired_resource():
    key='studio:991'
    old=acquire(key,'old'); expire(key)
    def claim(i):
        try: return acquire(key,str(i))
        except leases.LeaseLost: return None
    with concurrent.futures.ThreadPoolExecutor(max_workers=8) as pool: winners=list(pool.map(claim,range(8)))
    live=[x for x in winners if x]
    assert len(live)==1 and live[0].generation==old.generation+1


def test_expired_owner_cannot_renew_write_bulk_write_checkpoint_finish_or_spend():
    rid=resume.begin('studio','Synthetic',{'project_id':992})
    old=resume._claims[rid]
    with SessionLocal() as s:
        p=StudioProject(tmdb_id=992,title='Before'); s.add(p); s.commit(); pid=p.id
    expire(old.resource)
    new=acquire(old.resource,'new')
    with SessionLocal() as s:
        row=s.get(ResumableJob,rid); row.owner='new'; row.checkpoint={'stage':'new'}; s.commit()
        assert not leases.renew(s,old)
    with leases.bind(old):
        with pytest.raises(leases.LeaseLost):
            with SessionLocal() as s:
                s.get(StudioProject,pid).title='Stale'; s.commit()
        with pytest.raises(leases.LeaseLost):
            with SessionLocal() as s: s.execute(update(StudioProject).where(StudioProject.id==pid).values(title='Stale bulk'))
        with pytest.raises(leases.LeaseLost): resume.checkpoint(rid,stage='stale')
        with pytest.raises(leases.LeaseLost): br.reserve('fixture',.01)
        resume.finish(rid,'done')  # stale completion is ignored
    with SessionLocal() as s:
        assert s.get(StudioProject,pid).title=='Before'
        assert s.get(ResumableJob,rid).status=='running' and s.get(ResumableJob,rid).checkpoint=={'stage':'new'}
    leases.guard(handle=new)


def test_owner_stop_revokes_lease_and_attempt_paths_never_overlap():
    rid=resume.begin('studio','Synthetic',{'project_id':993})
    old=resume._claims[rid]
    with leases.bind(old): old_path=runner.attempt_dir(993)
    runner.stop(993)
    with leases.bind(old):
        with pytest.raises(leases.LeaseLost): runner.attempt_dir(993)
    new=acquire(old.resource,'next')
    with leases.bind(new): new_path=runner.attempt_dir(993)
    assert new_path!=old_path
    (old_path/'result.txt').write_text('old worker')
    (new_path/'result.txt').write_text('new worker')
    assert (new_path/'result.txt').read_text()=='new worker'


def test_same_process_cannot_launch_an_owned_job_twice():
    rid=resume.begin('studio','Synthetic',{'project_id':994})
    fn=resume.bind_work(rid,lambda:resume.finish(rid,'done'))
    with pytest.raises(leases.LeaseLost): resume.bind_work(rid,lambda:None)
    with pytest.raises(leases.LeaseLost): resume.reopen(rid)
    fn()
    assert rid not in resume._active_rids
