"""Benchmark accounting/selection only; actual transfer trials are separate."""
from copy import deepcopy

import pytest

from scripts.benchmark_archive_workers import DRAMA, TONIGHT, choose, rates


def trial(workers, drama, tonight, volume, healthy=True):
    return {'total_workers': workers, 'healthy': healthy, 'rates': {
        'total_videos_per_minute': drama + tonight, 'total_MB_per_second': volume,
        DRAMA: {'videos_per_minute': drama}, TONIGHT: {'videos_per_minute': tonight}}}


def test_eight_requires_clear_gain_without_starving_fixed_job():
    baseline = trial(6, 2, 2, 1)
    seven = trial(7, 3, 2, 1.4)
    assert choose(baseline, seven, trial(8, 4, 2, 1.5))[0] == 8
    assert choose(baseline, seven, trial(8, 3.2, 2, 1.5))[0] == 7
    assert choose(baseline, seven, trial(8, 5, 1, 1.5))[0] == 7
    assert choose(baseline, seven, trial(8, 5, 2, .8))[0] == 7
    assert choose(baseline, seven, trial(8, 5, 2, 1.5, healthy=False))[0] == 7


def test_baseline_retained_if_higher_counts_are_unhealthy_or_slower():
    baseline = trial(6, 3, 2, 1.5)
    assert choose(baseline, trial(7, 1, 2, 1), trial(8, 1, 2, 1))[0] == 6
    assert choose(baseline, trial(7, 4, 2, 1, False), trial(8, 5, 2, 1, False))[0] == 6


def test_rates_count_only_new_verified_ids_and_refuse_lost_completions():
    before = {n: {'items': {'old': {'video_id': 'old', 'status': 'uploaded', 'file_size': 100}}}
              for n in (DRAMA, TONIGHT)}
    after = deepcopy(before)
    after[DRAMA]['items']['new'] = {'video_id': 'new', 'status': 'uploaded',
                                    'file_size': 600, 'duration': 90}
    after[DRAMA]['items']['in-progress'] = {'status': 'uploading', 'file_size': 9999}
    result = rates(before, after, 60)
    assert result['total_videos_per_minute'] == 1
    assert result[DRAMA]['bytes'] == 600 and result[DRAMA]['new_video_ids'] == ['new']
    assert result[TONIGHT]['completed'] == 0
    after[DRAMA]['items']['old']['status'] = 'failed'
    with pytest.raises(ValueError, match='verified IDs disappeared'):
        rates(before, after, 60)
