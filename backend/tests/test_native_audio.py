"""Actual authorized background audio, relative levels and explicit endings."""
from array import array
import hashlib
import io
import math
from pathlib import Path
import wave

from fastapi.testclient import TestClient
from PIL import Image
import pytest

from backend.app.db import SessionLocal
from backend.app.editorial import native
from backend.app.file_use import FileBusy,pin,suspend
from backend.app.main import app
from backend.app.models import CreativeCampaign,CreativeExport,QueueItem
from backend.app.studio import media
from backend.tests.test_asset_catalog import upload
from backend.tests.test_native_campaigns import brief,research,acceptance_request,approve_native


@pytest.fixture
def client():return TestClient(app,headers={'x-access-token':'test-token'})


@pytest.fixture
def selected(client,tmp_path):
    path=tmp_path/'original-background.wav'
    phase=int(hashlib.sha256(str(tmp_path).encode()).hexdigest()[:12],16)/16**12*2*math.pi
    media.run(['ffmpeg','-v','error','-y','-f','lavfi','-i',f'aevalsrc=0.1*sin(2*PI*1100*t+{phase:.12f}):s=48000:d=3',str(path)])
    imported=upload(client,path,'audio')
    assert imported.status_code==200,imported.text
    oid=research(client);client.post(f'/api/editorial/opportunities/{oid}/approve')
    body={**brief(oid),'formats':['short']}
    body['beats'][0].update(background_audio_id=imported.json()['id'],audio_start=.25,audio_end=1.25,audio_end_policy='silence',audio_gain_db=-18)
    return body,imported.json()


@pytest.fixture
def tone(monkeypatch):
    def synth(text,path):media.run(['ffmpeg','-v','error','-y','-f','lavfi','-i','sine=f=750:d=2',str(path)])
    monkeypatch.setattr(native.tts,'synth',synth)


def create(client,body):
    result=client.post('/api/editorial/campaigns',json=body)
    assert result.status_code==200,result.text
    return result.json()['id']


def test_audio_needs_every_explicit_choice_current_duration_and_correct_asset_kind(client,selected):
    body,asset=selected
    for changes in [{'background_audio_id':True},{'audio_start':True},{'audio_end':None},{'audio_gain_db':None},
                    {'audio_gain_db':-11},{'audio_gain_db':-40},{'audio_gain_db':'-18'},{'audio_gain_db':False},
                    {'audio_end_policy':'hold'},{'audio_end_policy':None},{'audio_end':.3},{'audio_end':20}]:
        candidate={**body,'beats':[{**body['beats'][0],**changes},*body['beats'][1:]]}
        assert client.post('/api/editorial/campaigns',json=candidate).status_code==422
    candidate={**body,'beats':[{**body['beats'][0],'audio_end':4},*body['beats'][1:]]}
    assert client.post('/api/editorial/campaigns',json=candidate).status_code==409
    assert client.post('/api/editorial/campaigns',json={**body,'formats':['short','carousel']}).status_code==409
    image=io.BytesIO();Image.new('RGB',(40,40),'#42a180').save(image,format='PNG')
    still=client.post('/api/editorial/assets',data={'license':'owner_created','attestation':'Original synthetic still created for this private asset-kind test.'},files={'file':('original.png',image.getvalue(),'image/png')}).json()['id']
    for changes in [{'background_audio_id':still},{'asset_id':asset['id']},
                    {'asset_id':still,'clip_start':0,'clip_end':1,'clip_end_policy':'hold'}]:
        candidate={**body,'beats':[{**body['beats'][0],**changes},*body['beats'][1:]]}
        assert client.post('/api/editorial/campaigns',json=candidate).status_code==409


def energies(path,at,destination,frequencies=(750,1100)):
    media.run(['ffmpeg','-v','error','-y','-ss',str(at),'-i',str(path),'-t','0.3','-vn',
        '-ar','16000','-ac','1','-c:a','pcm_s16le',str(destination)])
    with wave.open(str(destination)) as stream:samples=array('h',stream.readframes(stream.getnframes()))
    def power(frequency):
        angle=2*math.pi*frequency/16000
        return sum(value*math.cos(angle*i) for i,value in enumerate(samples))**2+sum(value*math.sin(angle*i) for i,value in enumerate(samples))**2
    return tuple(power(frequency) for frequency in frequencies)


