#!/usr/bin/env python3
"""Offline, backed-up SQLite identity upgrade. Dry run unless both flags supplied."""
from __future__ import annotations
import argparse
import json
from pathlib import Path
import re
import sqlite3
import sys
sys.path.insert(0,str(Path(__file__).resolve().parents[1]))
from backend.app.sqlite_snapshots import compressed_snapshot,integrity,offline_lock,unique_stem

TABLES=('studio_projects','countdown_projects')


def upgrade(path,*,confirm=False,offline_confirmed=False):
    path=Path(path).resolve()
    if not path.is_file():raise ValueError('SQLite database does not exist')
    if confirm and not offline_confirmed:raise ValueError('Stop all apps/workers and confirm offline maintenance')
    with offline_lock(path):
        integrity(path)
        with sqlite3.connect(path) as conn:
            definitions={name:conn.execute('SELECT sql FROM sqlite_master WHERE type=? AND name=?',('table',name)).fetchone() for name in TABLES}
            needed={name:row[0] for name,row in definitions.items() if row and 'AUTOINCREMENT' not in row[0].upper()}
            result={'dry_run':not confirm,'tables':list(needed),'backup':None,'rows':{},'historical_id_guarantee':False}
            if not confirm or not needed:return result
            result['backup']=compressed_snapshot(path,path.parent/'backups',unique_stem('scrapper_pre_identity'))
            # Foreign keys are checked before commit, and this setting is restored on close.
            conn.execute('PRAGMA foreign_keys=OFF')
            conn.execute('PRAGMA legacy_alter_table=ON')
            conn.execute('BEGIN IMMEDIATE')
            try:
                for name,ddl in needed.items():
                    # Only the known original integer PK shape is accepted.
                    if not re.search(r'PRIMARY KEY\s*\(\s*id\s*\)',ddl,re.I):
                        raise ValueError(f'Unexpected primary-key definition for {name}; no tables changed')
                    replacement,count=re.subn(r'\bid\s+INTEGER\s+NOT NULL\b','id INTEGER NOT NULL PRIMARY KEY AUTOINCREMENT',ddl,count=1,flags=re.I)
                    if count!=1:raise ValueError(f'Unexpected id column for {name}')
                    replacement=re.sub(r',\s*PRIMARY KEY\s*\(\s*id\s*\)', '',replacement,count=1,flags=re.I)
                    temporary='identity_upgrade_'+name
                    replacement,count=re.subn(r'^(CREATE TABLE\s+)(?:"?'+name+r'"?)\b',lambda m:m[1]+'"'+temporary+'"',replacement,count=1,flags=re.I)
                    if count!=1:raise ValueError(f'Unexpected table definition for {name}')
                    objects=[r[0] for r in conn.execute('SELECT sql FROM sqlite_master WHERE tbl_name=? AND type IN (?,?) AND sql IS NOT NULL',(name,'index','trigger'))]
                    columns=[r[1] for r in conn.execute(f'PRAGMA table_info("{name}")')]
                    quoted=','.join('"'+c.replace('"','""')+'"' for c in columns)
                    before=conn.execute(f'SELECT count(*) FROM "{name}"').fetchone()[0]
                    conn.execute(replacement)
                    conn.execute(f'INSERT INTO "{temporary}" ({quoted}) SELECT {quoted} FROM "{name}"')
                    if conn.execute(f'SELECT count(*) FROM "{temporary}"').fetchone()[0]!=before:
                        raise ValueError('Copied row counts differ')
                    # Existing orphaned local studio directories must not be reused either.
                    high=conn.execute(f'SELECT coalesce(max(id),0) FROM "{name}"').fetchone()[0]
                    if name=='studio_projects':
                        root=path.parent/'studio'
                        if root.is_dir():high=max([high,*[int(p.name) for p in root.iterdir() if p.name.isdecimal() and p.is_dir()]])
                    conn.execute(f'DROP TABLE "{name}"')
                    conn.execute(f'ALTER TABLE "{temporary}" RENAME TO "{name}"')
                    updated=conn.execute('UPDATE sqlite_sequence SET seq=? WHERE name=?',(high,name))
                    if not updated.rowcount:conn.execute('INSERT INTO sqlite_sequence(name,seq) VALUES (?,?)',(name,high))
                    for statement in objects:conn.execute(statement)
                    result['rows'][name]={'copied':before,'sequence_floor':high}
                if conn.execute('PRAGMA foreign_key_check').fetchall():raise ValueError('Foreign-key check failed; identity upgrade rolled back')
                if conn.execute('PRAGMA integrity_check').fetchall()!=[('ok',)]:raise ValueError('Integrity check failed; identity upgrade rolled back')
                conn.commit()
            except BaseException:
                conn.rollback();raise
        integrity(path)
        return result


def main():
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--database',required=True)
    parser.add_argument('--confirm',action='store_true')
    parser.add_argument('--offline-confirmed',action='store_true')
    args=parser.parse_args()
    try:
        print(json.dumps(upgrade(args.database,confirm=args.confirm,offline_confirmed=args.offline_confirmed),indent=2));return 0
    except Exception as exc:
        print(f'Identity upgrade failed: {exc}',file=sys.stderr);return 1


if __name__=='__main__':raise SystemExit(main())
