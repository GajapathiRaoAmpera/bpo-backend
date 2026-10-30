from datetime import datetime

from pydantic import BaseModel, Field

from app.models.enums import EscalationStatus, Language, Priority


class EscalationRow(BaseModel):
    id: int
    call_id: int | None
    call_ref: str | None
    customer_id: int
    customer_name: str
    phone: str  # masked for masked roles
    city: str
    preferred_language: Language
    policy_no: str | None
    plan_name: str | None
    premium_inr: int | None
    reason: str
    priority: Priority
    status: EscalationStatus
    assigned_to: int | None
    assigned_to_name: str | None
    created_at: datetime
    resolved_at: datetime | None
    resolution_note: str | None
    call_summary: str | None


class ResolveIn(BaseModel):
    note: str = Field(min_length=3, max_length=1000)
