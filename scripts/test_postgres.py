"""Start a private, temporary PostgreSQL cluster, run contracts and stop it.

The runtime JSON supplies bin/share/libraries for locally available PostgreSQL.
No service is registered, TCP listener enabled or existing database contacted.
"""
import argparse
import json
import os
from pathlib import Path
import shutil
import subprocess
import sys
import tempfile
from urllib.parse import urlencode

repo = Path(__file__).resolve().parents[1]
parser = argparse.ArgumentParser()
parser.add_argument('--runtime', required=True, help='JSON with bin, share and optional libraries')
parser.add_argument('--artifacts', default='artifacts/postgres')
parser.add_argument('pytest_args', nargs='*')
args = parser.parse_args()
cfg = json.loads(Path(args.runtime).read_text())
bin_path, share = Path(cfg['bin']).resolve(), Path(cfg['share']).resolve()
out = (repo / args.artifacts).resolve()
if not out.is_relative_to(repo/'artifacts'): raise SystemExit('Evidence must stay under ignored artifacts/')
out.mkdir(parents=True, exist_ok=True)
root = Path(tempfile.mkdtemp(prefix='scpg-', dir='/tmp')).resolve()
(root/'.scrapper-postgres-fixture').touch()
(root/'socket').mkdir(mode=0o700)
env = {k: os.environ[k] for k in ('PATH','HOME','TMPDIR','LANG','SYSTEMROOT') if k in os.environ}
if cfg.get('libraries'): env['DYLD_LIBRARY_PATH'] = ':'.join(cfg['libraries'])
env.update(SCRAPPER_PG_TEST_ROOT=str(root), PYTHONPATH=str(repo),
    DATABASE_URL='postgresql+psycopg2://scrapper_fixture@/scrapper_fixture?'+urlencode({'host':str(root/'socket')}))
log = (out/'server.log').resolve()
started = False
result = 1
try:
    with (out/'initdb.txt').open('w') as file:
        subprocess.run([str(bin_path/'initdb'),'-D',str(root/'cluster'),'-L',str(share),'-U','scrapper_fixture',
            '-A','trust','--encoding=UTF8','--locale=C','--data-checksums'],env=env,stdout=file,stderr=subprocess.STDOUT,check=True)
    subprocess.run([str(bin_path/'pg_ctl'),'-D',str(root/'cluster'),'-l',str(log),'-w','start','-o',
        f"-h '' -k {root/'socket'} -c shared_buffers=16MB -c max_connections=30"],env=env,check=True)
    started = True
    subprocess.run([str(bin_path/'createdb'),'-h',str(root/'socket'),'-U','scrapper_fixture','scrapper_fixture'],env=env,check=True)
    manifest={'root':str(root),'postgres_runtime':str(bin_path),'network':'private Unix socket; no TCP listener',
        'database':'new scrapper_fixture only','external_spend_usd':0}
    (out/'manifest.json').write_text(json.dumps(manifest,indent=2))
    with (out/'pytest.txt').open('w') as file:
        result=subprocess.run([sys.executable,'-m','pytest','backend/pgtests','-q',
            '-W','error::pytest.PytestUnhandledThreadExceptionWarning',f'--junitxml={out/"pytest.xml"}',*args.pytest_args],
            cwd=repo,env=env,stdout=file,stderr=subprocess.STDOUT).returncode
    if result == 0:
        with (out/'schema.sql').open('w') as file:
            subprocess.run([str(bin_path/'pg_dump'),'-h',str(root/'socket'),'-U','scrapper_fixture',
                '--schema-only','scrapper_fixture'],env=env,stdout=file,check=True)
finally:
    if started:
        subprocess.run([str(bin_path/'pg_ctl'),'-D',str(root/'cluster'),'-w','stop','-m','fast'],env=env,check=True)
    if result == 0 and (root/'.scrapper-postgres-fixture').is_file(): shutil.rmtree(root)
    elif root.exists(): print('Stopped PostgreSQL fixture retained:',root)
sys.exit(result)
