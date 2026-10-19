"""Recovery contracts use disposable databases; never a configured owner database."""
import gzip
import hashlib
import json
import os
import sqlite3
import sys
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from unittest.mock import patch

import pytest
from sqlalchemy import create_engine

from backend.app import backup, db, sqlite_snapshots as snap
from backend.app.config import settings
from backend.app.file_use import pin
from scripts import restore_db


@pytest.fixture
def source(tmp_path, monkeypatch):
    path=tmp_path/'fixture.db'
    with sqlite3.connect(path) as conn:
        conn.execute('CREATE TABLE before_upgrade (id INTEGER PRIMARY KEY, label TEXT)')
        conn.execute("INSERT INTO before_upgrade VALUES (1,'baseline')")
    monkeypatch.setattr(settings,'database_url','sqlite:///'+str(path))
    monkeypatch.setattr(settings,'data_dir',str(tmp_path))
    monkeypatch.setattr(settings,'backup_drive_folder_id','')
    return path


def rows(path):
    with sqlite3.connect(path) as conn:return conn.execute('SELECT * FROM before_upgrade').fetchall()


def unpack(file,target):
    target.write_bytes(gzip.decompress(Path(file).read_bytes()))
    snap.integrity(target)
    return target


def restore(source,target,*flags):
    with patch.object(sys,'argv',['restore_db.py','--from',str(source),'--to',str(target),*flags]):
        return restore_db.main()


def test_parallel_same_second_snapshots_are_unique_and_verified(source,tmp_path,monkeypatch):
    monkeypatch.setattr(snap.time,'strftime',lambda *a:'20261018_000000')
    with ThreadPoolExecutor(max_workers=6) as pool:
        results=list(pool.map(lambda _:backup.create_backup('parallel',False,rotate=False),range(6)))
    assert len({r['filename'] for r in results})==6
    for i,r in enumerate(results):
        raw=unpack(r['path'],tmp_path/f'check{i}.db')
        assert rows(raw)==[(1,'baseline')]
        assert hashlib.sha256(raw.read_bytes()).hexdigest()==r['sha256']
        assert hashlib.sha256(Path(r['path']).read_bytes()).hexdigest()==r['compressed_sha256']
    assert not list((tmp_path/'backups').glob('.snapshot-*'))


def test_failed_compression_has_no_complete_filename_or_status(source,tmp_path,monkeypatch):
    def fail(*a,**kw):raise OSError('synthetic disk failure')
    monkeypatch.setattr(snap.gzip,'GzipFile',fail)
    with pytest.raises(OSError):backup.create_backup('failure',False,rotate=False)
    assert not list((tmp_path/'backups').glob('*.db.gz'))
    assert not list((tmp_path/'backups').glob('.snapshot-*'))
    assert not (tmp_path/'backups/latest_status.json').exists()


def test_pre_migration_snapshot_precedes_create_all(source,tmp_path,monkeypatch):
    engine=create_engine('sqlite:///'+str(source))
    monkeypatch.setattr(db,'engine',engine)
    monkeypatch.setattr(db,'_migrate',lambda:None)
    db.init_db()
    snapshot=next((tmp_path/'backups').glob('*pre_migration.db.gz'))
    with sqlite3.connect(unpack(snapshot,tmp_path/'before.db')) as conn:
        assert conn.execute("SELECT name FROM sqlite_master WHERE type='table'").fetchall()==[('before_upgrade',)]
    with sqlite3.connect(source) as conn:
        assert conn.execute("SELECT count(*) FROM sqlite_master WHERE type='table'").fetchone()[0]>1
    engine.dispose()


def test_pre_migration_failure_blocks_all_schema_mutation(source,monkeypatch):
    engine=create_engine('sqlite:///'+str(source));monkeypatch.setattr(db,'engine',engine)
    def fail(*a,**kw):raise OSError('synthetic disk failure')
    monkeypatch.setattr(snap,'compressed_snapshot',fail)
    with pytest.raises(backup.BackupError,match='schema was not changed'):db.init_db()
    with sqlite3.connect(source) as conn:
        assert conn.execute("SELECT name FROM sqlite_master WHERE type='table'").fetchall()==[('before_upgrade',)]
    engine.dispose()


def test_wal_emergency_snapshot_keeps_latest_commits(source,tmp_path):
    initial=backup.create_backup('initial',False,rotate=False)
    writer=sqlite3.connect(source)
    writer.execute('PRAGMA journal_mode=WAL');writer.execute('PRAGMA wal_autocheckpoint=0')
    writer.execute("INSERT INTO before_upgrade VALUES (2,'committed in WAL')");writer.commit()
    assert Path(str(source)+'-wal').stat().st_size>0
    try:
        assert restore(initial['path'],source,'--confirm','--offline-confirmed')==0
        emergency=next((tmp_path/'backups').glob('scrapper_pre_restore_*.db.gz'))
        assert rows(unpack(emergency,tmp_path/'emergency.db'))==[(1,'baseline'),(2,'committed in WAL')]
        assert rows(source)==[(1,'baseline')]
    finally:writer.close()


