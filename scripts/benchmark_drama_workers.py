#!/usr/bin/env python3
"""Compare three/four DramaRama workers using real missing archive transfers.

Tonight's Conversation must stay complete. Existing cooldown/integrity controls
remain active. Drain buffers before changing caps; never repeat completed media.
"""
from __future__ import annotations

import argparse
import hashlib
import json
from pathlib import Path
import sys
import time

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from scripts.benchmark_archive_workers import (
    Benchmark, DRAMA, TONIGHT, ResourcePressure, atomic_json, machine, now,
    safe_error, snapshot,
)


def completed(job):
    return {v: r for v, r in job['items'].items() if r['status'] in ('uploaded', 'aliased')}


def preserved(before, after):
    for vid, old in completed(before).items():
        row = after['items'].get(vid, {})
        if any(row.get(k) != old.get(k) for k in (
                'status', 'drive_file_id', 'canonical_video_id', 'canonical_drive_file_id',
                'sha256', 'md5', 'file_size')):
            raise ValueError('Previously completed source identity changed: ' + vid)


def measured_rates(before, after, seconds):
    if seconds <= 0:
        raise ValueError('Positive measurement duration required')
    preserved(before, after)
    old = completed(before)
    added = [r for v, r in completed(after).items() if v not in old]
    files = [r for r in added if r['status'] == 'uploaded']
    aliases = [r for r in added if r['status'] == 'aliased']
    return {'uploaded_files': len(files), 'aliased_sources': len(aliases),
            'completed_sources': len(added), 'files_per_minute': len(files) * 60 / seconds,
            'sources_per_minute': len(added) * 60 / seconds,
            'bytes': sum(r.get('file_size', 0) for r in files),
            'MB_per_second': sum(r.get('file_size', 0) for r in files) / seconds / 1e6,
            'media_seconds': sum(r.get('duration', 0) for r in files),
            'new_video_ids': [r['video_id'] for r in files],
            'new_alias_ids': [r['video_id'] for r in aliases]}


def choose(three, four):
    healthy = [s for s in (three, four) if s['healthy'] and s['rates']['uploaded_files'] > 0]
    if not healthy:
        return 2, 'Neither trial had healthy measured uploads; restore two workers.'
    if len(healthy) == 1:
        return healthy[0]['workers'], 'Only this trial had healthy measured uploads.'
    a, b = three['rates'], four['rates']
    if (b['files_per_minute'] >= a['files_per_minute'] * 1.10 and
            b['MB_per_second'] >= a['MB_per_second'] * .90 and
            b['media_seconds'] / four['elapsed_seconds'] >=
            a['media_seconds'] / three['elapsed_seconds'] * .90):
        return 4, 'Four showed at least10% more verified files with no material loss of bytes/media throughput.'
    return 3, 'Four did not show a clear usable gain; keep the lower-resource three-worker setting.'


