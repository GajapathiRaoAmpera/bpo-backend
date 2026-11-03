from datetime import date, datetime

from pydantic import BaseModel

from app.models.enums import ClaimStatus


class ClaimRow(BaseModel):
    id: int
    claim_no: str
    policy_no: str
    plan_name: str
    customer_id: int
    customer_name: str
    phone: str
    call_id: int | None
    call_ref: str | None
    incident_date: date
    description: str
    status: ClaimStatus
    created_at: datetime
    updated_at: datetime


class ClaimStatusIn(BaseModel):
    status: ClaimStatus