@pytest.mark.parametrize('policy',['silence','loop'])
def test_actual_background_mix_keeps_narration_dominant_and_obeys_selected_ending(client,selected,tone,tmp_path,policy):
    body,asset=selected;body['beats'][0]['audio_end_policy']=policy
    cid=create(client,body)
    response=client.post(f'/api/editorial/campaigns/{cid}/render/short')
    assert response.status_code==200,response.text
    export=response.json();m=export['manifest'];source=m['audio_sources'][0]
    assert m['quality']['decode']=='passed' and not m['quality']['errors']
    assert source['asset_id']==asset['id'] and source['source_sha256']==asset['sha256']
    assert source['source_start']==.25 and source['source_end']==1.25 and source['gain_db']==-18
    assert source['normalization_reference_lufs']==-18 and source['end_policy']==policy
    assert .9<=source['excerpt_seconds']<=1.1 and source['presentation_seconds']>2.5
    early_voice,early_bed=energies(m['video']['file'],.4,tmp_path/'early.wav')
    late_voice,late_bed=energies(m['video']['file'],2.1,tmp_path/'late.wav')
    plain_voice,_=energies(m['video']['file'],m['timeline'][1]['start']+.5,tmp_path/'next-beat.wav')
    assert .5<plain_voice/late_voice<2  # Same original voice stays within3dB across a beat without background.
    assert early_voice>early_bed*15 and early_bed>early_voice/10000
    if policy=='loop':assert late_voice>late_bed*15 and late_bed>late_voice/10000
    else:assert late_bed*100<early_bed
    review=client.get(f"/api/editorial/exports/{export['id']}/export-review").json()
    findings=[f for f in review['findings'] if f['kind']=='background_audio']
    assert len(findings)==1 and '-18.0dB' in findings[0]['message']
    decision=acceptance_request(client,export['id']);decision['warning_decisions'].pop(findings[0]['id'])
    assert client.post(f"/api/editorial/exports/{export['id']}/approve",json=decision).status_code==409
    approve_native(client,export['id'])
    queued=client.post(f"/api/editorial/exports/{export['id']}/enqueue",json={'accounts':['default:instagram']})
    assert queued.status_code==200,queued.text
    with SessionLocal() as s:
        row=s.get(QueueItem,queued.json()['queue_item_id']);assert row.status=='review' and not row.publish_requests
    Path(source['file']).write_bytes(b'changed retained background excerpt')
    assert client.post(f"/api/editorial/exports/{export['id']}/enqueue",json={'accounts':['default:instagram']}).status_code==409


def test_audio_source_is_pinned_and_changed_source_cannot_promote(client,selected,tone,monkeypatch):
    body,asset=selected;cid=create(client,body);original=native.render;seen=[]
    def changed(root,brief,fmt,paths):
        source=paths[asset['id']]
        with suspend(),pytest.raises(FileBusy):
            with pin([source],exclusive=True):pytest.fail('Background source was not protected')
        seen.append(source);result=original(root,brief,fmt,paths)
        source.write_bytes(b'out-of-band background source change');return result
    monkeypatch.setattr(native,'render',changed)
    result=client.post(f'/api/editorial/campaigns/{cid}/render/short')
    assert result.status_code==409,result.text
    assert len(seen)==1
    with SessionLocal() as s:
        assert not s.query(CreativeExport).filter_by(campaign_id=cid).count()
        assert s.get(CreativeCampaign,cid).stage_status=='error'


def test_combined_video_and_background_mix_excludes_the_video_soundtrack(client,selected,tone,tmp_path):
    body,background=selected
    video=tmp_path/'original-visual-and-sound.mp4'
    media.run(['ffmpeg','-v','error','-y','-f','lavfi','-i','testsrc2=s=320x180:r=30:d=2',
        '-f','lavfi','-i','sine=f=1900:d=2','-c:v','libx264','-pix_fmt','yuv420p','-c:a','aac',str(video)])
    imported=upload(client,video,'video')
    assert imported.status_code==200,imported.text
    body['beats'][0].update(asset_id=imported.json()['id'],clip_start=.25,clip_end=1.25,clip_end_policy='loop',audio_end_policy='loop')
    cid=create(client,body)
    result=client.post(f'/api/editorial/campaigns/{cid}/render/short')
    assert result.status_code==200,result.text
    m=result.json()['manifest']
    assert m['quality']['decode']=='passed' and not m['quality']['errors']
    assert len(m['audio_sources'])==len(m['visual_sources'])==1
    assert m['visual_sources'][0]['source_audio']=='excluded'
    voice,bed,excluded=energies(m['video']['file'],1.4,tmp_path/'combined.wav',(750,1100,1900))
    assert voice>15*bed and bed>voice/10000 and voice>1000*excluded
