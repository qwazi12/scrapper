"""Owner-approved research -> selected treatments -> inspected immutable exports."""
from __future__ import annotations

from datetime import datetime, timezone
from pathlib import Path
import os
from typing import Literal
import uuid

from fastapi import APIRouter, Depends, File, Form, HTTPException, UploadFile
from ..file_response import FileResponse
from PIL import Image, UnidentifiedImageError
from pydantic import Field, model_validator
from sqlalchemy import update
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from .. import control
from ..auth import require_token
from ..config import settings
from ..db import get_session, SessionLocal
from ..models import CreativeCampaign, CreativeExport, MediaAsset, QueueItem
from ..social import enqueue
from ..studio.media_quality import file_hash
from .routes import Strict, digest, opportunity

router = APIRouter(prefix="/api/editorial", dependencies=[Depends(require_token)])
UTC = timezone.utc
NATIVE = Literal["short", "reel", "carousel", "image", "community", "poll"]


class Beat(Strict):
    role: Literal["hook", "body", "payoff"]
    text: str = Field(min_length=12, max_length=140)
    source_refs: list[int] = Field(min_length=1, max_length=10)
    asset_id: int | None = Field(default=None, gt=0)


class CampaignIn(Strict):
    opportunity_id: int
    title: str = Field(min_length=3, max_length=72)
    formats: list[NATIVE] = Field(min_length=1, max_length=6)
    beats: list[Beat] = Field(min_length=3, max_length=8)
    short_caption: str = Field(default="", max_length=2000)
    reel_caption: str = Field(default="", max_length=2000)
    carousel_caption: str = Field(default="", max_length=2000)
    image_text: str = Field(default="", max_length=140)
    image_caption: str = Field(default="", max_length=2000)
    community_text: str = Field(default="", max_length=4000)
    poll_question: str = Field(default="", max_length=255)
    poll_options: list[str] = Field(default_factory=list,max_length=4)
    community_question: str = Field(default="", max_length=1000)

    @model_validator(mode="after")
    def structure(self):
        if self.beats[0].role != "hook" or self.beats[-1].role != "payoff" or any(b.role != "body" for b in self.beats[1:-1]):
            raise ValueError("Use a hook, body beats and a final payoff")
        if len({b.text.casefold().strip() for b in self.beats}) != len(self.beats): raise ValueError("Repeated beat text needs an original treatment")
        if len(set(self.formats)) != len(self.formats): raise ValueError("Choose each treatment once")
        for fmt,field in [('short','short_caption'),('reel','reel_caption'),('carousel','carousel_caption'),('image','image_caption'),('community','community_text'),('poll','poll_question')]:
            if fmt in self.formats and len(getattr(self,field).strip())<10:
                raise ValueError(f'Write a distinct {field} for the selected format')
        if 'image' in self.formats and len(self.image_text.strip())<12:raise ValueError('Write the single-image treatment')
        if 'poll' in self.formats:
            self.poll_options=[text.strip() for text in self.poll_options]
            if not 2<=len(self.poll_options)<=4 or any(not 2<=len(text)<=80 for text in self.poll_options):
                raise ValueError('A poll needs two to four options, each 2–80 characters')
            if len({text.casefold() for text in self.poll_options})!=len(self.poll_options):raise ValueError('Poll options must be distinct')
        return self


def approved_research(s, oid):
    row = opportunity(s, oid)
    data = {k: v for k, v in row.data.items() if k not in ("status", "approval")}
    fingerprint = digest(data)
    if row.data.get("status") != "approved" or row.data.get("approval", {}).get("fingerprint") != fingerprint:
        raise HTTPException(409, "Review and approve the current research brief first")
    return row, fingerprint


def get_campaign(s, cid):
    row = s.get(CreativeCampaign, cid)
    if not row: raise HTTPException(404, "Campaign not found")
    return row


