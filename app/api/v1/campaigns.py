import plivo
from fastapi import APIRouter, Depends, HTTPException, Query, Request, status
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.core import audit
from app.core.config import get_settings
from app.core.db import get_db, utcnow
from app.core.rbac import Access, Level, Screen, require
from app.models import Campaign, Script, User
from app.models.enums import AuditAction, CallMode, CampaignStatus, Language, Product
from app.schemas.campaigns import (
    CAMPAIGN_CHECKS, AudienceOut, CampaignIn, CampaignOut, CampaignPatch, LaunchOut, ScriptRef, Segment,
)
from app.schemas.scripts import ReviewIn
from app.services import campaigns as svc
from app.services.scripts import active_script

router = APIRouter(prefix="/campaigns", tags=["campaigns"])
read = require(Screen.campaigns)
write = require(Screen.campaigns, Level.full)
checker = require(Screen.campaigns, Level.approve)


def campaign_out(db: Session, c: Campaign) -> CampaignOut:
    s = db.get(Script, c.script_id)
    return CampaignOut.model_validate(c).model_copy(update=dict(
        script=ScriptRef(id=s.id, name=s.name, version=s.version) if s else None,
        created_by_name=db.get(User, c.created_by).full_name,
        reviewed_by_name=db.get(User, c.reviewed_by).full_name if c.reviewed_by else None))


def _get(db: Session, campaign_id: int) -> Campaign:
    c = db.get(Campaign, campaign_id)
    if not c:
        raise HTTPException(404, "Campaign not found")
    return c


def _draft(db: Session, campaign_id: int) -> Campaign:
    c = _get(db, campaign_id)
    if c.status != CampaignStatus.draft:
        raise HTTPException(409, "Only draft campaigns can be changed")
    return c


def _renewal_script(db: Session) -> Script:
    script = active_script(db, CallMode.renewal)
    if script is None:
        raise HTTPException(409, "No approved renewal script. A script must be approved before campaigns can be built.")
    return script


@router.get("", response_model=list[CampaignOut])
def list_campaigns(access: Access = Depends(read), db: Session = Depends(get_db)) -> list[CampaignOut]:
    return [campaign_out(db, c) for c in db.scalars(select(Campaign).order_by(Campaign.created_at.desc()))]


@router.get("/audience", response_model=AudienceOut)
def preview_audience(product: Product, renewal_within_days: int = Query(30, ge=1, le=90),
                     languages: list[Language] = Query(list(Language)),
                     access: Access = Depends(write), db: Session = Depends(get_db)) -> AudienceOut:
    """Wizard preview: how many policies the segment selects, and the script it will run."""
    seg = Segment(renewal_within_days=renewal_within_days, languages=languages).model_dump(mode="json")
    s = active_script(db, CallMode.renewal)
    return AudienceOut(total_targets=svc.audience_size(db, product, seg),
                       script=ScriptRef(id=s.id, name=s.name, version=s.version) if s else None)


@router.post("", response_model=CampaignOut, status_code=status.HTTP_201_CREATED)
def create_campaign(body: CampaignIn, request: Request, access: Access = Depends(write),
                    db: Session = Depends(get_db)) -> CampaignOut:
    c = Campaign(name=body.name, product=body.product, segment=body.segment.model_dump(mode="json"),
                 script_id=_renewal_script(db).id, status=CampaignStatus.draft, created_by=access.user.id,
                 scheduled_at=body.scheduled_at)
    db.add(c)
    db.flush()
    svc.set_targets(db, c)
    db.commit()
    audit.record(db, request, AuditAction.create, entity_type="campaign", entity_id=c.id,
                 detail={"name": c.name, "product": c.product, "total_targets": c.total_targets})
    return campaign_out(db, c)


@router.patch("/{campaign_id}", response_model=CampaignOut)
def update_campaign(campaign_id: int, body: CampaignPatch, request: Request, access: Access = Depends(write),
                    db: Session = Depends(get_db)) -> CampaignOut:
    c = _draft(db, campaign_id)
    changes = body.model_dump(mode="json", exclude_unset=True, exclude_none=True)
    for k in ("name", "product", "segment"):
        if k in changes:
            setattr(c, k, changes[k])
    if body.scheduled_at:
        c.scheduled_at = body.scheduled_at
    c.script_id = _renewal_script(db).id
    svc.set_targets(db, c)
    db.commit()
    audit.record(db, request, AuditAction.update, entity_type="campaign", entity_id=c.id,
                 detail={"fields": sorted(changes), "total_targets": c.total_targets})
    return campaign_out(db, c)


