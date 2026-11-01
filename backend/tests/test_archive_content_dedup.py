import importlib.util, pathlib, threading, time
from unittest.mock import MagicMock
import pytest
spec=importlib.util.spec_from_file_location('guard_archive', pathlib.Path(__file__).resolve().parents[2]/'scripts/archive_memory.py')
a=importlib.util.module_from_spec(spec);spec.loader.exec_module(a)

def row(vid,sha='a',status='uploaded',**kw):
 return dict(video_id=vid,sha256=sha,md5='md5'+sha,file_size=10,full_decode_passed=True,verified_at='now',remote_sha256_verified=True,drive_file_id='file'+vid,drive_parent_id='folder',status=status,**kw)
def job(rows):
 x=a.Archive.__new__(a.Archive);x.state={'items':{r['video_id']:r for r in rows}};x.lock=threading.RLock();x.reserved_sources=set();x.content_locks={};x.platform='tiktok';x.channel='account';return x

def test_cross_series_exact_duplicate_not_title():
 c=row('c',series_id='one',series_title='A');r=row('r',status='validated',series_id='two',series_title='B');x=job([c,r]);assert x.exact_canonical(r)==c
 r['sha256']='different';assert x.exact_canonical(r) is None
 r.update(sha256='a',file_size=11);assert x.exact_canonical(r) is None
 r.update(file_size=10,full_decode_passed=False);assert x.exact_canonical(r) is None

def test_partial_series_never_propagates_alias_from_episode_one():
 c=row('c',episode_number=1);r=row('r',status='validated',episode_number=1);other={'video_id':'unknown','status':'discovered','episode_number':2};x=job([c,r,other]);assert x.exact_canonical(r);assert x.exact_canonical(other) is None

def test_inflight_receipt_not_canonical():
 c=row('c',status='uploading');r=row('r',status='validated');x=job([c,r]);assert x.exact_canonical(r) is None
 c['status']='uploaded';c['remote_sha256_verified']=False;assert x.exact_canonical(r) is None

def test_atomic_same_source_reservation_and_release_on_interrupt():
 x=job([]);entered=threading.Event();release=threading.Event();calls=[]
 def process(v):calls.append(v);entered.set();release.wait(2)
 x._process=process;t=threading.Thread(target=x.process,args=('v',));t.start();assert entered.wait(1);x.process('v');release.set();t.join();assert calls==['v'];assert not x.reserved_sources
 x._process=MagicMock(side_effect=RuntimeError('interrupt'))
 with pytest.raises(RuntimeError):x.process('v')
 assert not x.reserved_sources

def test_alias_requires_accessible_exact_canonical(monkeypatch):
 c=row('c');r=row('r',status='validated');x=job([c,r]);x.patch=MagicMock();meta={'id':'filec','parents':['folder'],'appProperties':x.props('c'),'size':'10','md5Checksum':c['md5'],'sha256Checksum':'a'};monkeypatch.setattr(a,'remote',lambda *_:meta)
 x.alias_to(None,'r',c);assert x.patch.call_args.kwargs['status']=='aliased'
 meta['sha256Checksum']='wrong'
 with pytest.raises(ValueError):x.alias_to(None,'r',c)
 meta['sha256Checksum']='a';meta['trashed']=True
 with pytest.raises(ValueError):x.alias_to(None,'r',c)

def test_resume_alias_provenance_mismatch_blocks(monkeypatch):
 c=row('c');r=row('r',status='aliased',canonical_video_id='c',canonical_drive_file_id='wrong');x=job([c,r])
 with pytest.raises(ValueError,match='provenance'):x.verify_aliases(None)

def test_completed_alias_counts_do_not_inflate_storage():
 x=job([row('c'),row('r',status='aliased'),row('pending',sha='b',status='interrupted')]);counts=x.counts();assert counts['uploaded']==1 and counts['aliased']==1 and counts['remaining']==1 and counts['uploaded_bytes']==10 and counts['completed_sources']==2

def runnable(rows):
 x=job(rows);x.stop=threading.Event();x.owner_stop=threading.Event();x.args=type('Args',(),{'max_video_bytes':100,'ytdlp_python':'python'})();x.journal=False;x.format='b'
 def patch(vid,**values):x.state['items'][vid].update(values)
 x.patch=patch;x.save=MagicMock();x.manifest=MagicMock();x.media_folder=MagicMock(return_value='folder');x.metadata=MagicMock();return x

def test_known_alias_skips_download_and_folder_creation(monkeypatch):
 c=row('c');r=row('r',status='validated');r.pop('drive_file_id');x=runnable([c,r]);service=MagicMock();monkeypatch.setattr(a,'get_drive_service',lambda:service);monkeypatch.setattr(a,'remote',lambda *_:{'parents':['folder'],'appProperties':x.props('c'),'size':'10','md5Checksum':'md5a','sha256Checksum':'a'})
 x.process('r');assert r['status']=='aliased';x.metadata.assert_not_called();x.media_folder.assert_not_called()

def test_interrupted_upload_reconciles_same_id_before_download(monkeypatch):
 r=row('r',status='interrupted');x=runnable([r]);monkeypatch.setattr(a,'get_drive_service',MagicMock());monkeypatch.setattr(a,'remote',lambda *_:{'parents':['folder'],'appProperties':x.props('r'),'size':'10','md5Checksum':'md5a','sha256Checksum':'a'})
 x.process('r');assert r['status']=='uploaded';x.metadata.assert_not_called()

