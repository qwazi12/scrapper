"""Real backup/restore drill for the marked disposable PostgreSQL harness only.

No live URL is accepted. Native tools connect exclusively to the harness's private
Unix socket and newly created fixture databases. Never use this as a live restore CLI.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import os
from pathlib import Path
import shutil
import stat
import subprocess
import tempfile
import time
from urllib.parse import parse_qs,urlsplit
from uuid import uuid4

import psycopg2
from psycopg2 import sql

REPO=Path(__file__).resolve().parents[1]
SOURCE='scrapper_fixture'


def digest(value):
    return hashlib.sha256(json.dumps(value,sort_keys=True,ensure_ascii=False,separators=(',',':')).encode()).hexdigest()


def private_fixture():
    root=Path(os.environ.get('SCRAPPER_PG_TEST_ROOT','/nonexistent')).resolve()
    url=urlsplit(os.environ.get('DATABASE_URL',''))
    query=parse_qs(url.query)
    if (root.parent!=Path('/tmp').resolve() or not root.name.startswith('scpg-') or not root.is_dir()
            or root.stat().st_uid!=os.getuid() or stat.S_IMODE(root.stat().st_mode)&0o077
            or not (root/'.scrapper-postgres-fixture').is_file() or (root/'socket').is_symlink()
            or (root/'socket').resolve().parent!=root or not (root/'socket').is_dir()
            or url.scheme!='postgresql+psycopg2' or url.hostname or url.password or url.username!=SOURCE
            or url.path!='/'+SOURCE or query!={'host':[str(root/'socket')]}):
        raise RuntimeError('Restore drills require the newly created private marked Unix-socket fixture; no other database is accepted')
    return root


def connect(root,database,*,readonly=True,user=SOURCE):
    if database!=SOURCE and not database.startswith('scrapper_restore_'):raise RuntimeError('Unexpected fixture database')
    if database==SOURCE and not readonly:raise RuntimeError('Source fixture connections must remain read-only')
    if user!=SOURCE and not user.startswith('scrapper_restore_app_'):raise RuntimeError('Unexpected fixture role')
    conn=psycopg2.connect(host=str(root/'socket'),user=user,dbname=database,connect_timeout=5,
        options='-c statement_timeout=60000 -c lock_timeout=5000')
    conn.set_session(readonly=readonly,isolation_level='REPEATABLE READ')
    with conn.cursor() as cur:cur.execute("SET TIME ZONE 'UTC'")
    return conn


def fingerprint(root,database):
    """Semantic row hashes plus user schema/constraints/functions and sequences."""
    conn=connect(root,database)
    try:
        with conn.cursor() as cur:
            cur.execute("SELECT tablename FROM pg_tables WHERE schemaname='public' ORDER BY tablename")
            tables=[row[0] for row in cur.fetchall()]
            cur.execute("""SELECT c.relname,a.attnum,a.attname,format_type(a.atttypid,a.atttypmod),a.attnotnull,
                pg_get_expr(d.adbin,d.adrelid) FROM pg_class c JOIN pg_namespace n ON n.oid=c.relnamespace
                JOIN pg_attribute a ON a.attrelid=c.oid LEFT JOIN pg_attrdef d ON d.adrelid=c.oid AND d.adnum=a.attnum
                WHERE n.nspname='public' AND c.relkind='r' AND a.attnum>0 AND NOT a.attisdropped
                ORDER BY c.relname,a.attnum""")
            columns=cur.fetchall()
            cur.execute("SELECT tablename,indexname,indexdef FROM pg_indexes WHERE schemaname='public' ORDER BY tablename,indexname")
            indexes=cur.fetchall()
            cur.execute("""SELECT c.relname,t.tgname,pg_get_triggerdef(t.oid) FROM pg_trigger t
                JOIN pg_class c ON c.oid=t.tgrelid JOIN pg_namespace n ON n.oid=c.relnamespace
                WHERE n.nspname='public' AND NOT t.tgisinternal ORDER BY c.relname,t.tgname""")
            triggers=cur.fetchall()
            cur.execute("""SELECT p.proname,pg_get_functiondef(p.oid) FROM pg_proc p
                JOIN pg_namespace n ON n.oid=p.pronamespace WHERE n.nspname='public' ORDER BY p.proname,p.oid""")
            functions=cur.fetchall()
            cur.execute("""SELECT c.relname,k.conname,pg_get_constraintdef(k.oid) FROM pg_constraint k
                JOIN pg_class c ON c.oid=k.conrelid JOIN pg_namespace n ON n.oid=c.relnamespace
                WHERE n.nspname='public' ORDER BY c.relname,k.conname""")
            constraints=cur.fetchall()
            cur.execute("""SELECT t.typname,e.enumsortorder::text,e.enumlabel FROM pg_enum e
                JOIN pg_type t ON t.oid=e.enumtypid JOIN pg_namespace n ON n.oid=t.typnamespace
                WHERE n.nspname='public' ORDER BY t.typname,e.enumsortorder""")
            enums=cur.fetchall()
            cur.execute("SELECT sequencename,data_type::text,start_value,min_value,max_value,increment_by,cycle,cache_size FROM pg_sequences WHERE schemaname='public' ORDER BY sequencename")
            sequences=[]
            for metadata in cur.fetchall():
                name=metadata[0]
                cur.execute(sql.SQL('SELECT last_value,is_called FROM {}').format(sql.Identifier('public',name)))
                sequences.append([*metadata,*cur.fetchone()])
        rows={}
        for table in tables:
            with conn.cursor(name='verify_'+uuid4().hex) as cur:
                cur.execute(sql.SQL('SELECT to_jsonb(t) FROM {} AS t').format(sql.Identifier('public',table)))
                hashes=[digest(record[0]) for record in cur]
            rows[table]={'rows':len(hashes),'sha256':digest(sorted(hashes))}
        schema={'columns':columns,'indexes':indexes,'triggers':triggers,'functions':functions,'constraints':constraints,'enums':enums}
        return {'tables':rows,'schema_sha256':digest(schema),'sequences':sequences,'table_count':len(rows),
            'append_only_triggers':[list(trigger[:2]) for trigger in triggers if '_no_update' in trigger[1] or '_no_delete' in trigger[1]]}
    finally:conn.rollback();conn.close()


def check_append_only(root,database,user):
    conn=connect(root,database,readonly=False,user=user)
    try:
        with conn.cursor() as cur:
            identifier=digest(['restore-guard',uuid4().hex])
            cur.execute("""INSERT INTO public.learning_decisions(id,diagnostic_key,version,action,data,created_at)
                VALUES(%s,%s,%s,%s,%s,clock_timestamp())""",(identifier,identifier,identifier,'recovery_sentinel','{}'))
            try:
                cur.execute('DELETE FROM public.learning_decisions WHERE id=%s',(identifier,))
            except psycopg2.Error as exc:
                if exc.pgcode!='23514':raise
                conn.rollback();return {'delete_rejected':True,'sqlstate':'23514'}
            raise RuntimeError('Restored append-only decision evidence accepted deletion')
    finally:conn.rollback();conn.close()


def check_content_hash(root,database,expected):
    """Prove equal row counts cannot conceal changed JSON contents."""
    conn=connect(root,database,readonly=False);key='restore-drill-'+uuid4().hex
    try:
        with conn.cursor() as cur:
            cur.execute("INSERT INTO public.app_settings(key,value,updated_at) VALUES(%s,%s,clock_timestamp())",(key,'{"fixture":"original"}'))
        conn.commit();first=fingerprint(root,database)
        with conn.cursor() as cur:cur.execute('UPDATE public.app_settings SET value=%s WHERE key=%s',('{"fixture":"changed"}',key))
        conn.commit();second=fingerprint(root,database)
        if (first['tables']['app_settings']['rows']!=second['tables']['app_settings']['rows']
                or first['tables']['app_settings']['sha256']==second['tables']['app_settings']['sha256']):
            raise RuntimeError('Content hashes did not detect an equal-row-count JSON mutation')
        with conn.cursor() as cur:cur.execute('DELETE FROM public.app_settings WHERE key=%s',(key,))
        conn.commit()
        if fingerprint(root,database)!=expected:raise RuntimeError('Synthetic hash challenge did not return the restored database to its exact prior state')
        return True
    finally:conn.rollback();conn.close()


def run(bin_path,out,root):
    started=time.monotonic();run_id=uuid4().hex
    env={key:os.environ[key] for key in ('PATH','HOME','TMPDIR','LANG','SYSTEMROOT','DYLD_LIBRARY_PATH','LD_LIBRARY_PATH') if key in os.environ}
    fd,passfile=tempfile.mkstemp(prefix='.empty-pgpass-',dir=root);os.close(fd)
    env.update(PGCONNECT_TIMEOUT='5',PGAPPNAME='scrapper-isolated-recovery-drill',PGPASSFILE=passfile,
        PGOPTIONS='-c statement_timeout=60000 -c lock_timeout=5000',PGCLIENTENCODING='UTF8')
    common=['--host',str(root/'socket'),'--username',SOURCE,'--no-password']
    commands=[]
    def execute(tool,args,*,success=True,output=None,readonly=False):
        argv=[str(bin_path/tool),*args]
        with (out/(str(len(commands)+1)+'-'+tool+'.log')).open('wb') as log:
            dispatch_env={**env,'PGOPTIONS':env['PGOPTIONS']+' -c default_transaction_read_only=on'} if readonly else env
            result=subprocess.run(argv,env=dispatch_env,stdout=output or log,stderr=log,timeout=120)
        commands.append({'tool':tool,'returncode':result.returncode})
        if success and result.returncode:raise RuntimeError(f'{tool} failed; inspect the private fixture log')
        if not success and not result.returncode:raise RuntimeError(f'{tool} unexpectedly accepted the invalid fixture')
        return result
    dump=out/f'fixture-{run_id}.dump';part=out/f'.{run_id}.part'
    restore='scrapper_restore_'+run_id[:12];failure='scrapper_restore_failure_'+run_id[:12]
    restore_role='scrapper_restore_app_'+run_id[:12]
    restore_common=['--host',str(root/'socket'),'--username',restore_role,'--no-password']
    try:
        before=fingerprint(root,SOURCE)
        if not before['table_count']:raise RuntimeError('Source has no application schema')
        if shutil.disk_usage(out).free<128*1024*1024:raise RuntimeError('Insufficient space for this bounded synthetic recovery drill')
        with part.open('xb') as stream:
            os.chmod(part,0o600)
            execute('pg_dump',[*common,'--format=custom','--compress=6','--no-owner','--no-privileges',SOURCE],output=stream,readonly=True)
            stream.flush();os.fsync(stream.fileno())
        if part.read_bytes()[:5]!=b'PGDMP':raise RuntimeError('Expected PostgreSQL custom archive format')
        checksum=hashlib.sha256(part.read_bytes()).hexdigest()
        os.link(part,dump);part.unlink()
        folder=os.open(out,os.O_RDONLY)
        try:os.fsync(folder)
        finally:os.close(folder)
        with (out/'archive-list.txt').open('wb') as listing:execute('pg_restore',['--list',str(dump)],output=listing)
        # Bootstrap only the newly created cluster's roles via its own maintenance DB.
        execute('psql',[*common,'-X','--set=ON_ERROR_STOP=1','--dbname','postgres','--command',
            'CREATE ROLE '+restore_role+' LOGIN NOSUPERUSER NOCREATEDB NOCREATEROLE NOREPLICATION NOINHERIT'])
        for database in (restore,failure):execute('createdb',[*common,'--template=template0','--owner',restore_role,database])
        empty=fingerprint(root,failure)
        execute('pg_restore',[*restore_common,'--exit-on-error','--single-transaction','--no-owner','--no-privileges','--no-tablespaces','--dbname',restore,str(dump)])
        restored=fingerprint(root,restore)
        conn=connect(root,restore,user=restore_role)
        try:
            with conn.cursor() as cur:
                cur.execute('SELECT rolsuper,rolcreatedb,rolcreaterole,rolreplication FROM pg_roles WHERE rolname=current_user')
                if any(cur.fetchone()):raise RuntimeError('Restore role unexpectedly has elevated cluster privileges')
                cur.execute("SELECT COUNT(*) FROM pg_tables WHERE schemaname='public' AND tableowner<>current_user")
                if cur.fetchone()[0]:raise RuntimeError('Restored application tables were not owned by the unprivileged restore role')
        finally:conn.rollback();conn.close()
        if before!=restored:raise RuntimeError('Restored schema, row hashes, sequences or constraints differ from the source snapshot')
        content_mutation_detected=check_content_hash(root,restore,before)
        append_only=check_append_only(root,restore,restore_role)
        if before!=fingerprint(root,restore):raise RuntimeError('Append-only rejection changed restored evidence')
        corrupt=out/'truncated.dump';corrupt.write_bytes(dump.read_bytes()[:dump.stat().st_size//2]);os.chmod(corrupt,0o600)
        execute('pg_restore',[*restore_common,'--exit-on-error','--single-transaction','--no-owner','--no-privileges','--dbname',failure,str(corrupt)],success=False)
        if fingerprint(root,failure)!=empty:raise RuntimeError('Rejected truncated restore left partial database objects')
        plain=out/'late-error.sql'
        execute('pg_restore',['--no-owner','--no-privileges','--file',str(plain),str(dump)])
        os.chmod(plain,0o600)
        with plain.open('a') as file:file.write('\nSELECT 1/0; -- deliberate late fixture failure\n')
        execute('psql',[*restore_common,'-X','--single-transaction','--set=ON_ERROR_STOP=1','--dbname',failure,'--file',str(plain)],success=False)
        if fingerprint(root,failure)!=empty:raise RuntimeError('Late SQL failure did not roll back the entire logical restore')
        if fingerprint(root,SOURCE)!=before:raise RuntimeError('Source fixture changed during its read-only backup drill')
        result={'status':'passed','mode':'disposable_private_unix_socket_only','archive':dump.name,'bytes':dump.stat().st_size,'sha256':checksum,
            'source_read_only':True,'restore_role_superuser':False,'restore_tables_owned_by_app_role':True,'schema_data_sequences_identical':True,'equal_count_content_mutation_detected':content_mutation_detected,'append_only':append_only,
            'truncated_archive_rejected_empty_target':True,'late_sql_error_rolled_back_empty_target':True,
            'source_unchanged':True,'fingerprint':before,'commands':commands,'seconds':round(time.monotonic()-started,3),
            'limitations':['Single database only; roles/ACLs/tablespaces, media files and production recovery time are outside this drill',
                'The trusted synthetic archive executes application schema code; never restore an untrusted dump',
                'No existing database, service, account, remote host or paid API was accessed']}
        manifest=out/'restore-manifest.json'
        with manifest.open('x') as file:os.chmod(manifest,0o600);json.dump(result,file,indent=2);file.flush();os.fsync(file.fileno())
        print(json.dumps({key:result[key] for key in ('status','bytes','sha256','seconds','source_unchanged')}))
    finally:
        Path(passfile).unlink(missing_ok=True);part.unlink(missing_ok=True)


def main():
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--bin',required=True)
    parser.add_argument('--artifacts',required=True)
    args=parser.parse_args();root=private_fixture();bin_path=Path(args.bin).resolve();out=Path(args.artifacts).resolve()
    if not out.is_relative_to(REPO/'artifacts'):raise RuntimeError('Fixture evidence must stay inside ignored repository artifacts')
    for tool in ('pg_dump','pg_restore','createdb','psql'):
        if not (bin_path/tool).is_file():raise RuntimeError('Required native PostgreSQL tools are missing')
    out.mkdir(mode=0o700,parents=True,exist_ok=False)
    run(bin_path,out,root)


if __name__=='__main__':main()
