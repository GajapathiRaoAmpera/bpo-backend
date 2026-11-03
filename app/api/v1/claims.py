from fastapi import APIRouter, Depends, HTTPException, Query, Request
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.api.v1.calls import customer_masked
from app.core import audit
from app.core.db import get_db
from app.core.masking import mask_phone
from app.core.rbac import Access, Level, Screen, require
from app.models import Call, Claim, Customer, Policy
from app.models.enums import AuditAction, ClaimStatus
from app.schemas.claims import ClaimRow, ClaimStatusIn

router = APIRouter(prefix="/claims", tags=["claims"])

S = ClaimStatus
NEXT: dict[ClaimStatus, set[ClaimStatus]] = {
    S.registered: {S.under_review}, S.under_review: {S.approved, S.rejected}, S.approved: {S.settled},
    S.rejected: set(), S.settled: set(),
}


def _row(db: Session, cl: Claim, masked: bool) -> ClaimRow:
    c, p = db.get(Customer, cl.customer_id), db.get(Policy, cl.policy_id)
    call = db.get(Call, cl.call_id) if cl.call_id else None
    return ClaimRow(id=cl.id, claim_no=cl.claim_no, policy_no=p.policy_no, plan_name=p.plan_name, customer_id=c.id,
                    customer_name=c.full_name, phone=mask_phone(c.phone) if masked else c.phone, call_id=cl.call_id,
                    call_ref=call.call_ref if call else None, incident_date=cl.incident_date,
                    description=cl.description, status=cl.status, created_at=cl.created_at, updated_at=cl.updated_at)


@router.get("", response_model=list[ClaimRow])
def list_claims(status: ClaimStatus | None = Query(None), access: Access = Depends(require(Screen.claims)),
                db: Session = Depends(get_db)) -> list[ClaimRow]:
    stmt = select(Claim).order_by(Claim.created_at.desc())
    if status:
        stmt = stmt.where(Claim.status == status)
    masked = customer_masked(access.user)
    # shortcut: capped at 100 rows, no pagination; paginate when the queue grows past that.
    return [_row(db, cl, masked) for cl in db.scalars(stmt.limit(100))]


@router.patch("/{claim_id}", response_model=ClaimRow)
def move_claim(claim_id: int, body: ClaimStatusIn, request: Request,
               access: Access = Depends(require(Screen.claims, Level.full)), db: Session = Depends(get_db)) -> ClaimRow:
    cl = db.get(Claim, claim_id)
    if not cl:
        raise HTTPException(404, "Claim not found")
    if body.status not in NEXT[cl.status]:
        raise HTTPException(409, f"A {cl.status.replace('_', ' ')} claim can't move to {body.status.replace('_', ' ')}")
    old, cl.status = cl.status, body.status
    db.commit()
    audit.record(db, request, AuditAction.update, entity_type="claim", entity_id=cl.id,
                 detail={"from": old, "to": cl.status})
    return _row(db, cl, customer_masked(access.user))