def inputs(s, campaign):
    research, fingerprint = approved_research(s, campaign.opportunity_id)
    if campaign.brief["research_fingerprint"] != fingerprint:
        raise HTTPException(409, "Research changed since this campaign was drafted; create a new treatment from the reviewed brief")
    assets = {}
    for beat in campaign.brief["beats"]:
        if not all(type(ref) is int and 1 <= ref <= len(research.data["evidence"]) for ref in beat["source_refs"]):
            raise HTTPException(409, "Every beat needs a valid research excerpt reference")
        aid = beat.get("asset_id")
        if aid:
            asset = s.get(MediaAsset, aid)
            path = Path(asset.file_path).resolve() if asset else None
            if not asset or not path.is_relative_to(settings.data_path.resolve() / "editorial" / "assets") or not path.is_file() or file_hash(path) != asset.sha256:
                raise HTTPException(409, "A selected asset is missing or changed; reimport and inspect it")
            if not asset.provenance.get("attestation"): raise HTTPException(409, "Asset rights attestation missing")
            assets[str(aid)] = {"sha256": asset.sha256, "provenance": asset.provenance}
    return {"brief": campaign.brief, "research_fingerprint": fingerprint, "assets": assets}


def export_out(row):
    return {"id": row.id, "campaign_id": row.campaign_id, "format": row.format, "manifest": row.manifest,
            "approval": row.approval, "queue_item_id": row.queue_item_id, "created_at": row.created_at}


def campaign_out(s, row):
    return {"id": row.id, "opportunity_id": row.opportunity_id, "brief": row.brief,
            "stage_status": row.stage_status, "stage_message": row.stage_message,
            "exports": [export_out(x) for x in s.query(CreativeExport).filter(CreativeExport.campaign_id == row.id)
                        .order_by(CreativeExport.created_at.desc()).all()]}


@router.get("/campaigns")
def list_campaigns(s: Session = Depends(get_session)):
    return [campaign_out(s, c) for c in s.query(CreativeCampaign).order_by(CreativeCampaign.id.desc()).all()]


@router.post("/campaigns")
def create_campaign(req: CampaignIn, s: Session = Depends(get_session)):
    research, fingerprint = approved_research(s, req.opportunity_id)
    if not set(req.formats).issubset(research.data["formats"]): raise HTTPException(409, "Choose formats selected in the opportunity brief")
    row = CreativeCampaign(opportunity_id=req.opportunity_id, brief={**req.model_dump(mode="json"), "research_fingerprint": fingerprint})
    inputs(s, row)
    s.add(row); s.commit(); s.refresh(row)
    return campaign_out(s, row)


@router.post("/campaigns/{cid}/render/{fmt}")
def render_campaign(cid: int, fmt: NATIVE, s: Session = Depends(get_session)):
    from ..worker_leases import scope, LeaseLost
    try:
        with scope(f"native:{cid}"):
            from . import native
            row = get_campaign(s, cid)
            if fmt not in row.brief["formats"]: raise HTTPException(409, "This format is not selected for this campaign")
            frozen = inputs(s, row)
            token = uuid.uuid4().hex
            claimed = s.execute(update(CreativeCampaign).where(CreativeCampaign.id == cid).values(stage_status="running", work_token=token,
                stage_message=f"Rendering {fmt}; each export is kept as a separate version"), execution_options={"synchronize_session": False}).rowcount
            s.commit()
            if not claimed: raise HTTPException(409, "Campaign is already rendering; wait for the result")
            export_id = uuid.uuid4().hex
            root = settings.data_path / "editorial" / "campaigns" / str(cid) / export_id
            try:
                with control.job("native", f"Campaign #{cid}: {fmt}", scope="editorial", ref=cid):
                    paths = {int(aid): Path(s.get(MediaAsset, int(aid)).file_path) for aid in frozen["assets"]}
                    manifest = native.render(root, row.brief, fmt, paths)
                # Inputs are checked again after long-running render, before promotion.
                s.expire_all(); row = get_campaign(s, cid)
                if row.work_token != token or digest(inputs(s, row)) != digest(frozen): raise HTTPException(409, "Campaign inputs changed while rendering; this version was not promoted")
                manifest.update(input_sha256=digest(frozen), inputs=frozen)
                item = CreativeExport(id=export_id, campaign_id=cid, format=fmt, manifest=manifest)
                s.add(item)
                row.stage_status, row.stage_message, row.work_token = "ready", f"{fmt} rendered; inspect every card, captions and sound before approval", None
                s.commit()
                return export_out(item)
            except BaseException as exc:
                s.rollback()
                s.execute(update(CreativeCampaign).where(CreativeCampaign.id == cid, CreativeCampaign.work_token == token)
                          .values(stage_status="error", stage_message=str(exc)[:500], work_token=None))
                s.commit()
                if isinstance(exc, HTTPException): raise
                if isinstance(exc, Exception): raise HTTPException(409, f"Native render stopped: {str(exc)[:300]}") from exc
                raise
    except LeaseLost as exc:
        raise HTTPException(409, str(exc))


