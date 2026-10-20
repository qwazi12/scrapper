"""Local auth routing with synthetic cookie markers; never read real profiles."""
from pathlib import Path
from types import SimpleNamespace
import json
import subprocess
import threading

import pytest

from backend.app import channel_sources as source
from scripts import archive_worker as worker


@pytest.fixture
def profile(tmp_path,monkeypatch):
    monkeypatch.setattr(source.sys,'platform','darwin')
    monkeypatch.delenv('RAILWAY_ENVIRONMENT',raising=False)
    monkeypatch.setattr(Path,'home',classmethod(lambda cls:tmp_path))
    selected=tmp_path/'Library/Application Support/BraveSoftware/Brave-Browser/Default'
    selected.mkdir(parents=True)
    return selected


def test_explicit_profile_is_only_used_when_requested(profile,monkeypatch):
    from backend.core import scraper
    monkeypatch.setattr(scraper,'js_runtime_args',lambda:[])
    commands=[]
    def execute(command,**kwargs):
        commands.append(command)
        return subprocess.CompletedProcess(command,0,'result','')
    monkeypatch.setattr(source.control,'run',execute)
    assert source._execute(['--skip-download','https://www.youtube.com/watch?v=aaaaaaaaaaa'])=='result'
    assert source._execute(['--skip-download','https://www.youtube.com/watch?v=aaaaaaaaaaa'],browser_profile=profile)=='result'
    assert '--cookies-from-browser' not in commands[0]
    index=commands[1].index('--cookies-from-browser')
    assert commands[1][index+1]=='brave:'+str(profile)
    assert '--cookies' not in commands[1]  # no persistent cookie-jar export


@pytest.mark.parametrize('server', [False,True])
def test_session_cannot_be_loaded_on_server_or_nonmac(profile,monkeypatch,server):
    if server:monkeypatch.setenv('RAILWAY_ENVIRONMENT','production')
    else:monkeypatch.setattr(source.sys,'platform','linux')
    with pytest.raises(ValueError,match='local Mac'):source.browser_cookie_args(profile)


def test_unrelated_profile_paths_are_not_accepted(profile,tmp_path):
    with pytest.raises(ValueError,match='selected by its owner'):source.browser_cookie_args(tmp_path)


def cookie(name,domain='.youtube.com',expired=False):
    return SimpleNamespace(name=name,domain=domain,value='synthetic-value',is_expired=lambda:expired)


def test_signin_requires_current_youtube_markers_and_never_returns_values(profile,monkeypatch):
    from yt_dlp import cookies
    jar=[cookie('LOGIN_INFO'),cookie('SAPISID'),cookie('UNRELATED','other.example')]
    monkeypatch.setattr(cookies,'extract_cookies_from_browser',lambda *args,**kwargs:jar)
    status=worker.browser_session_status(profile)
    assert status=={'browser':'brave','authenticated_youtube_session':True,'auth_cookie_count':2}
    assert 'synthetic-value' not in str(status)
    jar[:]=[cookie('LOGIN_INFO',expired=True),cookie('SAPISID')]
    with pytest.raises(RuntimeError,match='no active YouTube sign-in'):worker.browser_session_status(profile)
    jar[:]=[cookie('LOGIN_INFO','other.example'),cookie('SAPISID','other.example')]
    with pytest.raises(RuntimeError,match='no active YouTube sign-in'):worker.browser_session_status(profile)


def test_authenticated_failure_stays_restricted_and_redacts_profile(profile,monkeypatch):
    from backend.core import scraper
    monkeypatch.setattr(scraper,'js_runtime_args',lambda:[])
    monkeypatch.setattr(source.control,'run',lambda command,**kwargs:
        subprocess.CompletedProcess(command,1,'',f'{profile}: Sign in to confirm you are not a bot'))
    with pytest.raises(source.AccessRestricted) as error:source._execute([],browser_profile=profile)
    assert str(profile) not in str(error.value)
    assert '[local Brave profile]' in str(error.value)


def test_download_keeps_only_private_minimal_receipt_and_still_resumes(profile,tmp_path,monkeypatch):
    cid='UC'+'a'*22;vid='a'*11;destination=tmp_path/'media'
    def extract(*args,**kwargs):
        (destination/(vid+'.info.json')).write_text(json.dumps({
            'id':vid,'channel_id':cid,'duration':1,'upload_date':'20261019',
            'formats':[{'url':'https://example.test/signed-temporary-url'}],
            'http_headers':{'Cookie':'synthetic-sensitive-header'}}))
        (destination/(vid+'.mp4')).write_bytes(b'synthetic-boundary-only')
    monkeypatch.setattr(source,'_execute',extract)
    video,info=source.download(cid,vid,destination,1000,browser_profile=profile)
    receipt=destination/(vid+'.info.json')
    assert len(receipt.read_bytes())<1000 and 'signed-temporary-url' not in receipt.read_text()
    assert 'synthetic-sensitive-header' not in receipt.read_text()
    assert receipt.stat().st_mode&0o777==0o600 and destination.stat().st_mode&0o777==0o700
    monkeypatch.setattr(source,'download',lambda *args,**kwargs:pytest.fail('Completed local media must be reused'))
    assert worker.download(cid,vid,destination,1000,threading.Event(),browser_profile=profile)==(video,info)
