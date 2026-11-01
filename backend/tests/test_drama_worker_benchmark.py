"""Simulated accounting/selection; live trials are recorded separately."""
from copy import deepcopy

import pytest

from scripts.benchmark_drama_workers import choose, measured_rates, preserved


def trial(workers, files, volume=1, media=100, healthy=True):
    return {'workers': workers, 'elapsed_seconds': 600, 'healthy': healthy,
            'rates': {'uploaded_files': files, 'files_per_minute': files / 10,
                      'MB_per_second': volume, 'media_seconds': media}}


def test_four_needs_a_clear_gain_in_verified_files_without_volume_loss():
    three = trial(3, 50)
    assert choose(three, trial(4, 60))[0] == 4
    assert choose(three, trial(4, 52))[0] == 3
    assert choose(three, trial(4, 60, volume=.8))[0] == 3
    assert choose(three, trial(4, 60, media=80))[0] == 3


def test_health_and_actual_uploads_take_precedence_over_nominal_speed():
    assert choose(trial(3, 50), trial(4, 60, healthy=False))[0] == 3
    assert choose(trial(3, 50, healthy=False), trial(4, 60))[0] == 4
    assert choose(trial(3, 50, healthy=False), trial(4, 60, healthy=False))[0] == 2
    assert choose(trial(3, 0), trial(4, 0))[0] == 2


def test_count_only_new_uploaded_files_and_separate_aliases():
    old = {'video_id': 'old', 'status': 'uploaded', 'drive_file_id': 'fixed', 'sha256': 'oldsha'}
    before = {'items': {'old': old}}
    after = deepcopy(before)
    after['items'].update({
        'new': {'video_id': 'new', 'status': 'uploaded', 'file_size': 600, 'duration': 20},
        'alias': {'video_id': 'alias', 'status': 'aliased', 'file_size': 600},
        'active': {'video_id': 'active', 'status': 'uploading', 'file_size': 900}})
    rates = measured_rates(before, after, 60)
    assert rates['uploaded_files'] == 1 and rates['aliased_sources'] == 1
    assert rates['completed_sources'] == 2 and rates['bytes'] == 600
    assert rates['new_video_ids'] == ['new'] and rates['new_alias_ids'] == ['alias']
    assert rates['media_seconds'] == 20
    with pytest.raises(ValueError):
        measured_rates(before, after, 0)


@pytest.mark.parametrize('field,value', [('status', 'failed'), ('drive_file_id', 'different'),
                                        ('sha256', 'changed'), ('canonical_drive_file_id', 'changed')])
def test_changed_completed_identity_aborts(field, value):
    before = {'items': {'v': {'video_id': 'v', 'status': 'aliased',
                             'canonical_drive_file_id': 'fixed', 'sha256': 'sha'}}}
    after = deepcopy(before)
    after['items']['v'][field] = value
    with pytest.raises(ValueError, match='identity changed'):
        preserved(before, after)
