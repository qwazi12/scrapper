"""Generated original scenes/tones only; no episode, native API or paid call."""
from pathlib import Path
import subprocess

import pytest

from backend.app import drama_observed_media as media


@pytest.fixture(scope='module')
def original(tmp_path_factory):
    path=tmp_path_factory.mktemp('normal-observed-original')/'source.mp4'
    subprocess.run(['ffmpeg','-v','error','-f','lavfi','-i','testsrc2=size=320x180:rate=12:duration=8',
        '-f','lavfi','-i','sine=frequency=660:sample_rate=48000:duration=8','-c:v','libx264',
        '-preset','ultrafast','-pix_fmt','yuv420p','-c:a','aac','-ar','48000','-ac','2','-shortest',str(path)],check=True)
    return path


def encode(original,out,flags):
    subprocess.run(['ffmpeg','-v','error','-i',str(original),*flags,'-c:v','libx264',
        '-preset','ultrafast','-pix_fmt','yuv420p','-c:a','aac',str(out)],check=True)


def test_normal_platform_gain_resolution_fps_and_lossy_reencoding_pass_without_byte_or_level_equality(original,tmp_path):
    path=tmp_path/'served.mp4';encode(original,path,['-vf','scale=160:90,fps=24','-af','volume=7dB'])
    result=media.compare(original,path)
    assert result['media_checks_passed'],result
    assert media.passes(result)
    assert result['source']['sha256']!=result['observed']['sha256']
    assert result['observed']['width']==160
    assert all(c['passed'] and c['gain_db_observed']>6 for c in result['audio_channels'])
    assert 'sampled' in result['scope'] and 'rights' in result['scope']


@pytest.mark.parametrize('damage',['wrong_audio','silence','missing_audio_tail','missing_video_tail','truncated','wrong_video'])
def test_wrong_replaced_silent_or_truncated_media_does_not_certify(original,tmp_path,damage):
    path=tmp_path/'bad.mp4'
    if damage=='wrong_audio':
        encode(original,path,['-f','lavfi','-i','sine=frequency=1600:sample_rate=48000:duration=8','-map','0:v:0','-map','1:a:0','-ac','2'])
    elif damage=='wrong_video':
        encode(original,path,['-f','lavfi','-i','color=c=black:s=320x180:r=12:d=8','-map','1:v:0','-map','0:a:0'])
    else:
        flags={'silence':['-af','volume=0'],'missing_audio_tail':['-af','atrim=duration=4,apad=whole_dur=8'],
            'missing_video_tail':['-vf','trim=duration=4,tpad=stop_mode=clone:stop_duration=4'],
            'truncated':['-t','4']}[damage]
        encode(original,path,flags)
    if damage=='silence':
        with pytest.raises(ValueError,match='silent'):media.compare(original,path)
    else:
        result=media.compare(original,path)
        assert not result['media_checks_passed'],result
        assert result['errors']


def test_success_boolean_cannot_replace_measured_coverage_or_policy(original,tmp_path):
    import copy
    path=tmp_path/'served.mp4';encode(original,path,['-af','volume=7dB'])
    result=media.compare(original,path);assert media.passes(result)
    for mutation in ['missing_audio','freeze','short_video_samples','wrong_policy']:
        bad=copy.deepcopy(result);bad['media_checks_passed']=True;bad['errors']=[]
        if mutation=='missing_audio':bad['audio_channels'][0]['missing_active_run_seconds']=4
        if mutation=='freeze':bad['visual']['missing_motion_run_seconds']=4
        if mutation=='short_video_samples':bad['visual']['observed_samples']=1
        if mutation=='wrong_policy':bad['policy_sha256']='0'*64
        assert not media.passes(bad)
