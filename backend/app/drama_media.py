"""Explicit local artifacts: canonical originals are never replaced or relabelled.

Only fixed server recipes run. Technical similarity is not perceptual approval,
copyright clearance, publication authority or delivered-platform fidelity.
"""
from __future__ import annotations

from array import array
import hashlib
import json
import math
from pathlib import Path
import re
import shutil

from . import control, drama_controls as dc, pending_metadata as pm, publication_journal as journal
from .config import settings
from .models import DramaRecord
from .studio import media
from .studio.media_quality import file_hash
from .uploads import validate_video

RECIPE='h264-aaclc-preserve-timeline-v1'
MAX_BYTES=512*1024*1024


def path(operation_id):
    if not re.fullmatch(pm.OPERATION_PATTERN,operation_id):dc.fail('invalid server artifact identifier')
    root=settings.data_path/'drama-artifacts'
    parent=root/operation_id
    if root.is_symlink() or parent.is_symlink():dc.fail('unsafe artifact directory')
    return parent/'artifact.mp4'


def tool_versions():
    return {tool:media.run([tool,'-version'],timeout=10).stdout.splitlines()[0]
            for tool in ('ffmpeg','ffprobe')}


def number(value):
    try:result=float(value)
    except (ValueError,TypeError):dc.fail('missing media timing evidence')
    if not math.isfinite(result):dc.fail('nonfinite media timing evidence')
    return result


def inspect(path):
    path=Path(path)
    if path.is_symlink() or not path.is_file() or not 0<path.stat().st_size<=MAX_BYTES:
        dc.fail('artifact missing, unsafe or outside the bounded size')
    probe=validate_video(path)  # Actual complete decode, not caller-supplied booleans.
    streams=probe.get('streams',[])
    video=[x for x in streams if x.get('codec_type')=='video']
    audio=[x for x in streams if x.get('codec_type')=='audio']
    if len(video)!=1 or len(audio)!=1 or len(streams)!=2:
        dc.fail('exactly one video and one known audio stream are required')
    v,a=video[0],audio[0]
    duration=number(probe['format'].get('duration'))
    if not 0<duration<=140 or v.get('width',0)>1920 or v.get('height',0)>1920:
        dc.fail('media exceeds this conservative software/provider-route baseline')
    if (not a.get('profile') or a.get('channels') not in (1,2)
            or not str(a.get('sample_rate','')).isdigit() or not 8000<=int(a['sample_rate'])<=96000):
        dc.fail('missing/unknown audio profile or channel evidence')
    frames=json.loads(media.run(['ffprobe','-v','error','-threads','2','-select_streams','v:0','-show_frames',
        '-show_entries','frame=best_effort_timestamp_time','-of','json',str(path)],timeout=120).stdout)['frames']
    pts=[number(frame.get('best_effort_timestamp_time')) for frame in frames]
    if not pts or len(pts)>8400 or any(b<=a for a,b in zip(pts,pts[1:])):
        dc.fail('missing, excessive or nonmonotonic decoded video timeline')
    summary={'format':probe['format'].get('format_name',''),'video_codec':v.get('codec_name'),
        'video_profile':v.get('profile'),'pixel_format':v.get('pix_fmt'),'width':v.get('width'),'height':v.get('height'),
        'audio_codec':a.get('codec_name'),'audio_profile':a.get('profile'),'audio_channels':a.get('channels'),
        'audio_sample_rate':a.get('sample_rate'),'video_start':number(v.get('start_time')),
        'audio_start':number(a.get('start_time')),'video_duration':number(v.get('duration')),
        'audio_duration':number(a.get('duration')),'video_time_base':v.get('time_base'),
        'frame_count':len(pts),'video_pts_sha256':pm.digest(pts),
        **{key:v.get(key) for key in ('color_range','color_space','color_transfer','color_primaries')}}
    if v.get('side_data_list') or v.get('tags',{}).get('rotate'):
        dc.fail('unreviewed video rotation/side-data transformation is unsupported')
    return {'sha256':file_hash(path),'file_size':path.stat().st_size,'duration':duration,
            'media':summary,'video_pts':pts,'decode':'passed'}


def compatible(proof):
    p=proof.get('media') or {}
    return (p.get('video_codec')=='h264' and p.get('audio_codec')=='aac'
            and p.get('audio_profile')=='LC' and p.get('audio_channels') in (1,2)
            and p.get('pixel_format')=='yuv420p' and 'mp4' in p.get('format',''))


def pcm(path,channels):
    result=control.run(['ffmpeg','-v','error','-nostdin','-threads','2','-i',str(path),
        '-map','0:a:0','-vn','-ac',str(channels),'-ar','16000','-f','f32le','-'],timeout=120,text=False)
    if result.returncode or len(result.stdout)>140*16000*channels*4+16000:
        dc.fail('bounded full audio comparison failed')
    samples=array('f');samples.frombytes(result.stdout)
    if not samples or any(not math.isfinite(x) for x in samples):dc.fail('unknown decoded audio content')
    return samples


