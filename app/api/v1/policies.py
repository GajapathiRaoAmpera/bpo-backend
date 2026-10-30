from fastapi import APIRouter, Depends, HTTPException, Request
from pydantic import BaseModel
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.core import audit
from app.core.db import get_db
from app.core.rbac import Access, Screen, require
from app.models import Customer, Policy
from app.models.enums import AuditAction
from app.schemas.customer import CustomerOut, PolicyOut, customer_fields

router = APIRouter(prefix="/policies", tags=["policies"])


class PolicyDetail(PolicyOut):
    customer: CustomerOut


@router.get("/{policy_no}", response_model=PolicyDetail)
def get_policy(policy_no: str, request: Request, access: Access = Depends(require(Screen.customers)),
               db: Session = Depends(get_db)) -> PolicyDetail:
    p = db.scalar(select(Policy).where(Policy.policy_no == policy_no.strip().upper()))
    if not p:
        raise HTTPException(404, "Policy not found")
    c = db.get(Customer, p.customer_id)
    audit.record(db, request, AuditAction.view_customer, entity_type="policy", entity_id=p.policy_no,
                 detail={"customer_code": c.customer_code, "masked": access.masked})
    return PolicyDetail(**PolicyOut.model_validate(p).model_dump(),
                        customer=CustomerOut(**customer_fields(c, access.masked)))
