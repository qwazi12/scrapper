"""Actual selected-video output, muted source audio and retained provenance."""
from array import array
import hashlib
import json
import math
from pathlib import Path
import wave

from fastapi.testclient import TestClient
from PIL import Image,ImageChops,ImageStat
import pytest

from backend.app.db import SessionLocal
from backend.app.editorial import native
from backend.app.file_use import FileBusy,pin,suspend
from backend.app.main import app
from backend.app.models import CreativeCampaign,CreativeExport
from backend.app.studio import media
from backend.tests.test_asset_catalog import upload
from backend.tests.test_native_campaigns import brief,research,acceptance_request,approve_native


@pytest.fixture
def client():return TestClient(app,headers={'x-access-token':'test-token'})


@pytest.fixture
def selected(client,tmp_path):
    source=tmp_path/'original-colors.mp4'
    hue=int(hashlib.sha256(str(tmp_path).encode()).hexdigest()[:5],16)%360
    media.run(['ffmpeg','-v','error','-y','-f','lavfi','-i','testsrc2=s=320x180:r=30:d=3',
        '-f','lavfi','-i','sine=f=1900:d=3','-vf',f'hue=h={hue}',
        '-c:v','libx264','-pix_fmt','yuv420p','-c:a','aac',str(source)])
    result=upload(client,source,'video')
    assert result.status_code==200,result.text
    asset=result.json();oid=research(client)
    assert client.post(f'/api/editorial/opportunities/{oid}/approve').status_code==200
    body={**brief(oid),'formats':['short']}
    body['beats'][0].update(asset_id=asset['id'],clip_start=.25,clip_end=1.25,clip_end_policy='hold')
    return body,asset


@pytest.fixture
def tone(monkeypatch):
    def synth(text,path):media.run(['ffmpeg','-v','error','-y','-f','lavfi','-i','sine=f=750:d=2',str(path)])
    monkeypatch.setattr(native.tts,'synth',synth)


def create(client,body):
    result=client.post('/api/editorial/campaigns',json=body)
    assert result.status_code==200,result.text
    return result.json()['id']


def test_video_selection_requires_explicit_bounded_valid_range_and_vertical_formats(client,selected):
    body,asset=selected
    for changes in [{'clip_start':True},{'clip_end':'1.25'},{'clip_end':.3},
                    {'clip_end':.2},{'clip_end':20},{'clip_end':None},{'clip_end_policy':None},
                    {'clip_end_policy':'automatic'},{'asset_id':None}]:
        candidate={**body,'beats':[{**body['beats'][0],**changes},*body['beats'][1:]]}
        assert client.post('/api/editorial/campaigns',json=candidate).status_code==422
    candidate={**body,'beats':[{**body['beats'][0],'clip_end':4},*body['beats'][1:]]}
    assert client.post('/api/editorial/campaigns',json=candidate).status_code==409
    candidate={**body,'beats':[{key:value for key,value in body['beats'][0].items() if not key.startswith('clip_')},*body['beats'][1:]]}
    assert client.post('/api/editorial/campaigns',json=candidate).status_code==409
    assert client.post('/api/editorial/campaigns',json={**body,'formats':['short','carousel']}).status_code==409
    cid=create(client,body)
    with SessionLocal() as s:
        assert s.get(CreativeCampaign,cid).brief['beats'][0]['clip_start']==.25


@pytest.mark.parametrize('policy',['hold','loop'])
def test_real_selected_clip_moves_or_holds_as_chosen_and_source_audio_is_absent(client,selected,tone,tmp_path,policy):
    body,asset=selected;body['beats'][0]['clip_end_policy']=policy
    cid=create(client,body)
    result=client.post(f'/api/editorial/campaigns/{cid}/render/short')
    assert result.status_code==200,result.text
    export=result.json();manifest=export['manifest'];source=manifest['visual_sources'][0]
    assert source['source_start']==.25 and source['source_end']==1.25 and source['source_sha256']==asset['sha256']
    assert source['source_audio']=='excluded' and source['end_policy']==policy
    assert source['presentation_seconds']>source['excerpt_seconds']
    inspected=json.loads(media.run(['ffprobe','-v','error','-show_streams','-of','json',source['file']]).stdout)
    assert [s['codec_type'] for s in inspected['streams']]==['video']
    assert manifest['quality']['decode']=='passed' and not manifest['quality']['errors']
    assert manifest['cards'][0]['visual']=='owner_selected_video_interval'
    frames=[]
    for index,at in enumerate([1.4,2.3]):
        image=tmp_path/f'frame-{index}.png'
        media.run(['ffmpeg','-v','error','-y','-ss',str(at),'-i',manifest['video']['file'],'-frames:v','1',
            '-vf','crop=810:440:90:500,scale=120:64',str(image)])
        with Image.open(image) as opened:frames.append(opened.convert('RGB').copy())
    difference=sum(ImageStat.Stat(ImageChops.difference(*frames)).mean)/3
    assert difference<.2 if policy=='hold' else difference>.25
    # Measure two known frequencies in actual final audio. Narration uses a750Hz
    # synthetic tone; the original source1900Hz must not leak into the mix.
    sound=tmp_path/'final-audio.wav'
    media.run(['ffmpeg','-v','error','-y','-ss','0.6','-i',manifest['video']['file'],'-t','0.5',
        '-vn','-ar','16000','-ac','1','-c:a','pcm_s16le',str(sound)])
    with wave.open(str(sound)) as stream:samples=array('h',stream.readframes(stream.getnframes()))
    def energy(frequency):
        angle=2*math.pi*frequency/16000
        return sum(value*math.cos(angle*i) for i,value in enumerate(samples))**2+sum(value*math.sin(angle*i) for i,value in enumerate(samples))**2
    assert energy(750)>20*energy(1900)
    review=client.get(f"/api/editorial/exports/{export['id']}/export-review").json()
    assert len([f for f in review['findings'] if f['kind']=='clip_end_treatment'])==1
    approve_native(client,export['id'])
    # Retained selected excerpts are checked too, independently of the final mux.
    Path(source['file']).write_bytes(b'changed retained selected excerpt')
    assert client.get(f"/api/editorial/exports/{export['id']}/export-review").status_code==409


def test_native_render_pins_selected_source_until_promotion_and_detects_external_change(client,selected,tone,monkeypatch):
    body,asset=selected;cid=create(client,body);original=native.render;seen=[]
    def changed(root,brief,fmt,paths):
        source=paths[asset['id']]
        with suspend(),pytest.raises(FileBusy):
            with pin([source],exclusive=True):pytest.fail('Render did not protect its source')
        seen.append(source)
        result=original(root,brief,fmt,paths)
        source.write_bytes(b'out-of-band source change during render')
        return result
    monkeypatch.setattr(native,'render',changed)
    result=client.post(f'/api/editorial/campaigns/{cid}/render/short')
    assert result.status_code==409,result.text
    assert len(seen)==1
    with SessionLocal() as s:
        assert not s.query(CreativeExport).filter_by(campaign_id=cid).count()
        assert s.get(CreativeCampaign,cid).stage_status=='error'
