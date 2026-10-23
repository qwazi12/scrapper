"""Interrupted format fragments cannot masquerade as a completed media file."""
import json
import threading
from pathlib import Path

import pytest
from scripts import archive_worker as worker
from backend.app import channel_sources as source

CID='UC'+'a'*22
VID='a'*11


def files(directory):
    directory.mkdir()
    (directory/(VID+'.info.json')).write_text(json.dumps({'id':VID,'channel_id':CID}))
    audio=directory/(VID+'.f251-15.webm');audio.write_bytes(b'completed audio fragment')
    partial=directory/(VID+'.f788.mp4.part');partial.write_bytes(b'partial video bytes')
    return audio,partial


def test_worker_resumes_source_instead_of_uploading_single_audio_fragment(tmp_path,monkeypatch):
    directory=tmp_path/'media';audio,partial=files(directory);calls=[]
    def finish(cid,vid,dest,bound,**kwargs):
        calls.append((cid,vid,dest))
        assert audio.read_bytes()==b'completed audio fragment'
        assert partial.read_bytes()==b'partial video bytes'
        final=dest/(vid+'.mp4');final.write_bytes(b'completed merged boundary fixture')
        return final,{'id':vid,'channel_id':cid}
    monkeypatch.setattr(source,'download',finish)
    result=worker.download(CID,VID,directory,1000,threading.Event())
    assert result[0].name==VID+'.mp4' and len(calls)==1
    # The exact completed file is now reusable even with retained fragments.
    assert worker.download(CID,VID,directory,1000,threading.Event())[0]==result[0]
    assert len(calls)==1


def test_source_requires_exact_completed_basename_and_tolerates_leftover_fragments(tmp_path,monkeypatch):
    directory=tmp_path/'media';audio,partial=files(directory)
    monkeypatch.setattr(source,'_execute',lambda *a,**k:None)
    with pytest.raises(RuntimeError,match='Download missing'):
        source.download(CID,VID,directory,1000)
    final=directory/(VID+'.mp4');final.write_bytes(b'completed output boundary fixture')
    assert source.download(CID,VID,directory,1000)[0]==final
    assert audio.is_file() and partial.is_file()
