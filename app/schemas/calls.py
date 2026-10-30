from datetime import datetime

from pydantic import BaseModel

from app.models.enums import (
    CallDirection, CallMode, CallOutcome, CallStatus, EscalationStatus, FollowupKind, FollowupStatus, Priority, Sentiment,
)
from app.schemas.customer import CustomerDetail, CustomerOut, PolicyOut
from app.schemas.followups import FollowupRow
from app.schemas.voice import QAOut, TurnOut


class CallRow(BaseModel):
    id: int
    call_ref: str
    direction: CallDirection
    mode: CallMode
    status: CallStatus
    started_at: datetime
    duration_sec: int
    outcome: CallOutcome | None
    sentiment: Sentiment | None
    customer_id: int | None
    customer_name: str | None
    policy_no: str | None
    agent_name: str | None  # None = handled by the AI alone
    qa_score: int | None
    is_simulated: bool


class CallPage(BaseModel):
    items: list[CallRow]
    total: int
    page: int
    page_size: int


class CallEscalation(BaseModel):
    id: int
    reason: str
    priority: Priority
    status: EscalationStatus
    assigned_to_name: str | None
    created_at: datetime
    resolved_at: datetime | None
    resolution_note: str | None


class CallFollowup(BaseModel):
    id: int
    kind: FollowupKind
    status: FollowupStatus
    due_at: datetime
    reason: str
    message_text: str | None
    sent_at: datetime | None


class CallDetail(CallRow):
    ended_at: datetime | None
    verified: bool
    taken_over_at: datetime | None
    summary: str | None
    next_action: str | None
    customer: CustomerOut | None  # masked unless the role has full customer access
    policy: PolicyOut | None
    turns: list[TurnOut]
    qa: QAOut | None
    escalations: list[CallEscalation]
    followups: list[CallFollowup]


class Customer360(CustomerDetail):
    calls: list[CallRow]  # only the calls this role may see
    followups: list[FollowupRow]
