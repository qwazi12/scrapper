"""Atomic, checksummed SQLite snapshots, including committed WAL transactions.

This module does not import the application database or create worker claims, so
it is usable before migrations and by the offline restore CLI.
"""
from __future__ import annotations
from contextlib import contextmanager
import gzip
import hashlib
import os
from pathlib import Path
import re
import shutil
import sqlite3
import tempfile
import time
import uuid


class SnapshotError(RuntimeError):
    pass


def fsync_directory(path):
    fd=os.open(path,os.O_RDONLY)
    try:os.fsync(fd)
    finally:os.close(fd)


def integrity(path):
    with sqlite3.connect(Path(path).resolve().as_uri()+'?mode=ro',uri=True) as conn:
        rows=conn.execute('PRAGMA integrity_check').fetchall()
    if rows!=[('ok',)]:raise SnapshotError(f'SQLite snapshot integrity check failed: {rows[:5]}')


def snapshot_raw(source,destination):
    source,destination=Path(source).resolve(),Path(destination)
    if not source.is_file():raise SnapshotError('Source database does not exist')
    if source==destination.resolve():raise SnapshotError('Snapshot source and destination must differ')
    with sqlite3.connect(source.as_uri()+'?mode=ro',uri=True,timeout=5) as original,sqlite3.connect(destination) as copy:
        pages=original.execute('PRAGMA page_count').fetchone()[0]
        size=original.execute('PRAGMA page_size').fetchone()[0]*pages
        if shutil.disk_usage(destination.parent).free<size+16*1024*1024:
            raise SnapshotError('Insufficient disk space for database snapshot')
        deadline=time.monotonic()+60
        def progress(*args):
            if time.monotonic()>deadline:raise SnapshotError('SQLite snapshot exceeded the bounded lock wait')
        original.backup(copy,pages=256,progress=progress,sleep=.05)
    integrity(destination)
    with destination.open('rb') as stream:os.fsync(stream.fileno())


def compressed_snapshot(source,directory,stem):
    """Create one immutable uniquely named gzip, visible only after verification."""
    if not re.fullmatch(r'[a-zA-Z0-9_-]{1,100}',stem):raise SnapshotError('Unsafe snapshot label')
    source,directory=Path(source).resolve(),Path(directory).resolve()
    directory.mkdir(parents=True,exist_ok=True)
    if shutil.disk_usage(directory).free<source.stat().st_size*2+16*1024*1024:
        raise SnapshotError('Insufficient disk space for a verified database snapshot')
    raw_fd,raw_name=tempfile.mkstemp(prefix='.snapshot-',suffix='.db',dir=directory);os.close(raw_fd)
    gz_fd,gz_name=tempfile.mkstemp(prefix='.snapshot-',suffix='.part',dir=directory)
    raw,temporary=Path(raw_name),Path(gz_name)
    final=directory/(stem+'.db.gz')
    try:
        snapshot_raw(source,raw)
        digest=hashlib.sha256()
        output=os.fdopen(gz_fd,'wb');gz_fd=None  # ownership moves to stream, never close a reused fd
        with raw.open('rb') as src,output:
            with gzip.GzipFile(fileobj=output,filename='',mode='wb',mtime=0,compresslevel=6) as compressed:
                while chunk:=src.read(1024*1024):digest.update(chunk);compressed.write(chunk)
            output.flush();os.fsync(output.fileno())
        # Verify the actual compressed bytes and CRC before publishing the name.
        check=hashlib.sha256()
        with gzip.open(temporary,'rb') as compressed:
            while chunk:=compressed.read(1024*1024):check.update(chunk)
        if check.hexdigest()!=digest.hexdigest():raise SnapshotError('Compressed snapshot checksum mismatch')
        os.link(temporary,final)  # no replacement, including same-second snapshots
        fsync_directory(directory)
        with final.open('rb') as file:compressed_sha=hashlib.file_digest(file,'sha256').hexdigest()
        return {'path':str(final),'filename':final.name,'uncompressed_bytes':raw.stat().st_size,
            'compressed_bytes':final.stat().st_size,'sha256':digest.hexdigest(),'compressed_sha256':compressed_sha}
    finally:
        if gz_fd is not None:os.close(gz_fd)
        raw.unlink(missing_ok=True);temporary.unlink(missing_ok=True)


def unique_stem(prefix,tag=''):
    if not re.fullmatch(r'[a-zA-Z0-9_-]{1,50}',tag or 'snapshot'):raise SnapshotError('Unsafe snapshot tag')
    stamp=time.strftime('%Y%m%d_%H%M%S',time.gmtime())
    return prefix+'_'+stamp+'_'+uuid.uuid4().hex+('_'+tag if tag else '')


def lock_handle(path,*,exclusive=False):
    """Cooperating app processes hold shared; offline restore requires exclusive."""
    try:import fcntl
    except ImportError:raise SnapshotError('Database maintenance locks require a supported POSIX runtime')
    target=Path(path).resolve();target.parent.mkdir(parents=True,exist_ok=True)
    lock_path=target.with_name('.'+target.name+'.maintenance.lock')
    flags=os.O_RDWR|os.O_CREAT|getattr(os,'O_NOFOLLOW',0)
    fd=os.open(lock_path,flags,0o600)
    try:fcntl.flock(fd,(fcntl.LOCK_EX if exclusive else fcntl.LOCK_SH)|fcntl.LOCK_NB)
    except BaseException:
        os.close(fd);raise SnapshotError('Database is in use; stop all Scrapper app/worker processes before offline maintenance')
    return fd


@contextmanager
def offline_lock(path):
    fd=lock_handle(path,exclusive=True)
    try:yield
    finally:os.close(fd)