def test_restore_refuses_missing_offline_confirmation_and_active_runtime(source,tmp_path):
    initial=backup.create_backup('initial',False,rotate=False)
    with sqlite3.connect(source) as conn:conn.execute("INSERT INTO before_upgrade VALUES (2,'keep me')")
    assert restore(initial['path'],source,'--confirm')==1
    handle=snap.lock_handle(source)
    try:assert restore(initial['path'],source,'--confirm','--offline-confirmed')==1
    finally:os.close(handle)
    assert rows(source)==[(1,'baseline'),(2,'keep me')]
    assert not list((tmp_path/'backups').glob('scrapper_pre_restore_*'))


def test_reader_protects_backup_from_retention_and_sole_copy_survives(source,tmp_path):
    old=backup.create_backup('old',False,rotate=False)
    new=backup.create_backup('new',False,rotate=False)
    oldpath=Path(old['path']);newpath=Path(new['path'])
    os.utime(oldpath,(1,1));os.utime(newpath,(2,2))
    with pin([oldpath]):
        with ThreadPoolExecutor(max_workers=1) as pool:
            assert pool.submit(backup.prune_local_backups,1).result()==[]
        assert oldpath.exists()
    assert backup.prune_local_backups(1)==[oldpath.name]
    assert backup.prune_local_backups(0)==[]
    assert newpath.exists()


def test_persisted_newest_status_wins_over_late_worker_cache(source,tmp_path):
    newer={'created_at':'2026-10-18T15:00:00+00:00','filename':'newer'}
    older={'created_at':'2026-10-18T14:00:00+00:00','filename':'older'}
    backup._write_persisted_status(newer)
    backup._write_persisted_status(older)
    backup._last_backup_cache=older
    assert backup.get_backup_status()['last_backup']==newer
    assert json.loads((tmp_path/'backups/latest_status.json').read_text())==newer
    assert not list((tmp_path/'backups').glob('.status-*'))


def test_quoted_table_names_in_restore_counts():
    with sqlite3.connect(':memory:') as conn:
        conn.execute('CREATE TABLE "name""quote" (id INTEGER)')
        conn.execute('INSERT INTO "name""quote" VALUES (1)')
        assert restore_db.get_table_counts(conn)=={'name"quote':1}


def test_actual_baseline_schema_upgrade_preserves_legacy_content_and_is_repeatable(source,tmp_path,monkeypatch):
    """Build the schema from the pinned repository baseline, not an invented subset."""
    import subprocess
    import types
    from sqlalchemy.orm import DeclarativeBase,Session
    baseline='95b6bd45c23644cff2ec4549db4312c2f7d1281e'
    model_source=subprocess.check_output(['git','show',baseline+':backend/app/models.py'],cwd=Path(__file__).resolve().parents[2],text=True)
    class LegacyBase(DeclarativeBase):pass
    namespace=types.ModuleType('scrapper_upgrade_fixture.models')
    package=types.ModuleType('scrapper_upgrade_fixture');package.__path__=[]
    legacy_db=types.ModuleType('scrapper_upgrade_fixture.db');legacy_db.Base=LegacyBase
    monkeypatch.setitem(sys.modules,'scrapper_upgrade_fixture',package)
    monkeypatch.setitem(sys.modules,'scrapper_upgrade_fixture.db',legacy_db)
    monkeypatch.setitem(sys.modules,'scrapper_upgrade_fixture.models',namespace)
    exec(compile(model_source,baseline+':backend/app/models.py','exec'),namespace.__dict__)
    engine=create_engine('sqlite:///'+str(source));LegacyBase.metadata.create_all(engine)
    with Session(engine) as s:
        row=namespace.StudioProject(tmdb_id=123,title='Original upgrade fixture',facts={'title':'Original upgrade fixture'},
            script={'sentences':[{'text':'Keep the original narration exactly.'}]},review={'legacy':'unchanged'})
        s.add(row);s.commit();pid=row.id
        created=row.created_at
    monkeypatch.setattr(db,'engine',engine)
    db.init_db();db.init_db()
    from backend.app.models import StudioProject
    with Session(engine) as s:
        row=s.get(StudioProject,pid)
        assert row.title=='Original upgrade fixture' and row.created_at==created
        assert row.script=={'sentences':[{'text':'Keep the original narration exactly.'}]}
        assert row.facts=={'title':'Original upgrade fixture'} and row.review=={'legacy':'unchanged'}
    snapshots=list((tmp_path/'backups').glob('*pre_migration.db.gz'))
    assert len(snapshots)==2
    old_tables=[]
    for i,file in enumerate(snapshots):
        with sqlite3.connect(unpack(file,tmp_path/f'upgrade{i}.db')) as conn:
            old_tables.append({r[0] for r in conn.execute("SELECT name FROM sqlite_master WHERE type='table'")})
    assert sum('research_source_records' in tables for tables in old_tables)==1
    engine.dispose()