def test_unknown_hash_downloads_then_reuses_exact_content(monkeypatch):
 import io
 c=row('c');r={'video_id':'r','title':'different','status':'discovered','original_url':'url'};x=runnable([c,r]);service=MagicMock();service.files().list().execute.return_value={'files':[]};monkeypatch.setattr(a,'get_drive_service',lambda:service);x.metadata.return_value={'title':'title','duration':1,'format_id':'b'}
 calls=[];monkeypatch.setattr(a,'collect_pipe',lambda *args,**kw:(calls.append(1) or io.BytesIO(b'data')));monkeypatch.setattr(a,'validate_buffer',lambda *_:{k:c[k] for k in ['sha256','md5','file_size','full_decode_passed']});monkeypatch.setattr(a,'remote',lambda *_:{'parents':['folder'],'appProperties':x.props('c'),'size':'10','md5Checksum':'md5a','sha256Checksum':'a'})
 x.process('r');assert calls==[1] and r['status']=='aliased';service.files().create.assert_not_called();service.files().generateIds.assert_not_called()

def test_concurrent_cross_id_exact_buffers_upload_only_once(monkeypatch):
 import io
 rows=[{'video_id':v,'title':v,'status':'discovered','original_url':'url'} for v in ['one','two']];x=runnable(rows);service=MagicMock();service.files().list().execute.return_value={'files':[]};service.files().generateIds().execute.return_value={'ids':['reserved']};monkeypatch.setattr(a,'get_drive_service',lambda:service);x.metadata=MagicMock(side_effect=lambda r:{'title':r['title'],'duration':1,'format_id':'b'})
 barrier=threading.Barrier(2)
 def collect(*args,**kw):barrier.wait(2);return io.BytesIO(b'data')
 monkeypatch.setattr(a,'collect_pipe',collect);monkeypatch.setattr(a,'validate_buffer',lambda *_:dict(sha256='a',md5='md5a',file_size=10,full_decode_passed=True))
 def remote(_,fid):
  if fid=='reserved' and not any(r['status']=='uploaded' for r in rows):
   from googleapiclient.errors import HttpError
   import httplib2
   raise HttpError(httplib2.Response({'status':'404'}),b'not found')
  c=next(r for r in rows if r['status']=='uploaded');return {'parents':['folder'],'appProperties':x.props(c['video_id']),'size':'10','md5Checksum':'md5a','sha256Checksum':'a'}
 monkeypatch.setattr(a,'remote',remote);uploads=[]
 def upload(*args):uploads.append(args[1]);time.sleep(.03);return {'sha256Checksum':'a'}
 monkeypatch.setattr(a,'upload_buffer',upload)
 threads=[threading.Thread(target=x.process,args=(r['video_id'],)) for r in rows]
 for t in threads:t.start()
 for t in threads:t.join(3)
 assert not any(t.is_alive() for t in threads);assert uploads==['reserved'];assert sorted(r['status'] for r in rows)==['aliased','uploaded']

def test_second_process_state_reservation_and_durable_alias_resume(tmp_path):
 import json
 from types import SimpleNamespace
 channel='UC'+'a'*22;vid='a'*11;alias='b'*11
 inv={'platform':'youtube','channel_id':channel,'complete':True,'webpage_url':'url','items':[{'id':vid,'title':'one'},{'id':alias,'title':'two'}]};ip=tmp_path/'inventory.json';ip.write_text(json.dumps(inv));root=tmp_path/'state';args=SimpleNamespace(state_dir=root,inventory=ip,parent_id='parent',folder_name='folder',concurrency=2,max_video_bytes=100)
 x=a.Archive(args)
 with pytest.raises(BlockingIOError):a.Archive(args)
 x.state['items'][alias].update(status='aliased',canonical_video_id=vid,canonical_drive_file_id='canonical',sha256='a');x.save();x.lock_file.close()
 y=a.Archive(args);assert y.state['items'][alias]['status']=='aliased';assert y.state['items'][alias]['canonical_drive_file_id']=='canonical';y.lock_file.close()

def test_empty_interrupted_reservation_aliases_without_redownload_or_resend(monkeypatch):
 c=row('c');r=row('r',status='interrupted');x=runnable([c,r]);monkeypatch.setattr(a,'get_drive_service',MagicMock())
 def remote(_,fid):return {'parents':['folder'],'appProperties':x.props('r' if fid=='filer' else 'c'),'size':'0' if fid=='filer' else '10','md5Checksum':None if fid=='filer' else 'md5a','sha256Checksum':None if fid=='filer' else 'a'}
 monkeypatch.setattr(a,'remote',remote);x.process('r');assert r['status']=='aliased' and r['retained_reserved_drive_file_id']=='filer' and r['drive_file_id'] is None;x.metadata.assert_not_called()

def test_cancelled_waiter_does_not_create_another_upload(monkeypatch):
 import io
 r={'video_id':'r','title':'title','status':'discovered','original_url':'url'};x=runnable([r]);service=MagicMock();service.files().list().execute.return_value={'files':[]};monkeypatch.setattr(a,'get_drive_service',lambda:service);x.metadata.return_value={'title':'title','duration':1,'format_id':'b'};monkeypatch.setattr(a,'collect_pipe',lambda *args,**kw:io.BytesIO(b'data'))
 def validate(*_):x.stop.set();return dict(sha256='a',md5='md5a',file_size=10,full_decode_passed=True)
 monkeypatch.setattr(a,'validate_buffer',validate);x.process('r');service.files().create.assert_not_called();service.files().generateIds.assert_not_called()