def compare(original,artifact,source,candidate):
    a,b=source['media'],candidate['media']
    if not compatible(candidate):dc.fail('artifact requires MP4/H264/yuv420p and AAC Low Complexity, mono/stereo')
    for key in ('width','height','frame_count','audio_channels','color_range','color_space','color_transfer','color_primaries'):
        if a[key]!=b[key]:dc.fail('derivative changed source geometry, frame count or audio channels')
    if len(source['video_pts'])!=len(candidate['video_pts']) or any(
            abs(x-y)>0.000002 for x,y in zip(source['video_pts'],candidate['video_pts'])):
        dc.fail('derivative changed the complete original video PTS timeline')
    for key,tolerance in (('video_start',0.001),('audio_start',0.001),('video_duration',0.002),('audio_duration',0.03)):
        if abs(a[key]-b[key])>tolerance:dc.fail('derivative changed original stream duration/offset')
    if abs(source['duration']-candidate['duration'])>0.03:dc.fail('derivative changed full container duration')
    channels=a['audio_channels'];x,y=pcm(original,channels),pcm(artifact,channels)
    if abs(len(x)-len(y))>480*channels:dc.fail('derivative omitted or added audio samples beyond codec padding tolerance')
    scores=[]
    for channel in range(channels):
        sx,sy=x[channel::channels],y[channel::channels]
        n=min(len(sx),len(sy));sx=sx[:n];sy=sy[:n]
        source_energy=math.fsum(v*v for v in sx);target_energy=math.fsum(v*v for v in sy)
        energy=source_energy*target_energy
        if energy<=1e-12:dc.fail('zero-energy/unknown audio cannot certify content preservation')
        cosine=math.fsum(i*j for i,j in zip(sx,sy))/math.sqrt(energy)
        if cosine<0.995:dc.fail('derivative changed audio content in at least one channel')
        level_ratio=math.sqrt(target_energy/source_energy)
        error_ratio=math.sqrt(math.fsum((i-j)**2 for i,j in zip(sx,sy))/source_energy)
        if not 0.98<=level_ratio<=1.02 or error_ratio>0.08:
            dc.fail('derivative changed per-channel audio level or decoded signal beyond codec tolerance')
        scores.append({'cosine':cosine,'rms_level_ratio':level_ratio,'normalized_error_rms':error_ratio})
    result=media.run(['ffmpeg','-hide_banner','-nostdin','-threads','2','-i',str(original),'-threads','2','-i',str(artifact),
        '-filter_complex_threads','1','-lavfi','[0:v]setpts=PTS-STARTPTS[a];[1:v]setpts=PTS-STARTPTS[b];[a][b]ssim',
        '-an','-f','null','-'],timeout=180)
    found=re.findall(r'All:([0-9.]+)',result.stderr)
    if not found or number(found[-1])<0.98:dc.fail('derivative full-video content similarity is unknown or below the review floor')
    return {'complete_video_pts_equal_within_seconds':0.000002,'ssim':number(found[-1]),
        'audio_per_channel':scores,'audio_comparison':'full decoded same-origin 16k PCM, each channel independently; content and level/error bounds',
        'not_perceptual_review_or_rights_or_delivered_fidelity':True}


def recipe_arguments(source):
    base=source['media']['video_time_base']
    match=re.fullmatch(r'1/([0-9]{1,9})',base or '')
    if not match:dc.fail('source video timebase unsupported for exact timeline preservation')
    # Values are server literals or measured timebase, never arbitrary client flags.
    return ['ffmpeg','-hide_banner','-nostdin','-n','-copyts','-threads','2','-i','{original}',
        '-map','0:v:0','-map','0:a:0','-c:v','libx264','-profile:v','high','-preset','medium','-crf','18',
        '-pix_fmt','yuv420p','-fps_mode:v','passthrough','-enc_time_base:v','1:'+match[1],
        '-threads:v','2','-x264-params','open-gop=0',
        '-video_track_timescale',match[1],'-c:a','aac','-profile:a','aac_low','-b:a','128k',
        '-ar','48000','-ac',str(source['media']['audio_channels']),'-movflags','+faststart',
        '-fs',str(MAX_BYTES),'{artifact}']
def encode(original,artifact,source):
    args=recipe_arguments(source)
    recipe={'id':RECIPE,'arguments':args,'tools':tool_versions()}
    media.run([str(original) if x=='{original}' else str(artifact) if x=='{artifact}' else x for x in args],timeout=180)
    return recipe