def test_offline_identity_upgrade_preserves_rows_indexes_triggers_and_avoids_id_reuse(source,tmp_path):
    from scripts.upgrade_sqlite_identity import upgrade
    with sqlite3.connect(source) as conn:
        conn.executescript('''CREATE TABLE studio_projects (id INTEGER NOT NULL, title TEXT, PRIMARY KEY (id));
            CREATE INDEX ix_legacy_title ON studio_projects(title);
            CREATE TABLE child (id INTEGER PRIMARY KEY, project INTEGER REFERENCES studio_projects(id));
            CREATE TABLE audit (title TEXT);
            CREATE TRIGGER legacy_title AFTER UPDATE ON studio_projects BEGIN INSERT INTO audit VALUES (NEW.title); END;
            INSERT INTO studio_projects VALUES (1,'keep original'); INSERT INTO child VALUES (1,1);''')
    (tmp_path/'studio/88').mkdir(parents=True)
    dry=upgrade(source)
    assert dry['dry_run'] and dry['tables']==['studio_projects'] and dry['backup'] is None
    result=upgrade(source,confirm=True,offline_confirmed=True)
    assert result['rows']=={'studio_projects':{'copied':1,'sequence_floor':88}}
    assert Path(result['backup']['path']).is_file()
    with sqlite3.connect(source) as conn:
        assert conn.execute('SELECT * FROM studio_projects').fetchall()==[(1,'keep original')]
        conn.execute("UPDATE studio_projects SET title='updated' WHERE id=1")
        assert conn.execute('SELECT * FROM audit').fetchall()==[('updated',)]
        assert conn.execute("SELECT name FROM sqlite_master WHERE type='index' AND name='ix_legacy_title'").fetchone()
        conn.execute("INSERT INTO studio_projects(title) VALUES ('temporary')")
        first=conn.execute('SELECT max(id) FROM studio_projects').fetchone()[0]
        conn.execute('DELETE FROM studio_projects WHERE id=?',(first,))
        conn.execute("INSERT INTO studio_projects(title) VALUES ('new')")
        assert conn.execute('SELECT max(id) FROM studio_projects').fetchone()[0]>first>88
        assert conn.execute('PRAGMA foreign_key_check').fetchall()==[]
    assert upgrade(source,confirm=True,offline_confirmed=True)['tables']==[]


def test_identity_upgrade_rolls_back_both_tables_on_unexpected_second_schema(source):
    from scripts.upgrade_sqlite_identity import upgrade
    with sqlite3.connect(source) as conn:
        conn.executescript('CREATE TABLE studio_projects(id INTEGER NOT NULL, title TEXT, PRIMARY KEY (id)); CREATE TABLE countdown_projects(id TEXT PRIMARY KEY);')
    with pytest.raises(ValueError,match='Unexpected primary-key'):
        upgrade(source,confirm=True,offline_confirmed=True)
    with sqlite3.connect(source) as conn:
        assert 'AUTOINCREMENT' not in conn.execute("SELECT sql FROM sqlite_master WHERE name='studio_projects'").fetchone()[0]
        assert not conn.execute("SELECT name FROM sqlite_master WHERE name LIKE 'identity_upgrade_%'").fetchall()


def test_empty_legacy_table_with_orphan_directory_gets_monotonic_floor(source,tmp_path):
    from scripts.upgrade_sqlite_identity import upgrade
    with sqlite3.connect(source) as conn:conn.execute('CREATE TABLE studio_projects(id INTEGER NOT NULL, title TEXT, PRIMARY KEY(id))')
    (tmp_path/'studio/99').mkdir(parents=True)
    upgrade(source,confirm=True,offline_confirmed=True)
    with sqlite3.connect(source) as conn:
        conn.execute("INSERT INTO studio_projects(title) VALUES ('fresh')")
        assert conn.execute('SELECT id FROM studio_projects').fetchone()[0]==100


@pytest.mark.parametrize('correct',[False,True])
def test_backup_drive_success_requires_remote_checksum_and_size(source,tmp_path,monkeypatch,session,correct):
    from backend.app import drive_sync
    from backend.app.models import RemoteCopyEvidence
    payload=tmp_path/'synthetic.db.gz';payload.write_bytes(b'synthetic immutable compressed fixture')
    identity='synthetic-backup-'+str(correct)
    class Service:
        def files(self):return self
        def create(self,**kw):return self
        def next_chunk(self):return None,{'id':identity,'webViewLink':'https://example.invalid/backup'}
        def get(self,**kw):return self
        def execute(self):return {'size':str(payload.stat().st_size),'md5Checksum':hashlib.md5(payload.read_bytes()).hexdigest() if correct else 'wrong'}
    monkeypatch.setattr(drive_sync,'get_drive_service',Service)
    result=backup._upload_to_google_drive(payload,'synthetic-folder')
    assert result['uploaded'] is correct
    assert payload.exists()
    assert session.query(RemoteCopyEvidence).filter_by(file_id=identity,action='remote_creation_receipt').count()==1
    assert session.query(RemoteCopyEvidence).filter_by(file_id=identity,action='verified_transfer_receipt').count()==int(correct)
    if correct:assert result['verification']['sha256']==hashlib.sha256(payload.read_bytes()).hexdigest()
    else:assert result['status']=='error'
