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

PostgreSQL logical backup/restore is now **verified on a disposable local PostgreSQL16 fixture**, separately from operational production recovery. `scripts/test_postgres.py` runs the actual contract corpus, creates a custom `pg_dump` archive and invokes `scripts/verify_postgres_restore.py` before stopping its own cluster. The verifier accepts only the freshly marked, owner-private `/tmp/scpg-*` cluster and exact synthetic database on its Unix socket. It rejects other URLs/sockets, writable roots and source writes. No production URL/target is accepted.

The drill creates an unprivileged app role in its own fresh cluster and restores into new databases owned by that role with `--single-transaction --exit-on-error`, excluding source ownership/ACL/tablespace reassignment. The restore role has no superuser/create-role/create-database/replication privileges; it must own the restored application tables. Source dump sessions enforce read-only mode. It compares semantic row hashes/counts, application table columns/defaults, indexes, constraints, enum labels, trigger/function definitions and sequence configuration/state. An equal-row-count JSON mutation must change the data hash. An insert/delete attempt verifies the restored append-only journal guard and rolls back its sentinel. Truncated archives are rejected; an intentionally late SQL error rolls back the whole logical restore to an empty target. The source fingerprint must remain unchanged. All native subprocesses and SQL lock/query waits are bounded; dump bytes and their checksum remain in private ignored artifacts.

Run with an existing local PostgreSQL16 runtime JSON containing `bin`, `share` and optional library paths:

```sh
python scripts/test_postgres.py --runtime artifacts/postgres-runtime-env.json --artifacts artifacts/postgres-recovery-new-run
```

The harness manifest points to its unique restore evidence directory. That directory contains the custom archive, table-of-contents inspection, expected-failure logs, schema/data/sequence fingerprints and final `restore-manifest.json`. A successful tiny synthetic drill is not a production recovery-time objective. PostgreSQL’s [pg_dump documentation](https://www.postgresql.org/docs/16/app-pgdump.html) describes the single-database custom archive, and [pg_restore documentation](https://www.postgresql.org/docs/16/app-pgrestore.html) describes transactional restoration. The trusted fixture dump executes its source schema code; arbitrary external dumps must not be treated as safe inputs.

The application's automatic backup endpoint remains SQLite-only and reports PostgreSQL as unsupported. Railway hosting does not establish recoverable provider backups. Real database roles/ACLs/tablespaces, extensions/large objects outside the application corpus, media files, encrypted off-disk retention, production permissions and recovery time still require a separate operational drill. This code does not enable a nightly PostgreSQL backup, modify a live database or change provider retention.

Existing SQLite Studio/Countdown tables retain their original integer allocation policy until explicitly upgraded offline. After a verified backup and shutdown, first inspect:

```sh
python scripts/upgrade_sqlite_identity.py --database /absolute/scrapper.db
```

Then apply with `--confirm --offline-confirmed`. The CLI requires an exclusive maintenance lock, creates a verified emergency snapshot, transactionally rebuilds the two known integer-primary-key tables with AUTOINCREMENT, copies all columns, restores indexes/triggers, checks foreign keys/integrity and rolls both changes back if either schema is unexpected. Existing local numeric Studio directories raise the sequence floor. A repeat is a no-op. No live installation was migrated. This prevents future reuse after migration; it cannot reconstruct every ID historically deleted before a ledger existed. Creation-time identities still fence old evidence/history. Inspect the actual database location and media root when a custom DATABASE_URL stores the SQLite file outside DATA_DIR; the default orphan-directory scan assumes `studio/` beside the database.

## Publisher runtime protocol v1

Current engines mark each SQLite/PostgreSQL connection as implementing the
original-Studio source protocol. A version-named database trigger rejects writes
to queue posting status from unmarked connections, including an idempotent raw
posting-status UPDATE. SQLite uses an engine-registered function (old writers
receive a missing-function error); PostgreSQL checks a session setting and raises
a constraint error. Current owner receipt adoption/reconciliation remains
available. Plain queue reads and private-note writes from an unmarked connection
are covered; other old mutations are not a supported rollback workflow.

This marker is a compatibility signal, not an authorization secret or independent
source proof. Keep the actual retained-source and lease checks. Future protocols
add new fences/markers rather than dropping older ones. Startup installs v1 only
after its pre-migration backup and additive schema migration; failure stops
startup. Native PostgreSQL restore fingerprints include the function/trigger.

Stop **all** old web/scheduler/Mac publisher processes before upgrade or rollback
and reconcile outstanding provider submissions. The database cannot recall an
in-flight request or stop arbitrary old code that submits without writing it
(for example, already holding a posting row). Never treat a trigger pass as proof
that mixed-version publishing is safe. Rollback remains read-only for publication
until a compatible worker and exact-source admission are verified. No production
rollout or account-credential change has been executed by these local tests.

Evidence: three independent unmarked SQL paths reproduced failures before repair;
focused53passed4.19s includes the exact pinned baseline publish_queue_item function
against an old-style session, zero fake provider calls and preserved ready state.
Full backend766passed/1skip79.92s; actualPG290passed39.25s. Restore1.547s,207354bytes,
SHA26f046eed885af108f70ea842c98c182e008e869a296da46186fa98c46284b2b,
source unchanged. Scoped actual UI5passed49.2s covers current startup/LongForm and
Native delivery, residential work, receipt adoption and financial recovery;
source2ec8ff3c2ac712bc688f0e6dcee34d69b97e37d7e876c87d6bdb638e261d03db,
evidence artifacts/e2e/history/publisher-compatibility1. External delivery is
simulated. The preceding full29 browser generation is separate.
