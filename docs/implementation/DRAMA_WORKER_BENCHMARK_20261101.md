# DramaRama solo worker comparison — November 1, 2026

Selected and retained **four DramaRama workers**. Both owner-requested trials
completed real ten-minute measurement windows, following 30-second warm-ups.
Tonight’s Conversation remained complete at 979 files throughout.

| Configured Drama workers | Verified files | Files/min | Verified MB/s | Media seconds | Recovery pauses |
|---|---:|---:|---:|---:|---:|
| 3 | 32 | 3.20 | 0.444 | 3,064 | 1 |
| 4 | 48 | 4.80 | 0.577 | 4,343 | 0 |

Four delivered 50% more verified files, about 30% more verified bytes per second
and 42% more media duration in these observations. The predeclared selection rule
preferred healthy measured uploads; four was the only trial without recovery.
When both trials are healthy, four needs at least 10% more files and at least
90% of the lower-cap run’s bytes/media throughput. Both unhealthy would retain
two workers instead.

The three-worker trial encountered a TikTok episode metadata HTTP 500 and a Drive
read timeout in one recovery round. It waited 60 seconds and resumed at two
workers under the existing adaptive policy. Eight samples show three effective
workers and twelve show two. Four effective workers were observed in all twenty
samples of its trial. These are configured-cap tests with normal recovery
enabled, not constant-concurrency laboratory comparisons. A delayed pre-existing
Drive upload was allowed to finish before either trial; it is excluded from the
measured windows.

The first window ran 08:18:42–08:28:42 EST (600.36 seconds); the second ran
08:30:44–08:40:44 EST (600.27 seconds). Different missing episodes, file sizes,
durations, network conditions and other Mac workloads were present. These results
support the practical setting chosen for this run; they do not prove worker count
caused any error or establish a universal 50% improvement.

The three-worker window reported 32–52% free-memory headroom and 3.03 GB of
whole-Mac swapouts; four reported 48–61% and 1.34 GB. These describe the whole Mac
and include unrelated applications. Neither reached the rollback threshold. Raw
media-buffer budgets were 384 and 512 MiB respectively, with a 128 MiB cap per
video. Decoder, Python and operating-system overhead is additional.

All 80 measured uploads are distinct source IDs and remain uploaded. No aliases
were counted as physical uploads in either window. All 1,453 original DramaRama
completions, including its 55 aliases, and all 979 Tonight completions retain their
status, Drive/canonical IDs, hashes and sizes. Fresh authenticated Drive reads of
six sampled files, three from each trial, independently match source identity,
parent, size, MD5 and SHA256 against full-decode receipts. Every successful archive
upload also undergoes the worker’s own remote verification.

At 08:41:34 EST, DramaRama PID 32656 was running with four configured/effective
workers, 1,494 physical uploads, 55 aliased sources, 10,622 remaining and zero
current failures. Counts continue changing. The benchmark controller PID 24737
is complete and must not be restarted. The active archive’s current command and
log are in its `latest-run.json`.

The archive core and metadata-helper SHA256 values stayed unchanged throughout
the trials. Existing source-duration/packet proof, full decoding, reserved Drive
IDs, exact-content deduplication, bounded retries/cooldowns and access restrictions
remained enabled. Media stays in RAM/pipes. Folder identity is unchanged:
standalone DramaRama under the Shared Drive root. No completed source was
redownloaded as a fixture. No queue, scheduling, publishing, file deletion or
application deployment was performed.

Implementation: `scripts/benchmark_drama_workers.py`, using existing graceful
drain/start and resource monitoring in `scripts/benchmark_archive_workers.py`.
83 focused tests passed; accounting/selection fixtures are simulated. The timed
transfers and six cloud reads described above are actual.

Private evidence (excluded from GitHub):
`artifacts/drama-worker-benchmark-20261101/`, including `state.json`,
`result-3.json`, `result-4.json`, `decision.json`, `original-completions.json`,
`independent-verification.json` and per-process logs. The current pointer is
`artifacts/current-drama-worker-benchmark.json`.
