# Database recovery and upgrade procedure

SQLite backup is an online snapshot through SQLite's backup API, including committed WAL transactions. Each snapshot has a unique timestamp/UUID name, verified SQLite integrity, gzip CRC, raw SHA-256 and compressed SHA-256. Only complete, fsynced, verified gzip files receive a visible `.db.gz` name. Temporary `.part` files are not recovery evidence. Keep the original database, WAL and SHM files together until a verified independent snapshot exists.

Startup takes the pre-migration snapshot **before `create_all` or additive migrations**. Snapshot failure stops startup; it must never become a warning followed by schema changes. New empty databases have no previous state to snapshot. Pre-migration snapshots do not trigger uploads or retention. The pinned original schema is exercised by the disposable upgrade test; this does not prove every historical installation can upgrade.

The app holds a shared POSIX maintenance lock beside its SQLite file. Restore requires the exclusive lock and explicit offline confirmation. Stop every web process, scheduler and Mac/other worker before a restore, including old app versions which do not implement this lock. The sidecar lock file is permanent; do not delete it to evade a live holder. Back up the mounted volume and media independently; a database snapshot does not contain media files.

First run a dry comparison, with exact absolute paths:

```sh
python scripts/restore_db.py --from /absolute/snapshot.db.gz --to /absolute/scrapper.db
```

After stopping all app/workers, execute the already reviewed restore:

```sh
python scripts/restore_db.py --from /absolute/snapshot.db.gz --to /absolute/scrapper.db --confirm --offline-confirmed
```

The CLI copies/decompresses the input into private temporary storage on the target filesystem, checks storage and integrity, and prints table counts. Before mutation it acquires the exclusive lock and creates a verified emergency snapshot using the online backup API, including WAL. The restore is transactional through SQLite's backup API; final integrity and table counts must match. Failure returns a nonzero exit and retains the emergency snapshot. Restore that emergency snapshot with the same offline procedure if rollback is needed. Inspect ownership/modes, app health, jobs and retained media references before resuming workers. Nothing in this local test lane restores a live database.

Local retention retains at least one copy and skips files held by downloads/transfers; the current backup name is protected even when its timestamp is older than another snapshot. Status JSON is atomically replaced and an older completion cannot replace newer status from another worker. Drive uploads use independent remote MD5/size verification and retained creation/verification receipts; local success alone is not an off-disk backup. Remote deletion remains disabled.

PostgreSQL backup/restore coverage is **unverified**. The SQLite endpoint returns an explicit unsupported response; Railway hosting does not establish that recoverable provider backups exist. A separate `pg_dump`/`pg_restore` drill, retention configuration, permissions and recovery-time evidence are required before claiming production database recovery.

Existing SQLite Studio/Countdown tables retain their original integer allocation policy until explicitly upgraded offline. After a verified backup and shutdown, first inspect:

```sh
python scripts/upgrade_sqlite_identity.py --database /absolute/scrapper.db
```

Then apply with `--confirm --offline-confirmed`. The CLI requires an exclusive maintenance lock, creates a verified emergency snapshot, transactionally rebuilds the two known integer-primary-key tables with AUTOINCREMENT, copies all columns, restores indexes/triggers, checks foreign keys/integrity and rolls both changes back if either schema is unexpected. Existing local numeric Studio directories raise the sequence floor. A repeat is a no-op. No live installation was migrated. This prevents future reuse after migration; it cannot reconstruct every ID historically deleted before a ledger existed. Creation-time identities still fence old evidence/history. Inspect the actual database location and media root when a custom DATABASE_URL stores the SQLite file outside DATA_DIR; the default orphan-directory scan assumes `studio/` beside the database.