@router.post("/{campaign_id}/submit", response_model=CampaignOut)
def submit_campaign(campaign_id: int, request: Request, access: Access = Depends(write),
                    db: Session = Depends(get_db)) -> CampaignOut:
    c = _draft(db, campaign_id)
    if c.total_targets == 0:
        raise HTTPException(422, "This segment selects no policies. Widen it before submitting.")
    c.status, c.submitted_at = CampaignStatus.pending, utcnow()
    db.commit()
    audit.record(db, request, AuditAction.submit, entity_type="campaign", entity_id=c.id, detail={"name": c.name})
    return campaign_out(db, c)


@router.post("/{campaign_id}/launch", response_model=LaunchOut)
def launch_campaign(campaign_id: int, request: Request, access: Access = Depends(write),
                    db: Session = Depends(get_db)) -> LaunchOut:
    """Places the next 3 calls: real Plivo calls when Plivo is configured, otherwise simulated (sandbox only).
    Running campaigns can dial the next batch."""
    c = _get(db, campaign_id)
    if c.status not in (CampaignStatus.approved, CampaignStatus.running):
        raise HTTPException(409, "Only approved campaigns can be launched")
    s = get_settings()
    if s.plivo_enabled:
        try:
            call_ids = svc.dial(db, c)
        except plivo.exceptions.AuthenticationError:  # raised before any call is placed
            raise HTTPException(502, "Plivo rejected the credentials: check PLIVO_AUTH_ID (starts with MA or SA) "
                                     "and PLIVO_AUTH_TOKEN") from None
    elif s.app_env == "production":  # never simulate calls on real data
        raise HTTPException(409, "Real dialling is not set up yet: set the PLIVO_* and PUBLIC_BASE_URL settings")
    else:
        call_ids = svc.launch(db, c)
    audit.record(db, request, AuditAction.launch, entity_type="campaign", entity_id=c.id,
                 detail={"name": c.name, "real_calls" if s.plivo_enabled else "simulated_calls": call_ids,
                         "dialled": c.dialled})
    return LaunchOut(campaign=campaign_out(db, c), call_ids=call_ids)


def review(db: Session, request: Request, c: Campaign, user: User, body: ReviewIn, approve: bool) -> Campaign:
    if c.status != CampaignStatus.pending:
        raise HTTPException(409, "Only campaigns awaiting approval can be reviewed")
    if c.created_by == user.id:
        raise HTTPException(403, "You can't review a campaign you created")
    if approve and not all(body.checklist.get(k) for k in CAMPAIGN_CHECKS):
        raise HTTPException(422, "Tick every compliance check before approving")
    if not approve and not (body.note or "").strip():
        raise HTTPException(422, "Add a note explaining what needs to change")
    c.status = CampaignStatus.approved if approve else CampaignStatus.rejected
    c.reviewed_by, c.reviewed_at, c.review_note = user.id, utcnow(), body.note
    c.checklist = {k: bool(body.checklist.get(k)) for k in CAMPAIGN_CHECKS}
    db.commit()
    audit.record(db, request, AuditAction.approve if approve else AuditAction.reject, entity_type="campaign",
                 entity_id=c.id, detail={"name": c.name, "checklist": c.checklist})
    return c


@router.post("/{campaign_id}/approve", response_model=CampaignOut)
def approve_campaign(campaign_id: int, body: ReviewIn, request: Request, access: Access = Depends(checker),
                     db: Session = Depends(get_db)) -> CampaignOut:
    return campaign_out(db, review(db, request, _get(db, campaign_id), access.user, body, approve=True))


@router.post("/{campaign_id}/reject", response_model=CampaignOut)
def reject_campaign(campaign_id: int, body: ReviewIn, request: Request, access: Access = Depends(checker),
                    db: Session = Depends(get_db)) -> CampaignOut:
    return campaign_out(db, review(db, request, _get(db, campaign_id), access.user, body, approve=False))
