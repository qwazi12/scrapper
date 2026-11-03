"""Explicit server-generated derivative preparation; never edits canonical media."""
from typing import Literal

from fastapi import APIRouter, Depends
from .file_response import FileResponse
from pydantic import Field
from sqlalchemy.orm import Session

from . import drama_controls as dc, drama_media as dm, pending_metadata as pm, publication_journal as journal
from .auth import require_token
from .db import get_session
from .drama_routes import ItemOperation, Operation, owned, lock_owned
from .models import DramaRecord, QueueItem

router=APIRouter(prefix='/api/drama',dependencies=[Depends(require_token)])


class Prepare(ItemOperation):
    evidence_id:str=Field(pattern=pm.OPERATION_PATTERN)
    recipe:Literal['h264-aaclc-preserve-timeline-v1']


class Revoke(Operation):
    document_sha256:str=Field(pattern=r'^[a-f0-9]{64}$')
    reason:str=Field(min_length=1,max_length=2000)


@router.post('/items/{qid}/derivatives/prepare')
def prepare(qid:int,req:Prepare,s:Session=Depends(get_session)):
    digest=pm.digest({'kind':'prepare_derivative','qid':qid,'request':req.model_dump(exclude={'operation_id'})})
    if saved:=dc.replay(s,req.operation_id,digest):return saved
    from . import drive_sync,drama_archive
    from .file_use import pin
    with owned(s,[qid]):
        if saved:=dc.replay(s,req.operation_id,digest):s.rollback();return saved
        item=s.get(QueueItem,qid,populate_existing=True)
        dc.require_item(s,item,pristine=True);dc.expected(s,item,req.expected_fingerprint)
        evidence=dc.binding(s,item)
        if not evidence or evidence.id!=req.evidence_id:dc.fail('current immutable canonical evidence is required')
        identity=journal.identity(item);copy_hash=dc.copy_hash(item);accounts=list(item.accounts);proof=dict(evidence.data)
        s.rollback()
        original=None
        try:
            drama_archive.verify_dispatch(proof)
            original=drive_sync.download_drive_file(proof['drive_file_id'])
            with pin([original]):
                doc=dm.create_derivative(original,req.operation_id,proof,identity,copy_hash,accounts,req.expected_fingerprint)
            drama_archive.verify_dispatch(proof)
            lock_owned(s)
            if saved:=dc.replay(s,req.operation_id,digest):s.rollback();return saved
            item=s.get(QueueItem,qid,populate_existing=True)
            dc.require_item(s,item,pristine=True);dc.expected(s,item,req.expected_fingerprint)
            if dc.binding(s,item).data!=proof:dc.fail('canonical evidence changed during explicit preparation')
            response=dc.record(s,req.operation_id,digest,'media_derivative',
                {'derivative_id':req.operation_id,'document_sha256':pm.digest(doc),'document':doc,
                 'artifact_path':f'/api/drama/derivatives/{req.operation_id}/artifact','publication_authorized':False},
                item=item,document=doc)
            s.commit();return response
        finally:
            if original is not None:original.unlink(missing_ok=True)
            # A generated artifact may already belong to a committed receipt
            # even if the commit response was lost. Never unlink it here.
            # Generation cleans only its own known-invalid partial output;
            # uncommitted validated artifacts remain for explicit inspection.


@router.get('/derivatives/{derivative_id}/artifact')
def artifact(derivative_id:str,s:Session=Depends(get_session)):
    row=s.query(DramaRecord).filter_by(operation_id=derivative_id,kind='media_derivative').first()
    if not row or dm.revoked(s,derivative_id):dc.fail('artifact missing or revoked')
    path=dm.path(derivative_id)
    if path.is_symlink() or not path.is_file() or dm.file_hash(path)!=row.data['document']['artifact']['sha256']:
        dc.fail('artifact bytes changed or disappeared')
    return FileResponse(path,media_type='video/mp4',filename='reviewed-derivative.mp4',expected_sha256=row.data['document']['artifact']['sha256'])


@router.post('/derivatives/{derivative_id}/revoke')
def revoke(derivative_id:str,req:Revoke,s:Session=Depends(get_session)):
    digest=pm.digest({'kind':'derivative_revoke','id':derivative_id,'request':req.model_dump(exclude={'operation_id'})})
    if saved:=dc.replay(s,req.operation_id,digest):return saved
    with owned(s,[],plan=True):
        lock_owned(s)
        if saved:=dc.replay(s,req.operation_id,digest):s.rollback();return saved
        row=s.query(DramaRecord).filter_by(operation_id=derivative_id,kind='media_derivative').first()
        if not row or pm.digest(row.data['document'])!=req.document_sha256:dc.fail('exact derivative version is required')
        response=dc.record(s,req.operation_id,digest,'derivative_revoke',{'revoked':derivative_id,'artifact_retained':True},
            derivative_id=derivative_id,reason=req.reason)
        s.commit();return response