def create_derivative(original,operation_id,evidence,identity,copy_hash,account_selection,fingerprint):
    destination=path(operation_id)
    if destination.parent.exists():dc.fail('uncommitted artifact exists; inspect it instead of overwriting/re-encoding')
    if shutil.disk_usage(settings.data_path).free<MAX_BYTES*2:dc.fail('insufficient bounded artifact disk reserve')
    destination.parent.mkdir(parents=True)
    try:
        source=inspect(original)
        if source['media']['pixel_format']!='yuv420p':dc.fail('unreviewed bit-depth/chroma conversion is outside this fixed derivative recipe')
        if source['sha256']!=evidence['sha256'] or source['file_size']!=evidence['file_size']:
            dc.fail('downloaded original differs from immutable canonical evidence')
        recipe=encode(original,destination,source)
        artifact=inspect(destination)
        similarity=compare(original,destination,source,artifact)
        if file_hash(original)!=source['sha256']:dc.fail('original changed during derivative generation')
        return {'recipe':recipe,'recipe_sha256':pm.digest(recipe),'original':source,'artifact':artifact,
            'canonical_evidence':evidence,'queue_identity':identity,'copy_hash':copy_hash,
            'account_selection':account_selection,'prepared_queue_fingerprint':fingerprint,
            'similarity':similarity,'rights_status':'not certified','content_review':'required separately',
            'source_overwritten':False,'publication_authorized':False}
    except BaseException:
        destination.unlink(missing_ok=True);destination.parent.rmdir();raise


def revoked(s,operation_id):
    return bool(s.query(DramaRecord.id).filter(DramaRecord.kind=='derivative_revoke',
        DramaRecord.data['derivative_id'].as_string()==operation_id).first())


def selected(s,item,choice=None):
    if choice is None:
        review=dc.latest(s,item,'review')
        choice=review.data['document']['review'].get('derivative') if review else None
    if not choice:return None
    row=s.query(DramaRecord).filter_by(operation_id=choice['derivative_id'],kind='media_derivative').first()
    if not row or revoked(s,row.operation_id) or pm.digest(row.data['document'])!=choice['document_sha256']:
        dc.fail('exact reviewed derivative is missing, revoked or changed')
    doc=row.data['document'];evidence=dc.binding(s,item)
    if (doc['recipe'].get('id')!=RECIPE or pm.digest(doc['recipe'])!=doc['recipe_sha256']
            or doc['recipe']['arguments']!=recipe_arguments(doc['original'])):
        dc.fail('unknown or changed deterministic derivative recipe; source rollback cannot adopt it')
    selection_matches=doc['account_selection']==list(item.accounts)
    if not selection_matches and dc.reserved_before(s,journal.identity(item)):
        # Only a journal-proven remaining subset of the original reviewed pair
        # can reuse this artifact after a partial failure. Manual retargeting
        # never qualifies, and a successful destination is never resent.
        review=dc.latest(s,item,'review')
        selection_matches=bool(review and review.data['document']['account_selection']==doc['account_selection']
            and set(item.accounts)==set(dc.dispatch_targets(s,item)))
    if (row.queue_identity!=journal.identity(item)
            or not evidence or doc['canonical_evidence']!=evidence.data or doc['copy_hash']!=dc.copy_hash(item)
            or not selection_matches):
        dc.fail('derivative no longer matches original canonical source, exact copy or target selection')
    artifact=path(row.operation_id)
    if artifact.is_symlink() or not artifact.is_file() or file_hash(artifact)!=doc['artifact']['sha256']:
        dc.fail('reviewed derivative bytes changed or disappeared')
    return {'derivative_id':row.operation_id,'document_sha256':pm.digest(doc),'recipe_sha256':doc['recipe_sha256'],
            'original_sha256':doc['original']['sha256'],'artifact_sha256':doc['artifact']['sha256'],
            'artifact':doc['artifact'],'path':artifact}


def binding(value):
    return {key:value[key] for key in ('derivative_id','document_sha256','recipe_sha256','original_sha256','artifact_sha256')} if value else None


def original_observation(s,item,evidence):
    row=dc.latest(s,item,'inspect')
    observed=row.data.get('live_media_observation') if row else None
    if observed and all(observed.get(k)==evidence.data.get(k) for k in ('sha256','file_size','drive_file_id','catalog_hash')):
        return observed
    return evidence.data  # Missing legacy profile stays unknown, never assumed LC.


def reviewed_proof(s,item,evidence):
    review=dc.latest(s,item,'review')
    derivative=selected(s,item)
    if derivative:return derivative['artifact']
    observed=review.data['document'].get('transport_media') if review else None
    if observed and observed.get('sha256')==evidence.data['sha256']:
        return observed
    return evidence.data
