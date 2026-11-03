"""Independent native media, not upload echoes; never a live post or analytics."""
import asyncio
import json
import subprocess
from pathlib import Path

import httpx
import pytest

from backend.app import drama_readback as rb
from backend.app.config import settings
from backend.app.social import upload_post


@pytest.fixture(scope='module')
def video(tmp_path_factory):
    path=tmp_path_factory.mktemp('published-native-readback')/'original.mp4'
    subprocess.run(['ffmpeg','-v','error','-y','-f','lavfi','-i','color=c=red:s=540x960:r=24:d=3',
        '-f','lavfi','-i','sine=f=700:d=3','-c:v','libx264','-pix_fmt','yuv420p','-c:a','aac',
        '-ar','48000','-ac','2','-b:a','128k','-shortest',str(path)],check=True)
    return path.read_bytes()


def fake_http(monkeypatch, handler):
    real=httpx.AsyncClient
    monkeypatch.setattr(settings,'uploadpost_api_key','synthetic-readback-only')
    monkeypatch.setattr(httpx,'AsyncClient',lambda **kw:real(transport=httpx.MockTransport(handler),**kw))


def test_independent_facebook_caption_and_actual_served_video_are_bound_but_not_overcertified(monkeypatch,video):
    calls=[]
    roots=[];real_directory=rb.tempfile.TemporaryDirectory
    def directory(**kwargs):
        context=real_directory(**kwargs);roots.append(Path(context.name));return context
    monkeypatch.setattr(rb.tempfile,'TemporaryDirectory',directory)
    def handler(request):
        calls.append(str(request.url.copy_with(query=None)))
        if request.url.host=='api.upload-post.com':
            assert request.method=='GET' and request.url.path=='/api/uploadposts/media'
            assert request.url.params['platform']=='facebook' and request.url.params['limit']=='25'
            return httpx.Response(200,json={'success':True,'media':[{'id':'synthetic-native-id',
                'permalink':'https://www.facebook.com/page/videos/123','caption':'Observed caption #Public',
                'media_type':'VIDEO','media_url':'https://video.xx.fbcdn.net/synthetic.mp4?temporary=not-persisted'}]})
        assert request.url.host=='video.xx.fbcdn.net'
        assert 'authorization' not in request.headers  # Never forward provider credentials to CDN.
        return httpx.Response(200,content=video,headers={'content-type':'video/mp4'})
    fake_http(monkeypatch,handler)
    source={'sha256':'a'*64,'duration':3.0}
    proof=asyncio.run(rb.inspect_destination('facebook','https://www.facebook.com/page/videos/123',
        {'facebook_description':'Observed caption #Public','facebook_title':'Separate title'},source))
    assert proof['native_post_id']=='synthetic-native-id'
    assert proof['caption_matches_reviewed_field'] and proof['duration_matches_source']
    assert proof['published_media']['decode']=='passed' and proof['published_media']['audio_observed']['codec']=='aac'
    assert proof['fidelity_verified'] is False and proof['facebook_title_observed'] is None
    assert 'temporary=not-persisted' not in json.dumps(proof)
    assert len(calls)==2 and roots and all(not root.exists() for root in roots)


def test_x_caption_is_native_but_preview_never_used_as_video_proof(monkeypatch):
    calls=[]
    def handler(request):
        calls.append(request.url.path)
        assert request.url.host=='api.upload-post.com' and request.method=='GET'
        return httpx.Response(200,json={'success':True,'media':[{'id':'123456',
            'permalink':'https://x.com/synthetic/status/123456','caption':'Full observed title\n\nCaption #Public',
            'media_type':'VIDEO','media_url':'https://pbs.twimg.com/video-preview.jpg'}]})
    fake_http(monkeypatch,handler)
    proof=asyncio.run(rb.inspect_destination('x','https://twitter.com/synthetic/status/123456',
        {'x_title':'Full observed title\n\nCaption #Public'},{'sha256':'b'*64,'duration':211.63}))
    assert proof['caption_matches_reviewed_field'] and proof['native_post_id']=='123456'
    assert proof['media_url_kind']=='video_preview_image_only'
    assert proof['duration_observed'] is None and proof['audio_observed'] is None and not proof['fidelity_verified']
    assert calls==['/api/uploadposts/media']


@pytest.mark.parametrize('damage',['not_found','ambiguous','missing_caption','different_caption'])
def test_uncertain_or_changed_native_readback_never_certifies_fidelity(monkeypatch,damage):
    row={'id':'123','permalink':'https://x.com/synthetic/status/123','caption':'Approved','media_type':'VIDEO'}
    rows=[row]
    if damage=='not_found': rows=[]
    elif damage=='ambiguous': rows=[row,row]
    elif damage=='missing_caption': row.pop('caption')
    elif damage=='different_caption': row['caption']='Different native text'
    fake_http(monkeypatch,lambda req:httpx.Response(200,json={'success':True,'media':rows}))
    result=asyncio.run(rb.inspect_destination('x','https://x.com/synthetic/status/123',{'x_title':'Approved'},
        {'sha256':'c'*64,'duration':3}))
    assert result['fidelity_verified'] is False
    if damage=='different_caption': assert result['caption_matches_reviewed_field'] is False
    else: assert result['status']=='unknown'


@pytest.mark.parametrize('url',['http://video.xx.fbcdn.net/v.mp4','https://127.0.0.1/v.mp4',
    'https://video.xx.fbcdn.net.attacker.invalid/v.mp4','https://user:password@video.xx.fbcdn.net/v.mp4'])
def test_untrusted_media_urls_do_not_trigger_fetch(monkeypatch,url):
    fake_http(monkeypatch,lambda req:pytest.fail('Unsupported URL must not be fetched'))
    assert asyncio.run(rb.facebook_media(url))['status']=='unknown'


@pytest.mark.parametrize('response',[httpx.Response(302,headers={'location':'https://127.0.0.1/'}),
    httpx.Response(200,headers={'content-length':str(rb.MAX_BYTES+1)},content=b'x'),
    httpx.Response(200,content=b'not media')])
def test_redirect_oversized_or_invalid_media_stays_unknown(monkeypatch,response):
    calls=[]
    def handler(req):calls.append(req);return response
    fake_http(monkeypatch,handler)
    assert asyncio.run(rb.facebook_media('https://video.xx.fbcdn.net/v.mp4'))['status']=='unknown'
    assert len(calls)==1


def test_provider_unavailability_is_not_replaced_with_submitted_metadata(monkeypatch):
    fake_http(monkeypatch,lambda req:httpx.Response(403,json={'error':'not available'}))
    with pytest.raises(upload_post.UploadPostError): asyncio.run(upload_post.published_media('Dramarama','x'))


@pytest.mark.parametrize('body',[{'success':True,'media':[],'pagination':False},
    {'success':True,'media':[None]},{'success':False,'media':[]},None])
def test_malformed_native_responses_are_controlled_unknowns(monkeypatch,body):
    fake_http(monkeypatch,lambda req:httpx.Response(200,json=body))
    with pytest.raises(upload_post.UploadPostError): asyncio.run(upload_post.published_media('Dramarama','facebook'))
