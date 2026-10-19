"""Local receipt review. These endpoints never call a billing/provider API."""
from datetime import datetime, timezone
from decimal import Decimal
from uuid import UUID, uuid4
import hashlib
import json
from typing import Literal

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, Field
from sqlalchemy.orm import Session

from .auth import require_token
from .db import get_session
from .models import BudgetReservation, CreditReservation, FinancialResolution, WorkerLease

router=APIRouter(prefix='/api/financial',dependencies=[Depends(require_token)])


def version(row):
    return hashlib.sha256(json.dumps([row.id,row.state,getattr(row,'actual_micros',None),
        getattr(row,'reserved_micros',getattr(row,'credits',None))]).encode()).hexdigest()


def ownership(s,kind,row):
    from .worker_leases import TTL
    key=('budget:' if kind=='usd' else 'credit:')+row.id
    lease=s.get(WorkerLease,key,populate_existing=True)
    from .worker_leases import clock
    now=clock(s)
    expiry=lease.expires_at.replace(tzinfo=timezone.utc) if lease and not lease.expires_at.tzinfo else lease.expires_at if lease else None
    live=bool(expiry and expiry>now)
    created=row.created_at.replace(tzinfo=timezone.utc) if not row.created_at.tzinfo else row.created_at
    orphan=row.state in ('reserved','dispatched')
    eligible=not live and (not orphan or bool(lease) or (now-created).total_seconds()>TTL)
    return live,orphan,eligible


def require_resolution(s,kind,row,confirm_interrupted=False,expected_version=None):
    if not row or row.state not in ('reserved','dispatched','unknown','breach'):
        raise ValueError('Reservation is not awaiting reconciliation or was already resolved')
    if expected_version and expected_version!=version(row):raise ValueError('Hold changed; reload before reconciling')
    live,orphan,eligible=ownership(s,kind,row)
    if live or not eligible:raise ValueError('A request worker may still be running; wait for its lease to expire')
    if orphan and not confirm_interrupted:raise ValueError('Explicitly confirm the interrupted worker and review provider evidence')


def save_decision(s,kind,rid,amount,receipt,rationale,key=None):
    receipt,rationale=receipt.strip(),rationale.strip()
    if not 5<=len(receipt)<=2048 or not 20<=len(rationale)<=4000:
        raise ValueError('Provide a provider receipt reference and a substantive reconciliation rationale')
    s.add(FinancialResolution(id=key or str(uuid4()),kind=kind,reservation_id=rid,
        amount=amount,receipt=receipt,rationale=rationale))


def duplicate(s,key,kind,rid,amount,receipt,rationale):
    if not key:return False
    prior=s.get(FinancialResolution,key)
    if not prior:return False
    if (prior.kind,prior.reservation_id,prior.amount,prior.receipt,prior.rationale)!=(kind,rid,amount,receipt.strip(),rationale.strip()):
        raise ValueError('Receipt decision key already records different evidence')
    return True


def resolve_credit(rid,amount,receipt,rationale,**kwargs):
    from . import vidiq
    from .budget_reservations import lock
    from .db import SessionLocal
    number=Decimal(str(amount))
    if not number.is_finite() or number<0 or number>1000000 or number!=number.to_integral_value():
        raise ValueError('Actual credits must be a nonnegative whole number')
    actual=int(number)
    with SessionLocal() as s:
        lock(s)
        if duplicate(s,kwargs.get('request_key'),'credit',rid,actual,receipt,rationale):return
        row=s.get(CreditReservation,rid)
        require_resolution(s,'credit',row,kwargs.get('confirm_interrupted',False),kwargs.get('expected_version'))
        save_decision(s,'credit',rid,actual,receipt,rationale,kwargs.get('request_key'))
        vidiq._update_ledger(s,actual)
        row.state='settled';s.commit()


@router.get('/holds')
def holds(s:Session=Depends(get_session)):
    rows=[]
    for kind,model in [('usd',BudgetReservation),('credit',CreditReservation)]:
        for row in s.query(model).filter(model.state.in_(('reserved','dispatched','unknown','breach'))).order_by(model.created_at):
            live,orphan,eligible=ownership(s,kind,row)
            rows.append(dict(id=row.id,kind=kind,state=row.state,service=getattr(row,'service',getattr(row,'tool','')),
                ref=getattr(row,'ref',None),reserved=row.reserved_micros/1e6 if kind=='usd' else row.credits,
                actual=row.actual_micros/1e6 if kind=='usd' and row.actual_micros is not None else None,
                version=version(row),worker_live=live,orphan=orphan,can_resolve=eligible,created_at=row.created_at))
    return {'holds':rows,'history':[dict(id=r.id,kind=r.kind,reservation_id=r.reservation_id,amount=r.amount,
        receipt=r.receipt,rationale=r.rationale,at=r.created_at)
        for r in s.query(FinancialResolution).order_by(FinancialResolution.created_at.desc()).limit(100)]}


class ResolutionIn(BaseModel):
    request_key: UUID
    kind: Literal['usd','credit']
    reservation_id: UUID
    expected_version: str=Field(min_length=64,max_length=64)
    actual: float=Field(ge=0,le=1000000,allow_inf_nan=False)
    receipt: str=Field(min_length=5,max_length=2048)
    rationale: str=Field(min_length=20,max_length=4000)
    reviewed: Literal[True]
    confirm_interrupted: bool=False


@router.post('/resolve')
def resolve(req:ResolutionIn):
    from .budget_reservations import resolve as resolve_usd
    fn=resolve_usd if req.kind=='usd' else resolve_credit
    try:
        fn(str(req.reservation_id),req.actual,req.receipt,req.rationale,request_key=str(req.request_key),
            expected_version=req.expected_version,confirm_interrupted=req.confirm_interrupted)
    except ValueError as exc:raise HTTPException(409,str(exc))
    return {'resolved':str(req.reservation_id)}