def exact_export(s, eid):
    row = s.get(CreativeExport, eid)
    if not row: raise HTTPException(404, "Export not found")
    campaign = get_campaign(s, row.campaign_id)
    if digest(inputs(s, campaign)) != row.manifest["input_sha256"]: raise HTTPException(409, "Export inputs changed; render and inspect a new version")
    quality = row.manifest.get("quality", {})
    if quality.get("errors") or quality.get("decode") != "passed": raise HTTPException(409, "Export quality checks did not pass")
    root = (settings.data_path / "editorial" / "campaigns" / str(campaign.id) / row.id).resolve()
    files = [*row.manifest["cards"], row.manifest["cover"],*row.manifest.get("manual_assets",[])]
    if row.manifest.get("video"): files.append(row.manifest["video"])
    for item in files:
        path = Path(item["file"]).resolve()
        if not path.is_relative_to(root) or not path.is_file() or file_hash(path) != item["sha256"]:
            raise HTTPException(409, "Export file missing or changed; render a new version")
    return row, digest({"input": row.manifest["input_sha256"], "files": [x["sha256"] for x in files],"platform_copy":row.manifest.get("platform_copy"),"delivery":row.manifest.get("delivery")})


class ApprovalIn(Strict):
    reviewed: Literal[True]


@router.post("/exports/{eid}/approve")
def approve_export(eid: str, req: ApprovalIn, s: Session = Depends(get_session)):
    row, fingerprint = exact_export(s, eid)
    row.approval = {"fingerprint": fingerprint, "at": datetime.now(UTC).isoformat(),
                    "scope": "owner inspected research, rights, full media, captions and platform copy"}
    s.commit()
    return export_out(row)


class EnqueueIn(Strict):
    accounts: list[str] = Field(min_length=1, max_length=10)


@router.post("/exports/{eid}/enqueue")
def enqueue_export(eid: str, req: EnqueueIn, s: Session = Depends(get_session)):
    row, fingerprint = exact_export(s, eid)
    if row.format in ('community','poll'):raise HTTPException(409,'Manual export only: this treatment cannot create queue items or publish through this integration')
    if (row.approval or {}).get("fingerprint") != fingerprint: raise HTTPException(409, "Inspect and approve this exact export first")
    from ..social.queue_manager import blocked_accounts
    if blocked_accounts(req.accounts): raise HTTPException(400, "This destination is not permitted for Screen Central")
    if row.format in ("carousel","image") and any(a.split(":")[-1] not in ("instagram", "facebook") for a in req.accounts):
        raise HTTPException(400, "Choose explicit Instagram/Facebook destinations for this image treatment")
    campaign = get_campaign(s, row.campaign_id)
    caption = campaign.brief[{"carousel":"carousel_caption","image":"image_caption","reel":"reel_caption"}.get(row.format,"short_caption")]
    candidate = QueueItem(pipeline="Native Studio", source=f"Editorial campaign #{campaign.id}", title=campaign.brief["title"],
        description=caption, video_name=f"Campaign {campaign.id} {row.format} {eid}",
        video_path=row.manifest.get("video", {}).get("file"), thumb_path=row.manifest["cover"]["file"],
        media_kind="images" if row.format in ("carousel","image") else "video",
        media_files=[card["file"] for card in row.manifest["cards"]] if row.format in ("carousel","image") else [],
        accounts=req.accounts, status="review", research={"native_export_id": eid, "approval_fingerprint": fingerprint})
    item, created = enqueue.claim(s, row, candidate)
    s.commit()
    return {"queue_item_id": item.id, "created": created, "status": item.status}


