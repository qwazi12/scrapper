#!/usr/bin/env python3
"""One-shot owner-approved 7/8-worker archive benchmark; no media on disk.

Only DramaRama is drained/restarted. Tonight's Conversation stays at four.
Measures actual newly verified IDs, not download starts or repeated test media.
An owner stop, access refusal or unexpected process replacement ends this run.
"""
from __future__ import annotations

import argparse
import datetime as dt
import fcntl
import hashlib
import json
import os
from pathlib import Path
import re
import signal
import subprocess
import sys
import time

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from scripts.archive_memory import atomic_json, now, safe_error

DRAMA = 'dramamodeonxprime-mx-20261031'
TONIGHT = 'tonights-conversation-20261031'


class ResourcePressure(RuntimeError):
    pass


def snapshot(root, receipts=False):
    jobs = {}
    for name in (DRAMA, TONIGHT):
        folder = root / 'artifacts' / name
        state = json.loads((folder / 'ledger.json').read_text())
        if receipts and (folder / 'receipts').exists():
            for path in (folder / 'receipts').glob('*.json'):
                row = json.loads(path.read_text())['row']
                if row.get('video_id') in state['items']:
                    state['items'][row['video_id']] = row
        jobs[name] = state
    return jobs


def uploaded(job):
    return {vid: row for vid, row in job['items'].items() if row['status'] == 'uploaded'}


def rates(before, after, seconds):
    result = {}
    for name in (DRAMA, TONIGHT):
        old, new = uploaded(before[name]), uploaded(after[name])
        if not set(old).issubset(new):
            raise ValueError('Previously verified IDs disappeared; stop benchmark')
        added = [row for vid, row in new.items() if vid not in old]
        result[name] = {'completed': len(added), 'videos_per_minute': len(added) * 60 / seconds,
                        'bytes': sum(row.get('file_size', 0) for row in added),
                        'media_seconds': sum(row.get('duration', 0) for row in added),
                        'new_video_ids': [row['video_id'] for row in added]}
    result['total_videos_per_minute'] = sum(result[n]['videos_per_minute'] for n in (DRAMA, TONIGHT))
    result['total_MB_per_second'] = sum(result[n]['bytes'] for n in (DRAMA, TONIGHT)) / seconds / 1e6
    return result


def choose(baseline, seven, eight):
    """Require a material gain, no degraded Tonight rate, and healthy operation."""
    eligible = [r for r in (seven, eight) if r['healthy']]
    if not eligible:
        return 6, 'Neither higher-worker trial was healthy; retain six.'
    best = eligible[0]
    if len(eligible) == 2:
        if (eight['rates']['total_videos_per_minute'] >= seven['rates']['total_videos_per_minute'] * 1.10 and
                eight['rates'][TONIGHT]['videos_per_minute'] >= seven['rates'][TONIGHT]['videos_per_minute'] * .90 and
                eight['rates']['total_MB_per_second'] >= seven['rates']['total_MB_per_second'] * .90):
            best = eight
    if (baseline['rates']['total_videos_per_minute'] > 0 and
            best['rates']['total_videos_per_minute'] < baseline['rates']['total_videos_per_minute'] * 1.10):
        return 6, 'Higher concurrency did not show a material gain over the recent six-worker baseline; keep fewer workers.'
    return best['total_workers'], ('Eight delivered a clear gain without reducing Tonight throughput or transfer volume.'
                                  if best['total_workers'] == 8 else
                                  'Seven is the healthier/lower-resource result; eight did not show a clear usable gain.')


def machine():
    stat = subprocess.check_output(['vm_stat'], text=True)
    page = int(re.search(r'page size of (\d+) bytes', stat)[1])
    swaps = int(re.search(r'Swapouts:\s+(\d+)', stat)[1]) * page
    free = subprocess.check_output(['memory_pressure', '-Q'], text=True)
    percent = float(re.search(r'(\d+(?:\.\d+)?)%', free)[1])
    return {'load_average': list(os.getloadavg()), 'swapout_bytes_total': swaps,
            'memory_free_percent_reported': percent, 'logical_cpus': os.cpu_count()}


