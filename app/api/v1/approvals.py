from fastapi import APIRouter, Depends
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.api.v1.campaigns import campaign_out
from app.api.v1.scripts import script_out
from app.core.db import get_db
from app.core.rbac import Access, Screen, require
from app.models import Campaign, Script
from app.models.enums import ApprovalStatus, CampaignStatus
from app.schemas.approvals import ApprovalsOut, DiffRow, ScriptApproval
from app.schemas.campaigns import CAMPAIGN_CHECKS
from app.schemas.scripts import SCRIPT_CHECKS
from app.services.scripts import active_script, side_by_side

router = APIRouter(prefix="/approvals", tags=["approvals"])


@router.get("", response_model=ApprovalsOut)
def pending_approvals(access: Access = Depends(require(Screen.approvals)), db: Session = Depends(get_db)) -> ApprovalsOut:
    """Everything awaiting a checker; scripts carry a line diff against the script the bot runs today."""
    scripts = []
    for s in db.scalars(select(Script).where(Script.status == ApprovalStatus.pending).order_by(Script.submitted_at)):
        base = active_script(db, s.mode)
        scripts.append(ScriptApproval(
            script=script_out(db, s), base=script_out(db, base) if base else None,
            diff=[DiffRow(**r) for r in side_by_side(base.system_prompt if base else "", s.system_prompt)]))
    campaigns = [campaign_out(db, c) for c in db.scalars(
        select(Campaign).where(Campaign.status == CampaignStatus.pending).order_by(Campaign.submitted_at))]
    return ApprovalsOut(scripts=scripts, campaigns=campaigns,
                        script_checks=list(SCRIPT_CHECKS), campaign_checks=list(CAMPAIGN_CHECKS))