@router.get("/exports/{eid}/file/{name}")
def export_file(eid: str, name: str, s: Session = Depends(get_session)):
    row = s.get(CreativeExport, eid)
    if not row: raise HTTPException(404, "Export not found")
    files = [*row.manifest.get("cards", []), row.manifest.get("cover", {}), row.manifest.get("video", {})]
    match = next((x.get("file") for x in files if x.get("file") and Path(x["file"]).name == name), None)
    root = (settings.data_path / "editorial" / "campaigns" / str(row.campaign_id) / eid).resolve()
    if not match or not Path(match).resolve().is_relative_to(root) or not Path(match).is_file(): raise HTTPException(404, "Media file missing")
    return FileResponse(match)


@router.get('/exports/{eid}/manual/{name}')
def manual_export_file(eid:str,name:str,s:Session=Depends(get_session)):
    row,fingerprint=exact_export(s,eid)
    if row.format not in ('community','poll') or (row.approval or {}).get('fingerprint')!=fingerprint:
        raise HTTPException(409,'Inspect and approve the exact manual export before downloading it')
    match=next((item for item in row.manifest.get('manual_assets',[]) if Path(item['file']).name==name),None)
    if not match:raise HTTPException(404,'Manual export file not found')
    return FileResponse(match['file'],filename=name,media_type=match['content_type'])


@router.get("/assets")
def assets(s: Session = Depends(get_session)):
    return [{"id": a.id, "sha256": a.sha256, "kind": a.kind, "tags": a.tags, "provenance": a.provenance}
            for a in s.query(MediaAsset).order_by(MediaAsset.id.desc()).all()]


@router.post("/assets")
async def import_asset(file: UploadFile = File(...), license: Literal["owner_created", "licensed"] = Form(...),
                       attestation: str = Form(min_length=20, max_length=2000), tags: str = Form(default=""),
                       s: Session = Depends(get_session)):
    root = settings.data_path / "editorial" / "assets"
    root.mkdir(parents=True, exist_ok=True)
    source = root / f".{uuid.uuid4().hex}.upload"
    target = root / f"{uuid.uuid4().hex}.png"
    try:
        size = 0
        with source.open("xb") as stream:
            while chunk := await file.read(1024 * 1024):
                size += len(chunk)
                if size > 20 * 1024 * 1024: raise HTTPException(413, "Stills must be at most 20 MiB")
                stream.write(chunk)
            stream.flush(); os.fsync(stream.fileno())
        with Image.open(source) as image:
            if image.width * image.height > 20_000_000: raise HTTPException(413, "Still exceeds 20 megapixels")
            image.convert("RGB").save(target, "PNG")  # strip EXIF and external metadata
        sha = file_hash(target)
        existing = s.query(MediaAsset).filter(MediaAsset.sha256 == sha).first()
        if existing: target.unlink(); return {"id": existing.id, "duplicate": True}
        row = MediaAsset(sha256=sha, file_path=str(target), kind="image", tags=[x.strip()[:80] for x in tags.split(",") if x.strip()][:30],
                         provenance={"license": license, "attestation": attestation, "recorded_at": datetime.now(UTC).isoformat(),
                                     "scope": "owner attested; legal clearance not independently verified"})
        s.add(row)
        try: s.commit()
        except IntegrityError:
            s.rollback(); target.unlink(missing_ok=True)
            return {"id": s.query(MediaAsset).filter(MediaAsset.sha256 == sha).one().id, "duplicate": True}
        return {"id": row.id, "duplicate": False}
    except (UnidentifiedImageError, OSError, Image.DecompressionBombError) as exc:
        target.unlink(missing_ok=True)
        raise HTTPException(400, "Upload a valid bounded image") from exc
    finally:
        source.unlink(missing_ok=True)