class Benchmark:
    def __init__(self, args):
        self.args, self.root, self.out = args, Path.cwd(), args.output.absolute()
        self.out.mkdir(parents=True, exist_ok=True)
        self.out.chmod(0o700)
        self.lock = (self.out / 'benchmark.lock').open('a')
        fcntl.flock(self.lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
        self.stop_requested = False
        signal.signal(signal.SIGTERM, lambda *_: setattr(self, 'stop_requested', True))
        signal.signal(signal.SIGINT, lambda *_: setattr(self, 'stop_requested', True))
        self.original = snapshot(self.root, receipts=True)
        self.tonight_pid = self.original[TONIGHT]['pid']
        plan = json.loads((self.root / 'artifacts' / DRAMA / 'latest-run.json').read_text())
        self.command = plan['command']
        self.source_hashes = {name: hashlib.sha256((self.root / name).read_bytes()).hexdigest()
                              for name in ('scripts/archive_memory.py', 'scripts/tiktok_access.py',
                                           'scripts/tiktok_drama_metadata.py')}
        self.children = []
        self.state = {'created_at': now(), 'pid': os.getpid(), 'status': 'preflight',
                      'duration_per_trial_seconds': args.duration, 'warmup_seconds': args.warmup,
                      'fixed_tonight_pid': self.tonight_pid, 'source_sha256': self.source_hashes,
                      'scope': 'Real archive transfers only; no queue, publishing or local media files',
                      'stages': [], 'decision_rule': '10% total-count gain; Tonight and bytes must retain at least90%; no new cooldown/errors.'}
        self.save()

    def save(self, **values):
        self.state.update(values, updated_at=now())
        atomic_json(self.out / 'state.json', self.state)

    def sleep(self, seconds):
        end = time.monotonic() + seconds
        while time.monotonic() < end:
            if self.stop_requested:
                raise InterruptedError('Owner stopped benchmark; no automatic worker restart')
            time.sleep(min(1, end - time.monotonic()))

    def owned_pid(self, job, expected):
        if job['pid'] != expected:
            raise RuntimeError('Archive process was replaced externally; stop benchmark')
        try:
            command = subprocess.check_output(['ps', '-p', str(expected), '-o', 'command='], text=True).strip()
        except subprocess.CalledProcessError:
            raise RuntimeError('Archive worker exited; stop benchmark')
        required = str(self.root / 'artifacts' / DRAMA)
        if 'scripts/archive_memory.py' not in command or required not in command:
            raise RuntimeError('PID no longer matches exact owned DramaRama command')

    def check(self, jobs, expected):
        if self.stop_requested:
            raise InterruptedError('Owner stopped benchmark; no automatic restart')
        self.owned_pid(jobs[DRAMA], expected)
        if jobs[DRAMA]['status'] in ('blocked', 'stopped', 'complete', 'complete_with_unavailable'):
            raise RuntimeError('DramaRama is no longer running: ' + str(jobs[DRAMA].get('error')))
        if jobs[TONIGHT]['pid'] != self.tonight_pid or jobs[TONIGHT]['status'] not in ('running', 'cooldown'):
            raise RuntimeError('Fixed Tonight worker changed/stopped; comparison interrupted')
        os.kill(self.tonight_pid, 0)
        if jobs[TONIGHT].get('concurrency') != 4:
            raise RuntimeError('Tonight no longer has its fixed four-worker configuration')
        for name in (DRAMA, TONIGHT):
            if not set(uploaded(self.original[name])).issubset(uploaded(jobs[name])):
                raise ValueError('Previously completed IDs changed')

    def drain(self, jobs):
        pid = jobs[DRAMA]['pid']
        self.owned_pid(jobs[DRAMA], pid)
        while jobs[DRAMA]['status'] == 'cooldown':
            self.save(status='waiting_for_existing_cooldown', next_retry_at=jobs[DRAMA].get('next_retry_at'))
            self.sleep(5)
            jobs = snapshot(self.root)
            self.check(jobs, pid)
        if jobs[DRAMA]['status'] != 'running':
            raise RuntimeError('Do not change concurrency during access failure/owner stop')
        self.save(status='draining', draining_pid=pid)
        os.kill(pid, signal.SIGUSR1)
        end = time.monotonic() + 1200
        while time.monotonic() < end:
            # poll() also reaps any archive children spawned by this controller.
            for child in self.children:
                child.poll()
            try:
                os.kill(pid, 0)
            except ProcessLookupError:
                closed = snapshot(self.root, receipts=True)
                if closed[DRAMA]['status'] != 'stopped' or closed[DRAMA].get('error'):
                    raise RuntimeError('Drain did not finish cleanly; no restart')
                return closed
            self.sleep(2)
        raise TimeoutError('Owned worker did not drain; leave it untouched')

    def start(self, count):
        for name, digest in self.source_hashes.items():
            if hashlib.sha256((self.root / name).read_bytes()).hexdigest() != digest:
                raise ValueError('Archive source changed during benchmark')
        cmd = self.command.copy()
        cmd[cmd.index('--concurrency') + 1] = str(count)
        cmd[cmd.index('--ram-budget-mib') + 1] = str(count * 128)
        log = self.out / f'drama-{count}-workers-{int(time.time())}.log'
        with log.open('a') as stream:
            child = subprocess.Popen(cmd, stdout=stream, stderr=subprocess.STDOUT, start_new_session=True)
        self.children.append(child)
        coffee = subprocess.Popen(['caffeinate', '-dimsu', '-w', str(child.pid)],
                                  stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, start_new_session=True)
        plan = {'started_at': now(), 'pid': child.pid, 'caffeinate_pid': coffee.pid,
                'status': 'starting', 'command': cmd, 'concurrency': count, 'ram_budget_mib': count * 128,
                'benchmark_pid': os.getpid(), 'benchmark_state': str(self.out / 'state.json'),
                'source_sha256': self.source_hashes, 'log': str(log),
                'folder_id': self.original[DRAMA]['folder_id'], 'folder_name': 'DramaRama',
                'scope': 'Archive only; no queue/scheduling/publication; RAM media only'}
        atomic_json(self.root / 'artifacts' / DRAMA / 'latest-run.json', plan)
        self.save(active_drama_pid=child.pid, requested_total_workers=count + 4, active_log=str(log))
        end = time.monotonic() + 300
        while time.monotonic() < end:
            if child.poll() is not None:
                raise RuntimeError('New worker exited during startup')
            jobs = snapshot(self.root)
            if jobs[DRAMA]['pid'] == child.pid and jobs[DRAMA].get('active_concurrency') == count:
                self.check(jobs, child.pid)
                self.sleep(self.args.warmup)
                return child.pid
            self.sleep(2)
        raise TimeoutError('New worker did not reach configured concurrency')

    def trial(self, count):
        self.drain(snapshot(self.root))
        pid = self.start(count)
        before = snapshot(self.root, receipts=True)
        self.check(before, pid)
        stage = {'total_workers': count + 4, 'drama_workers': count, 'started_at': now(),
                 'start_counts': {n: len(uploaded(before[n])) for n in before}, 'samples': []}
        self.save(status='measuring', current_stage=stage)
        begin = time.monotonic()
        while time.monotonic() - begin < self.args.duration:
            self.sleep(min(self.args.sample, self.args.duration - (time.monotonic() - begin)))
            jobs = snapshot(self.root)
            self.check(jobs, pid)
            health = machine()
            stage['samples'].append({'at': now(), 'elapsed': time.monotonic() - begin, **health,
                'jobs': {n: {'status': jobs[n]['status'], 'effective_workers': jobs[n].get('active_concurrency'),
                             'uploaded': len(uploaded(jobs[n])), 'error': jobs[n].get('error'),
                             'cooldown_restarts': jobs[n].get('cooldown_restarts', 0)} for n in jobs}})
            self.save(status='measuring', current_stage=stage)
            if (len(stage['samples']) >= 3 and
                    all(s['memory_free_percent_reported'] < 10 for s in stage['samples'][-3:])):
                raise ResourcePressure('Sustained very low reported memory headroom; stop escalation')
        elapsed = time.monotonic() - begin
        after = snapshot(self.root, receipts=True)
        self.check(after, pid)
        stage.update(finished_at=now(), elapsed_seconds=elapsed, rates=rates(before, after, elapsed),
                     end_counts={n: len(uploaded(after[n])) for n in after},
                     new_cooldowns={n: after[n].get('cooldown_restarts', 0) - before[n].get('cooldown_restarts', 0) for n in after})
        stage['healthy'] = (not any(stage['new_cooldowns'].values()) and
                            all(s['jobs'][n]['status'] == 'running' and not s['jobs'][n]['error']
                                for s in stage['samples'] for n in (DRAMA, TONIGHT)))
        self.state['stages'].append(stage)
        self.save(current_stage=None)
        atomic_json(self.out / f'result-{count + 4}.json', stage)
        print(json.dumps({'stage_complete': count + 4, 'healthy': stage['healthy'],
                          'videos_per_minute': stage['rates']['total_videos_per_minute']}), flush=True)
        return stage

    def run(self):
        self.check(self.original, self.original[DRAMA]['pid'])
        baseline_after = snapshot(self.root, receipts=True)
        cutoff = dt.datetime.now(dt.timezone.utc) - dt.timedelta(seconds=self.args.duration)
        baseline_before = json.loads(json.dumps(baseline_after))
        for job in baseline_before.values():
            for row in job['items'].values():
                if row['status'] == 'uploaded' and dt.datetime.fromisoformat(row['verified_at']) >= cutoff:
                    row['status'] = 'not_yet_uploaded_at_baseline_start'
        baseline = {'total_workers': 6, 'kind': 'recent retrospective baseline',
                    'rates': rates(baseline_before, baseline_after, self.args.duration)}
        self.save(baseline=baseline)
        seven, eight = self.trial(3), self.trial(4)
        winner, reason = choose(baseline, seven, eight)
        current = snapshot(self.root)
        if current[DRAMA].get('concurrency') != winner - 4:
            self.drain(current)
            pid = self.start(winner - 4)
        else:
            pid = current[DRAMA]['pid']
        self.check(snapshot(self.root), pid)
        decision = {'winner_total_workers': winner, 'drama_workers': winner - 4, 'tonight_workers': 4,
                    'reason': reason, 'pid': pid, 'decided_at': now(),
                    'limitation': 'Different real archive files per stage; no re-downloads for identical-file comparison.'}
        self.save(status='complete', decision=decision)
        atomic_json(self.out / 'decision.json', decision)
        print(json.dumps(decision), flush=True)


def main():
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument('--output', type=Path, required=True)
    p.add_argument('--duration', type=int, default=1200)
    p.add_argument('--warmup', type=int, default=60)
    p.add_argument('--sample', type=int, default=30)
    args = p.parse_args()
    if args.duration < 600 or not 30 <= args.warmup <= 180 or not 10 <= args.sample <= 60:
        p.error('Use at least10 measured minutes,30–180 warmup seconds and10–60 sample seconds')
    benchmark = Benchmark(args)
    try:
        benchmark.run()
    except Exception as exc:
        if isinstance(exc, ResourcePressure) and not benchmark.stop_requested:
            try:
                benchmark.drain(snapshot(benchmark.root))
                baseline_pid = benchmark.start(2)
                benchmark.save(resource_rollback_pid=baseline_pid, rollback_total_workers=6)
            except Exception as rollback:
                benchmark.save(rollback_error=safe_error(rollback))
        benchmark.save(status='owner_stopped' if benchmark.stop_requested else 'blocked', error=safe_error(exc))
        print(json.dumps({'status': benchmark.state['status'], 'error': safe_error(exc)}), flush=True)
        return 2
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
