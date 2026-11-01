# Archive worker comparison — October 31–November 1, 2026

Selected **six total workers: two DramaRama and four Tonight's Conversation**.
The new configuration was independently checked running and advancing uploads at 2026-11-01T04:19:09.837028+00:00.

| Total worker cap | Drama files | Tonight files | Total verified | Files/min | MB/s | Measured recovery pauses |
|---|---:|---:|---:|---:|---:|---:|
| 6, recent baseline | 47 | 33 | 80 | 4.00 | 1.66 | 0 |
| 7, live trial | 52 | 30 | 82 | 4.10 | 1.26 | 2 |
| 8, live trial | 29 | 30 | 59 | 2.95 | 1.20 | 1 |

The seven-worker cap improved file count only 2.5% over the recent six-worker
baseline and needed two recoveries: a TikTok HTTP 500 and a TLS media timeout.
The eight-worker cap was about 26% slower than baseline and needed one HTTP 500
recovery. A separate Drive-checkpoint startup timeout before the first measured
window is excluded from the table. Higher worker counts did not show a useful,
reliable gain in this run. The selection audit also keeps fewer workers when
rates differ by less than 10%, including the six-worker baseline.

The two prospective trials each measured 20 minutes after 60 s warm-up. Only
DramaRama was gracefully drained/restarted; Tonight remained at four workers
and the same PID. Seven meant 3+4, eight meant 4+4. Existing adaptive cooldown and
worker reduction stayed enabled, so these are configured-cap comparisons, not
constant-concurrency lab runs. Recovery pauses stayed in the measured windows.
The six-worker comparison is retrospective: its preceding 20 minutes, not a newly
rerun matched-file trial. Every window used different real missing archive files;
file sizes/durations and network conditions varied. These observations do not
prove that worker count caused the server errors or establish a universal optimum.

- 7 workers: 2026-10-31 23:29:41 EDT to 2026-10-31 23:49:41 EDT; 1200.05 measured seconds. Reported memory headroom 33–47%; whole-Mac swapout delta 3.46 GB.
- 8 workers: 2026-10-31 23:53:48 EDT to 2026-11-01 00:13:48 EDT; 1200.10 measured seconds. Reported memory headroom 32–45%; whole-Mac swapout delta 5.60 GB.

Resource figures describe the whole Mac, including other apps. No sustained
very-low-headroom rollback threshold was reached. Raw-video buffer budgets were
384 MiB for 3 Drama workers and512 MiB for 4; Tonight stayed1024 MiB. Final Drama
budget is256 MiB. Decoder/runtime overhead is additional.

All 141newly verified trial IDs are disjoint between trials and remain marked
uploaded. Fresh authenticated Drive reads of eight sampled files across both
sources/trials independently verified original identity/parent, byte size, MD5
and SHA256 against full-decode receipts. The archive itself performs those checks
for every successful upload. No completed video was redownloaded as a benchmark
fixture; all test transfers advanced the actual archives. No local archive media
files were found. DramaRama remains standalone at Drive root; Tonight remains
under The Ick Room. No queue, scheduling, publication or app deployment changes.

49 focused checks pass. Their service/time fixtures are simulated; the timed
transfers, cloud checks and resource measurements above are actual. Archive core
and metadata helpers remained frozen at recorded SHA256 values throughout both
windows. The benchmark started from repository commit 439ecc0; the final selection
review adds a regression check that close comparisons retain the lower baseline.
Both the original and reviewed rule select 6 for these actual results.

Live progress at 2026-11-01T04:19:09.837028+00:00: DramaRama 445/12171,
Tonight 404/979. This is a progress snapshot, not full
archive completion. Active PIDs: Drama 25162, Tonight 63531.
The one-shot benchmark is complete; the archive workers continue with their
normal bounded recovery. Do not automatically rerun the benchmark.

Reproducible controller: scripts/benchmark_archive_workers.py.
Private evidence: artifacts/archive-worker-benchmark-20261031/state.json,
result-7.json, result-8.json, decision.json, independent-verification.json,
final-running-state.json and per-run logs. Current source launches remain in
each archive's latest-run.json; artifacts/current-worker-benchmark.json records
the completed controller and chosen active archive PIDs. Credentials/raw receipts
remain excluded from GitHub.