class DramaBenchmark(Benchmark):
    other_workers = 0

    def __init__(self, args):
        super().__init__(args)
        if self.original[TONIGHT]['status'] != 'complete':
            raise ValueError('Tonight must already be complete; this is a solo Drama comparison')
        self.state.pop('fixed_tonight_pid', None)
        self.save(benchmark_kind='drama_solo_3_vs_4', trial_workers=[3, 4],
                  completed_tonight_pid=self.tonight_pid,
                  decision_rule='Healthy measured uploads; four needs10% more files and at least90% bytes/media throughput; otherwise three; two if both unhealthy.',
                  controller_sha256=hashlib.sha256(Path(__file__).read_bytes()).hexdigest())

    def check(self, jobs, expected):
        if self.stop_requested:
            raise InterruptedError('Owner stopped benchmark; no automatic restart')
        self.owned_pid(jobs[DRAMA], expected)
        if jobs[DRAMA]['status'] not in ('running', 'cooldown', 'preflight'):
            raise RuntimeError('DramaRama stopped: ' + safe_error(jobs[DRAMA].get('error')))
        if jobs[TONIGHT]['status'] != 'complete' or jobs[TONIGHT]['pid'] != self.tonight_pid:
            raise RuntimeError('Completed Tonight job changed; stop benchmark')
        try:
            for name in (DRAMA, TONIGHT):
                preserved(self.original[name], jobs[name])
        except ValueError:
            # A large ledger snapshot may lag durable per-row receipts by10s.
            latest = snapshot(self.root, receipts=True)
            for name in (DRAMA, TONIGHT):
                preserved(self.original[name], latest[name])

    def trial(self, count):
        self.drain(snapshot(self.root, receipts=True))
        pid = self.start(count)
        before = snapshot(self.root, receipts=True)
        self.check(before, pid)
        self.save(status='measuring', measured_workers=count)
        stage = {'workers': count, 'pid': pid, 'started_at': now(),
                 'start_uploaded': sum(r['status'] == 'uploaded' for r in before[DRAMA]['items'].values()),
                 'samples': [], 'start_machine': machine()}
        self.save(current_stage=stage)
        begin = time.monotonic()
        while time.monotonic() - begin < self.args.duration:
            self.sleep(min(self.args.sample, self.args.duration - (time.monotonic() - begin)))
            jobs = snapshot(self.root, receipts=True)
            self.check(jobs, pid)
            j, health = jobs[DRAMA], machine()
            stage['samples'].append({
                'at': now(), 'elapsed': time.monotonic() - begin, **health,
                'status': j['status'], 'effective_workers': j.get('active_concurrency'),
                'error': j.get('error'), 'cooldown_restarts': j.get('cooldown_restarts', 0),
                'rates_so_far': measured_rates(before[DRAMA], j, time.monotonic() - begin)})
            self.save(current_stage=stage)
            if len(stage['samples']) >= 3 and all(
                    s['memory_free_percent_reported'] < 10 for s in stage['samples'][-3:]):
                raise ResourcePressure('Sustained low memory headroom; stop escalation')
        elapsed = time.monotonic() - begin
        after = snapshot(self.root, receipts=True)
        self.check(after, pid)
        stage.update(finished_at=now(), elapsed_seconds=elapsed,
                     rates=measured_rates(before[DRAMA], after[DRAMA], elapsed),
                     end_machine=machine(),
                     new_cooldowns=after[DRAMA].get('cooldown_restarts', 0) - before[DRAMA].get('cooldown_restarts', 0))
        stage['healthy'] = (stage['new_cooldowns'] == 0 and all(
            s['status'] == 'running' and not s['error'] and s['effective_workers'] == count
            for s in stage['samples']))
        self.state['stages'].append(stage)
        self.save(current_stage=None)
        atomic_json(self.out / f'result-{count}.json', stage)
        print(json.dumps({'stage_complete': count, 'healthy': stage['healthy'], 'rates': stage['rates']}), flush=True)
        return stage

    def run(self):
        self.check(self.original, self.original[DRAMA]['pid'])
        atomic_json(self.out / 'original-completions.json', {
            name: completed(job) for name, job in self.original.items()})
        three, four = self.trial(3), self.trial(4)
        winner, reason = choose(three, four)
        current = snapshot(self.root, receipts=True)
        if current[DRAMA].get('concurrency') != winner:
            self.drain(current)
            pid = self.start(winner)
        else:
            pid = current[DRAMA]['pid']
        self.check(snapshot(self.root, receipts=True), pid)
        decision = {'workers': winner, 'reason': reason, 'pid': pid, 'decided_at': now(),
                    'limitation': 'Different real missing files per trial; not a matched-file causal experiment. No completed file was redownloaded.'}
        atomic_json(self.out / 'decision.json', decision)
        self.save(status='complete', decision=decision)
        print(json.dumps(decision), flush=True)


def main():
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument('--output', type=Path, required=True)
    p.add_argument('--duration', type=int, default=600)
    p.add_argument('--warmup', type=int, default=30)
    p.add_argument('--sample', type=int, default=30)
    args = p.parse_args()
    if args.duration < 600 or not 30 <= args.warmup <= 180 or not 10 <= args.sample <= 60:
        p.error('Use at least10 measured minutes,30–180 warmup seconds and10–60 sample seconds')
    benchmark = DramaBenchmark(args)
    try:
        benchmark.run()
    except Exception as exc:
        if isinstance(exc, ResourcePressure) and not benchmark.stop_requested:
            try:
                benchmark.drain(snapshot(benchmark.root, receipts=True))
                pid = benchmark.start(2)
                benchmark.save(resource_rollback_pid=pid, rollback_total_workers=2)
            except Exception as rollback:
                benchmark.save(rollback_error=safe_error(rollback))
        benchmark.save(status='owner_stopped' if benchmark.stop_requested else 'blocked', error=safe_error(exc))
        print(json.dumps({'status': benchmark.state['status'], 'error': safe_error(exc)}), flush=True)
        return 2
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
